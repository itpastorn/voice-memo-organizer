"""Delade byggstenar för steg b (flaggning av oklarheter).

Både percentil-generatorn (generera-corrections.py) och LLM-detektorn
(flagga-llm.py) läser samma Whisper-JSON och skriver samma corrections-format.
Det som skiljer är *hur* orden flaggas — vilka index som väljs. Allt runtomkring
(config, ordlista, kontextfönster, ankare, blockformat) bor här.

Se CLAUDE.md.
"""

from __future__ import annotations

import contextlib
import functools
import json
import re
import sys
import tomllib
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Konfiguration och sökvägar
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    with open(PROJECT_ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def normalize_stem(stem: str) -> str:
    """Gemener, bindestreck, ASCII, aldrig understreck (File Naming Convention
    i CLAUDE.md). Speglar transkribera.py så att JSON-namnet kan härledas."""
    decomposed = unicodedata.normalize("NFKD", stem)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    s = ascii_only.lower()
    out = []
    for ch in s:
        if ch in " _":
            out.append("-")
        elif ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "-":
            out.append(ch)
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def json_path_for(cfg: dict) -> Path:
    """JSON-filens sökväg härledd ur samma data.test_file som steg a."""
    audio_root = Path(cfg["data"]["root"])
    test_file = Path(cfg["data"]["test_file"])
    stem = normalize_stem(test_file.stem)
    return audio_root / test_file.parent / f"{stem}.json"


def aktuell_json(cfg: dict) -> tuple[Path, str]:
    """Vilken fil arbetar vi med? `granska/current.json` — den fil GUI:t visar —
    annars `data.test_file` i config.toml.

    Alla steg efter granskningen måste välja likadant. Gör de inte det granskar
    man fil X och bearbetar fil Y: apply skriver besluten i fel JSON, och steg c
    producerar en .md för fel memo.

    Returnerar (sökväg, varifrån valet kom); källan skrivs ut i skriptens
    rapporter så att ett felaktigt val syns direkt.
    """
    cur_file = PROJECT_ROOT / "granska" / "current.json"
    if cur_file.is_file():
        try:
            rel = json.loads(cur_file.read_text(encoding="utf-8")).get("transcript_json")
            if rel:
                p = Path(cfg["data"]["root"]) / rel
                # Runda 2 pekar current.json på basen (-bak2.json), men den
                # levande texten är alltid <stem>.json.
                for slut in ("-bak2.json", "-bak.json"):
                    if p.name.endswith(slut):
                        p = p.with_name(p.name[: -len(slut)] + ".json")
                        break
                if p.is_file():
                    return p, "granska/current.json (GUI:ts val)"
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    return json_path_for(cfg), "config.toml (data.test_file)"


def rel_to_root(cfg: dict, path: Path) -> str:
    """Sökväg relativt datamappens rot, med snedstreck — formatet granska/current.json
    använder. Ljudet ligger i temamappar (NAR-profetrorelsen/...), och GUI:t monterar
    roten som /data; bara filnamnet räcker alltså inte. Filer direkt i roten ger
    enbart filnamnet."""
    rel = path.resolve().relative_to(Path(cfg["data"]["root"]).resolve())
    return rel.as_posix()


# --------------------------------------------------------------------------- #
# Transkript, sidecars och filstatus
#
# Reglerna nedan fanns tidigare i två eller tre exemplar var (apply, aktuell,
# batch-flagga, och i PHP i valj.php). De MÅSTE ge samma svar överallt: väljer
# GUI:t en sidecar och apply en annan, granskar man fil X och skriver in
# besluten i fil Y.
# --------------------------------------------------------------------------- #

EXCLUDE_DIRS = {"test", "sammanfatta"}          # utanför projektet, se CLAUDE.md

# Härledda filer som ligger bland transkripten men inte ÄR transkript.
# Både -bak.json (nuvarande konvention) och .bak.json (legacy i
# andra-ideer-.../transcripts/json/) måste bort.
HARLEDDA_MARKORER = ("-corrections", "-bak.json", ".bak.json",
                     "-bak2.json", ".bak2.json")

# Hela den härledda familjen kring en stam, längsta suffix först så att
# '-corrections-2.json' matchas före '-corrections.json' och '-bak2.json' före
# '.json'. Ordningen ÄR logiken — en kortare träff först skulle ge fel stam.
#
# Listan bor här därför att två skript måste vara överens om vad som hör till en
# fil: synka-namn.py döper om familjen, och namnvakten avgör vad som ens är ett
# transkript. Går de isär lämnas filer kvar med gammal stam.
HARLEDDA_SUFFIX = (
    "-corrections-2.json", "-corrections.json", "-corrections.txt",
    "-bak2.json", "-bak.json", ".bak2.json", ".bak.json",
    "-korrigerad.srt", "-borttaget.txt",
    ".json", ".srt", ".txt", ".md",
)


def stam_av(namn: str) -> str | None:
    """Stammen ur ett härlett filnamn, eller None om det inte är en härledd fil.
    'zego-x-corrections.json' -> 'zego-x'."""
    for suffix in HARLEDDA_SUFFIX:
        if namn.endswith(suffix) and len(namn) > len(suffix):
            return namn[: -len(suffix)]
    return None


def ar_transkript(namn: str) -> bool:
    """Är filnamnet ett transkript, och inte en sidecar eller en backup?
    Speglar granska/valj.php:75-83 — listan i GUI:t och batchernas kö ska
    innehålla samma filer."""
    if not namn.endswith(".json"):
        return False
    return not any(m in namn for m in HARLEDDA_MARKORER)


def iter_transkript(root: Path):
    """Alla transkript under datamappens rot, utom test/ och sammanfatta/."""
    for p in root.rglob("*.json"):
        if not ar_transkript(p.name):
            continue
        if {d.lower() for d in p.relative_to(root).parts[:-1]} & EXCLUDE_DIRS:
            continue
        yield p


def valj_sidecar(json_path: Path) -> tuple[Path | None, int, str]:
    """Sidecarn för ett transkript: (sökväg, runda, varifrån den kom).

    Två regler, i ordning: senaste rundan vinner (runda 2 före runda 1), och
    arbetskopian i granska/state/ går före fröet i datamappen — /data monteras
    read-only, så GUI:ts beslut finns bara i arbetskopian.

    Rundan läses ur filnamnet, inte ur sidecarens `runda`-fält: en
    -corrections-2.json utan fältet är ändå runda 2, och basvalet i apply följer
    filnamnet. (None, 0, "") när ingen sidecar finns."""
    state_dir = PROJECT_ROOT / "granska" / "state"
    for namn, runda in ((f"{json_path.stem}-corrections-2.json", 2),
                        (f"{json_path.stem}-corrections.json", 1)):
        for kandidat, var in ((state_dir / namn, "arbetskopia i granska/state/"),
                              (json_path.with_name(namn), "frö i datamappen")):
            if kandidat.is_file():
                return kandidat, runda, var
    return None, 0, ""


def las_status(cfg: dict) -> dict[str, dict]:
    """granska/status.json rått: relativ posix-sökväg -> {status, note, satt}.
    Tom dict om filen saknas eller inte går att tolka."""
    p = PROJECT_ROOT / "granska" / "status.json"
    if not p.is_file():
        return {}
    try:
        alla = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return alla if isinstance(alla, dict) else {}


def status_for(cfg: dict, json_path: Path,
               alla: dict | None = None) -> dict | None:
    """Statusposten för ett transkript, eller None.

    Matchar på sökvägen relativt datamappens rot — formatet GUI:t skriver.
    Filnamnsfallbacken finns för poster skrivna innan temamapparna kom med, men
    exakt sökväg går först: två memon kan heta samma sak i olika temamappar, och
    då vore filnamnsmatchning fel fil.

    `alla` låter en batch läsa status.json en gång i stället för per fil."""
    if alla is None:
        alla = las_status(cfg)
    if not alla:
        return None
    try:
        rel = rel_to_root(cfg, json_path)
    except ValueError:
        rel = None
    if rel and rel in alla:
        return alla[rel]
    return next((v for r, v in alla.items()
                 if Path(r).name == json_path.name), None)


def fras_tackta(side: dict) -> set[int]:
    """Ordindex som ligger inom en frasersättning.

    Fraser utan span hoppas över: `migrera-corrections.py` lämnar stubbar för
    rader den inte kunde binda till ordindex, och apply filtrerar bort dem på
    samma villkor."""
    tackta: set[int] = set()
    for p in side.get("phrase_edits", []):
        if "span_start" in p and "span_end" in p:
            tackta.update(range(p["span_start"], p["span_end"] + 1))
    return tackta


def antal_ogranskade(side: dict) -> int:
    """Flaggor som ännu inte fått ett beslut. Saknat eller tomt `decision`
    räknas som ogranskat — strängare än granska/valj.php, som bara räknar
    decision === 'pending'. Skillnaden syns bara i handskrivna sidecars, och när
    en batch ska SKRIVA är det konservativa valet rätt.

    Ord som täcks av en fras räknas INTE, hur flaggan än ser ut: frasen ÄR
    beslutet. Apply hoppar över ordflaggan för varje index i ett frasspan, så en
    'kvar'-flagga där har ingen verkan — men den fick filen att se ogranskad ut
    och höll den utanför batchen i onödan."""
    tackta = fras_tackta(side)
    return sum(1 for f in side.get("flags", [])
               if f.get("global_index") not in tackta
               and (not (f.get("decision") or "").strip()
                    or f.get("decision") == "pending"))


def antal_operationer(side: dict) -> int:
    """Hur mycket sidecaren faktiskt skulle ändra. En sidecar utan operationer
    är inte värd en apply: den enda effekten vore en corrections_applied_at-
    stämpel, som får väljaren att visa 'applicerad' för en fil ingen granskat."""
    return (len(side.get("flags", []))
            + len(side.get("phrase_edits", []))
            + len(side.get("insertions", [])))


# --------------------------------------------------------------------------- #
# Namnvakt
#
# File Naming Convention i CLAUDE.md gäller allt vi PRODUCERAR. Ljudet är
# undantaget: 113 av 363 befintliga ljudfiler har versaler, och de döps inte om
# utan att Lars ber om det. transkribera.py normaliserar stammen på väg ut, så
# 'zego-Trump-akrist-1.m4a' ger 'zego-trump-akrist-1.json' och allt nedströms är
# redan rent. En spärr mot versaler skulle alltså stoppa en tredjedel av arkivet
# utan att avvärja ett enda fel.
#
# Det farliga är i stället normaliseringens KOLLISIONER: faller två ljudfiler
# ihop till samma stam pekar de på samma .json, och den ena inspelningen kommer
# aldrig in i pipelinen. Uppmätt i arkivet: zego-torpseminarium.aac (47:18) och
# zego-torpseminarium.m4a (54:20) är två OLIKA inspelningar med samma stam, och
# bara .m4a:ns 54 minuter finns transkriberade. Ingenting sa ifrån.
# --------------------------------------------------------------------------- #

class NamnFel(Exception):
    """Filnamnet gör vidare bearbetning otrygg — pipelinen skulle skriva över
    fel fil eller tappa en inspelning. Bär en åtgärdstext, som ApplyFel."""

    def __init__(self, meddelande: str, *, atgard: str = "") -> None:
        super().__init__(meddelande)
        self.atgard = atgard


LJUDANDELSER = {".m4a", ".mp3", ".aac", ".wav", ".ogg", ".m4b", ".flac", ".opus"}


def namnbrott(stem: str) -> list[str]:
    """Vilka av konventionens regler stammen bryter mot, i klartext. Tom lista
    betyder att normalize_stem() skulle lämna den orörd."""
    brott: list[str] = []
    if any(c.isupper() for c in stem):
        brott.append("versaler")
    if "_" in stem:
        brott.append("understreck")
    if " " in stem:
        brott.append("blanksteg")
    if any(ord(c) > 127 for c in stem):
        brott.append("icke-ASCII")
    ovrigt = sorted({c for c in stem if c.isascii() and not c.isalnum()
                     and c not in "-_ "})
    if ovrigt:
        brott.append("otillåtna tecken: " + " ".join(ovrigt))
    if "--" in stem or stem.strip("-") != stem:
        brott.append("bindestreck som ska kollapsas eller strippas")
    return brott


def ljud_per_stam(mapp: Path) -> dict[str, list[Path]]:
    """Ljudfilerna i en mapp grupperade på normaliserad stam — alltså på den
    .json var och en av dem skulle skriva."""
    per_stam: dict[str, list[Path]] = {}
    try:
        innehall = sorted(mapp.iterdir())
    except OSError:
        return per_stam
    for p in innehall:
        if p.is_file() and p.suffix.lower() in LJUDANDELSER:
            per_stam.setdefault(normalize_stem(p.stem), []).append(p)
    return per_stam


@functools.lru_cache(maxsize=8)
def _ljudindex(root: str) -> dict[str, tuple[str, ...]]:
    """Hela arkivets ljudfiler grupperade på normaliserad stam, som sökvägar
    relativt roten.

    Cachad: en batch över 363 filer skulle annars gå igenom trädet en gång per
    fil. Ljudmängden ändras inte under en körning — vi skapar aldrig ljud — så
    cachen kan inte bli inaktuell mitt i ett jobb. Anropa nollstall_namnindex()
    om något ändå döps om i samma process."""
    rot = Path(root)
    per_stam: dict[str, list[str]] = {}
    for f in rot.rglob("*"):
        if f.suffix.lower() not in LJUDANDELSER or not f.is_file():
            continue
        rel = f.relative_to(rot)
        if {d.lower() for d in rel.parts[:-1]} & EXCLUDE_DIRS:
            continue
        per_stam.setdefault(normalize_stem(f.stem), []).append(rel.as_posix())
    return {stam: tuple(sorted(v)) for stam, v in per_stam.items()}


def nollstall_namnindex() -> None:
    """Glöm det cachade stamindexet (efter en omdöpning i samma process)."""
    _ljudindex.cache_clear()


def vakta_ljud(cfg: dict, audio_path: Path) -> list[str]:
    """Steg a. Kastar NamnFel när ljudfilen inte kan transkriberas tryggt.
    Returnerar varningar som anroparen bör logga.

    Kollisionen är hård med flit: två ljudfiler som normaliserar till samma stam
    skriver samma .json, och den som körs sist raderar den förstas transkript
    utan att någonting säger ifrån."""
    audio_path = Path(audio_path)
    stem = normalize_stem(audio_path.stem)
    if not stem:
        raise NamnFel(
            f"{audio_path.name}: filnamnet ger ingen giltig stam",
            atgard="Döp om ljudfilen så att den har minst ett tecken a-z eller 0-9.")

    syskon = [p for p in ljud_per_stam(audio_path.parent).get(stem, [])
              if p.name != audio_path.name]
    if syskon:
        namn = ", ".join(sorted(p.name for p in [audio_path] + syskon))
        raise NamnFel(
            f"{audio_path.name}: {len(syskon) + 1} ljudfiler i mappen ger samma "
            f"stam {stem!r} och därmed samma {stem}.json — {namn}",
            atgard="Döp om alla utom en så att stammarna skiljer sig åt. "
                   "Kör namnvakt.py för att se hela arkivet.")

    # Samma stam i en ANNAN temamapp är lika illa, fast senare: granska/state/
    # är platt, så när båda transkriberats delar de arbetskopia och den ena
    # granskningens beslut hamnar i den andras sidecar. Fånga det här, innan
    # någon av dem kostat CPU-tid. (Uppmätt: zego-predikan-2 finns i både
    # andra-ideer-teologi-substack-webb/ och ideer-predikoutkast-....)
    try:
        rot = Path(cfg["data"]["root"]).resolve()
        egen_rel = audio_path.resolve().relative_to(rot).as_posix()
        annanstans = [r for r in _ljudindex(str(rot)).get(stem, ())
                      if r != egen_rel and Path(r).parent != Path(egen_rel).parent]
    except (OSError, ValueError, KeyError):
        annanstans = []
    if annanstans:
        raise NamnFel(
            f"{audio_path.name}: stammen {stem!r} används i fler än en temamapp — "
            f"granska/state/ är platt, så deras sidecars skulle skriva över "
            f"varandra: {', '.join([egen_rel] + annanstans)}",
            atgard="Döp om alla utom en så att stammarna blir unika i hela "
                   "arkivet, inte bara i mappen.")

    varningar: list[str] = []
    brott = namnbrott(audio_path.stem)
    if brott:
        varningar.append(
            f"ljudfilens namn följer inte konventionen ({', '.join(brott)}); "
            f"utdata får ändå det normaliserade namnet {stem!r}")
    return varningar


def vakta_transkript(cfg: dict, json_path: Path) -> list[str]:
    """Steg b och framåt. Kastar NamnFel när transkriptet inte är tryggt att
    arbeta vidare på. Returnerar varningar som anroparen bör skriva ut.

    Tre hårda fel:

    1. Transkriptets EGEN stam är inte normaliserad. En .json är något vi
       producerar, så en avvikande stam betyder att filen inte kom ur den här
       pipelinen. json_path_for() kan aldrig härleda fram till den, och dess
       härledda namn (-bak, -corrections, .md) skulle blandas ihop med den
       normaliserade grannens.
    2. Två ljudfiler i mappen faller ihop på transkriptets stam. Då är det inte
       längre känt vilken inspelning texten kommer ur, och en omtranskribering
       kan tyst byta ut den mot den andra.
    3. Två transkript i arkivet delar stam. granska/state/ är platt, så deras
       arbetskopior skriver över varandra: man granskar den ena filen och
       besluten landar i den andras sidecar.
    """
    json_path = Path(json_path)
    stem = json_path.stem

    brott = namnbrott(stem)
    if brott:
        raise NamnFel(
            f"{json_path.name}: transkriptets namn bryter mot konventionen "
            f"({', '.join(brott)})",
            atgard="Transkript är härledda filer. Transkribera om ljudet, eller "
                   f"döp om transkriptet och dess härledda filer till "
                   f"{normalize_stem(stem)!r}.")

    syskon = ljud_per_stam(json_path.parent).get(stem, [])
    if len(syskon) > 1:
        namn = ", ".join(sorted(p.name for p in syskon))
        raise NamnFel(
            f"{json_path.name}: {len(syskon)} ljudfiler i mappen ger stammen "
            f"{stem!r} — okänt vilken av dem texten kommer ur: {namn}",
            atgard="Döp om alla utom en så att stammarna skiljer sig åt, och "
                   "transkribera de övriga separat.")

    root = Path(cfg["data"]["root"])
    try:
        egen = json_path.resolve()
        dubbletter = [p for p in iter_transkript(root)
                      if p.stem == stem and p.resolve() != egen]
    except (OSError, ValueError):
        dubbletter = []
    if dubbletter:
        andra = ", ".join(sorted(p.relative_to(root).as_posix() for p in dubbletter))
        raise NamnFel(
            f"{json_path.name}: stammen {stem!r} finns i fler än en temamapp — "
            f"granska/state/ är platt, så sidecars skriver över varandra: {andra}",
            atgard="Döp om ljudet i en av mapparna och transkribera om, så att "
                   "stammarna blir unika i hela arkivet.")

    varningar: list[str] = []
    if not syskon:
        varningar.append("ingen ljudfil med den stammen i mappen — har ljudet "
                         "flyttats eller döpts om?")
    elif namnbrott(syskon[0].stem):
        varningar.append(f"ljudfilen heter {syskon[0].name!r} och följer inte "
                         "konventionen; transkriptet är rätt namngivet")
    return varningar


def vakta_eller_avsluta(cfg: dict, json_path: Path) -> None:
    """Namnvakt för skript som bearbetar en fil och avslutar.

    Skriver varningarna till stderr och avbryter med exit-kod 2 vid NamnFel —
    egen kod, så att ett namnproblem går att skilja från ett vanligt
    misslyckande (1) i ett skalskript."""
    import sys
    try:
        for v in vakta_transkript(cfg, json_path):
            print(f"VARNING: {v}", file=sys.stderr)
    except NamnFel as e:
        print(f"FEL: {e}", file=sys.stderr)
        if e.atgard:
            print(f"     {e.atgard}", file=sys.stderr)
        raise SystemExit(2)


# --------------------------------------------------------------------------- #
# Vaken dator under långa körningar (issue #9)
#
# Modernt vänteläge (S0) stoppar inte bakgrundsjobb, det stryper dem. En
# nattkörning dör alltså inte — den kryper, utan felutskrift, och loggen ser
# ut som om allt går. Uppmätt natten till 2026-07-31: vänteläge 18 minuter
# efter start, åtta timmar innan maskinen kom ur det, och en transkribering på
# ~12 minuter var inte klar på 8,5 timmar.
#
# Begäran hör hemma i koden och inte i ett energischema någon ska minnas att
# ändra — samma skäl som Portabilitet i CLAUDE.md anger för device och
# compute_type. Skärmen lämnas i fred (inget ES_DISPLAY_REQUIRED): jobbet
# behöver processorn vaken, inte panelen tänd.
# --------------------------------------------------------------------------- #

ES_CONTINUOUS = 0x80000000        # gäller tills den uttryckligen släpps
ES_SYSTEM_REQUIRED = 0x00000001   # systemet får inte somna av tomgång


@contextlib.contextmanager
def vaken(logger=None, *, skal: str = "lång körning"):
    """Håll systemet vaket så länge blocket körs. Släpper alltid efteråt.

    No-op på allt utom Windows, och på Windows om anropet inte går igenom.
    Att avbryta för att en energibegäran nekades vore fel avvägning: jobbet är
    fortfarande värt att köra, det riskerar bara att strypas. Därför en
    varning och inget mer — men *aldrig* tystnad, för strypningen syns inte.
    """
    def saga(niva: str, msg: str) -> None:
        if logger is not None:
            getattr(logger, niva)(msg)
        else:
            print(msg, file=sys.stderr if niva != "info" else sys.stdout)

    kernel32 = None
    if sys.platform != "win32":
        saga("info", f"Vänteläge: ingen begäran ({sys.platform} — bara Windows "
                     f"har SetThreadExecutionState).")
    else:
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
            kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
            if kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                saga("info", f"Vänteläge blockerat under {skal} "
                             f"(ES_SYSTEM_REQUIRED). Kontrollera med "
                             f"'powercfg /requests' i ett administratörsfönster.")
            else:
                kernel32 = None
                saga("warning", "VARNING: energibegäran nekades. Datorn kan gå i "
                                "vänteläge och strypa körningen utan att säga till.")
        except Exception as e:                      # OSError, AttributeError, ...
            kernel32 = None
            saga("warning", f"VARNING: kunde inte begära vaken dator ({e}). "
                            f"Körningen kan strypas av vänteläget.")
    try:
        yield kernel32 is not None
    finally:
        if kernel32 is not None:
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            saga("info", "Vänteläge släppt — datorn får somna igen.")


# --------------------------------------------------------------------------- #
# Ordnormalisering
#
# Whisper-tokens bär sina egna mellanslag och sin interpunktion ('  innehåll.'),
# medan allt som jämför ord — verifiering i runda 2, propagering av rättelser —
# vill åt själva ordet. Att jämföra råa tokens ger falska avvikelser.
# --------------------------------------------------------------------------- #

SKILJETECKEN = '.,!?;:"\'”“’‘…—–()[]'


def karna(s: str) -> str:
    """Ordet utan omgivande blanksteg och skiljetecken."""
    return s.strip().strip(SKILJETECKEN)


def normalisera_ord(w: str) -> str:
    """Jämförbar nyckel: gemener, ASCII-vikning (å/ä→a, ö→o), bara a-z0-9.
    Whisper stavar samma namn med och utan accent, och versaler säger inget om
    ordet — 'Kerps' och 'kerps.' ska ge samma nyckel."""
    w = unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in w.lower() if c.isalnum() and c.isascii())


# Svenska böjningsändelser, längst först så 'ens' inte kapas som 's'.
_ANDELSER = ("ens", "ers", "arna", "erna", "orna", "ar", "er", "en", "et", "s")


def grundform(w: str) -> str:
    """Ordet utan svensk böjningsändelse. Grovt med flit: 'Kirk' och 'Kirks'
    ska falla samman, och en felaktig kapning kostar bara att två ord jämförs
    på sin gemensamma stam. Kapar aldrig så att färre än tre tecken blir kvar."""
    for slut in _ANDELSER:
        if len(w) - len(slut) >= 3 and w.endswith(slut):
            return w[: -len(slut)]
    return w


def levenshtein(a: str, b: str) -> int:
    """Redigeringsavstånd. Egen implementation — projektet har inga
    fuzzy-beroenden, och detta är tolv rader."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    rad = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        ny = [i]
        for j, cb in enumerate(b, 1):
            ny.append(min(rad[j] + 1, ny[j - 1] + 1, rad[j - 1] + (ca != cb)))
        rad = ny
    return rad[-1]


def likhet(a: str, b: str) -> float:
    """1,0 = identiska. Normaliserat mot den längsta strängen, så ett fel i ett
    kort ord väger tyngre än ett fel i ett långt."""
    langst = max(len(a), len(b))
    return 1 - levenshtein(a, b) / langst if langst else 0.0


ORDLISTA_DIR = PROJECT_ROOT / "ordlista"


def ar_inkorg(cfg: dict, mappnamn: str | None) -> bool:
    """Är mappen inkorgen? Jämförs skiftlägesokänsligt: på Windows är
    'Incoming' och 'incoming' samma mapp, och en inkorg som felaktigt tas för
    ett tema ger tyst fel ordlista."""
    inkorg = cfg["data"].get("incoming")
    return bool(inkorg and mappnamn and mappnamn.lower() == inkorg.lower())


def temamapp_for(cfg: dict, path: Path) -> str | None:
    """Temamappens namn för en fil under datamappens rot, eller None när temat
    är okänt: filen ligger direkt i roten, eller i inkorgen (`data.incoming`).
    Bara den första nivån räknas; djupare undermappar tillhör sin temamapp.

    Inkorgen MÅSTE ge None. Fram till 2026-09-16 gav en fil i incoming/ temat
    "incoming", och load_ordlista("incoming") letade efter ordlista/incoming.txt,
    hittade den inte och gav bara basen — 8 termer i stället för 132. Tyst, i
    alla fyra skript som flaggar."""
    try:
        rel = Path(path).resolve().relative_to(Path(cfg["data"]["root"]).resolve())
    except ValueError:
        return None
    if len(rel.parts) <= 1 or ar_inkorg(cfg, rel.parts[0]):
        return None
    return rel.parts[0]


def _las_termfil(path: Path) -> list[str]:
    """Termer ur en ordlistefil. Rader som börjar med # hoppas över, och en
    '#'-kommentar sist på en rad skalas bort — filerna dokumenterar observerade
    förvanskningar ('Shawn Bolz  # (Bolts, Boltz)'), men bara den rättstavade
    termen ska vidare."""
    if not path.is_file():
        return []
    return [r for r in (rad.split("#", 1)[0].strip()
                        for rad in path.read_text(encoding="utf-8").splitlines()) if r]


def load_ordlista(tema: str | None = None, *, allt: bool = False,
                  bas: str = "gemensam") -> list[str]:
    """Ordlista: den gemensamma basen först, sedan temamappens egen fil.

    tema=None ger bara basen (t.ex. en fil i inkorgen). allt=True läser alla
    mappfiler — meningsfullt för LLM-detektorn i steg b, som saknar längdgräns
    och hellre ser för mycket än för lite när temat är okänt.

    Dubbletter tas bort men ordningen behålls: basen ligger först, så budget-
    vakten i initial_prompt() kapar mappens termer och aldrig basens.
    """
    filer = [ORDLISTA_DIR / f"{bas}.txt"]
    if allt:
        filer += sorted(p for p in ORDLISTA_DIR.glob("*.txt") if p.stem != bas)
    elif tema:
        filer.append(ORDLISTA_DIR / f"{tema}.txt")

    termer: list[str] = []
    sedda: set[str] = set()
    for f in filer:
        for t in _las_termfil(f):
            if t not in sedda:
                sedda.add(t)
                termer.append(t)
    return termer


# --------------------------------------------------------------------------- #
# initial_prompt till Whisper (steg a)
# --------------------------------------------------------------------------- #

def _whisper_tokenizer():
    """Modellens EGEN tokenizer ur models/, om den finns. Teckenuppskattning
    slår fel med ~10 % (mätt 442 mot verkliga 484 tokens), och taket är hårt —
    därför exakt räkning när det går."""
    try:
        from tokenizers import Tokenizer
    except ImportError:
        return None
    for p in (PROJECT_ROOT / "models").rglob("tokenizer.json"):
        try:
            return Tokenizer.from_file(str(p))
        except Exception:
            continue
    return None


def rakna_tokens(text: str) -> tuple[int, bool]:
    """(antal tokens, exakt). Faller tillbaka på ~3,3 tecken/token när modellen
    inte är nedladdad — anropare bör varna när exakt=False."""
    tok = _whisper_tokenizer()
    if tok is None:
        return round(len(text) / 3.3), False
    return len(tok.encode(text).ids), True


def bygg_ordlista_prompt(termer: list[str], budget: int) -> tuple[str, list[str], int, bool]:
    """Bygg ordlisteprompten ur termlistan.

    Kommaseparerat, inte radbrutet: samma innehåll mätte 484 mot 541 tokens.
    Termer kapas BAKIFRÅN tills prompten ryms i budgeten, så den gemensamma
    basen (som ligger först) alltid överlever.

    Returnerar (prompt, kapade termer, tokenantal, exakt räkning).
    """
    if not termer:
        return "", [], 0, True
    behallna = list(termer)
    kapade: list[str] = []
    while behallna:
        text = ", ".join(behallna)
        n, exakt = rakna_tokens(text)
        if n <= budget:
            return text, kapade, n, exakt
        kapade.insert(0, behallna.pop())
    return "", list(termer), 0, True


def ordlista_prompt_for(cfg: dict, audio_path: Path, logger=None) -> str | None:
    """Färdig ordlisteprompt för en ljudfil, eller None när steget är avstängt.

    VIKTIGT: strängen ska skickas som faster-whispers **`hotwords`**, inte som
    `initial_prompt`. `initial_prompt` läggs i `all_tokens`, och eftersom
    `condition_on_previous_text = false` (KBLabs rekommendation) nollställs den
    vid varje nytt 30-sekundersfönster — den når alltså bara filens första
    halvminut. Uppmätt: en körning med initial_prompt gav identiskt resultat på
    alla måltermer. `hotwords` injiceras i varje fönsters prompt.

    Härleder temat ur ljudfilens mapp, bygger prompten och loggar vad som gick
    in — och framför allt vad som kapades. En tyst trunkering vore ett osynligt
    fel: termer skulle sluta verka utan att någon märkte det.
    """
    tcfg = cfg.get("transcription", {})
    if not tcfg.get("ordlista_prompt", False):
        return None

    tema = temamapp_for(cfg, audio_path)
    termer = load_ordlista(tema, bas=tcfg.get("prompt_bas", "gemensam"))
    budget = int(tcfg.get("ordlista_prompt_max_tokens", 223))
    prompt, kapade, n, exakt = bygg_ordlista_prompt(termer, budget)

    if logger is not None:
        ungefar = "" if exakt else " (uppskattat — tokenizer saknas)"
        logger.info("ordlisteprompt: %s (%d termer, %d/%d tokens%s)",
                    tema or "inkorgen — bara basen", len(termer) - len(kapade),
                    n, budget, ungefar)
        if kapade:
            logger.warning("    KAPADE %d term(er) som inte fick plats: %s",
                           len(kapade), ", ".join(kapade))
    return prompt or None


def load_dotenv() -> None:
    """Minimal .env-läsare: KEY=VALUE-rader läggs i miljön om de inte redan finns.
    Undviker ett beroende till python-dotenv för en handfull rader."""
    import os
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for rad in path.read_text(encoding="utf-8").splitlines():
        rad = rad.strip()
        if not rad or rad.startswith("#") or "=" not in rad:
            continue
        key, _, val = rad.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


# --------------------------------------------------------------------------- #
# Ordlista ur JSON
# --------------------------------------------------------------------------- #

def flatten_words(segments: list[dict]) -> list[dict]:
    """Platta ut alla ord till en enda lista i ordning. Varje ord får med sig
    en säker starttid (faller tillbaka på segmentets start om ordets saknas)."""
    words: list[dict] = []
    for seg in segments:
        seg_start = seg.get("start")
        for w in seg.get("words") or []:
            start = w.get("start")
            words.append({
                "word": w.get("word", ""),
                "start": start if start is not None else seg_start,
                "probability": w.get("probability"),
            })
    return words


# --------------------------------------------------------------------------- #
# Whisper-specialtoken i texten
#
# Modellens styrtoken kan läcka ut som vanlig text: '<|nospeech|>' står kvar i
# segmentet och hamnar i .json, .txt och .srt. Uppmätt 2026-09-21: 27 av 293
# transkript, 107 segment, båda modellerna.
#
# Den går INTE att hitta med en sökning i enskilda ord. Whisper delar den över
# flera ordtokens — '<', '|nospeech', '|', '>' — så varje ord för sig ser
# oskyldigt ut. Därför sätts orden ihop per segment innan mönstret söks, och
# träffen mappas tillbaka till de ordindex den täcker.
#
# Bara token som omöjligt kan vara vanliga ord listas. 'translate' och
# 'transcribe' är också styrtoken men förekommer i engelsk text, och en vakt som
# larmar på riktiga ord blir avstängd.
# --------------------------------------------------------------------------- #

SPECIALTOKEN_RE = re.compile(
    r"<\s*\|\s*(nospeech|startoftranscript|startoflm|startofprev|endoftext|notimestamps)"
    r"\s*\|\s*>", re.IGNORECASE)


def specialtoken_traffar(segments: list[dict]) -> list[dict]:
    """Specialtoken i texten, en post per träff.

    Varje post bär segmentets index, de GLOBALA ordindex träffen täcker (samma
    numrering som flatten_words), själva tokensträngen, starttiden och om
    tokenen utgör hela segmentet.

    `helt_segment` avgör hur farlig träffen är att laga: är segmentet bara token
    kan det tas bort rakt av, men sitter tokenen mitt i tal sitter den ihop med
    riktiga ord ('<|nospeech|>ologi,') och en mekanisk strykning skulle ta text
    med sig.
    """
    traffar: list[dict] = []
    gi = 0
    for si, seg in enumerate(segments):
        ord_ = seg.get("words") or []
        text = ""
        spann: list[tuple[int, int]] = []
        for w in ord_:
            o = w.get("word", "")
            spann.append((len(text), len(text) + len(o)))
            text += o
        # Segment utan ordlista: sök i segmentets egen text, men då finns inga
        # ordindex att peka ut.
        sok = text if ord_ else (seg.get("text") or "")
        for m in SPECIALTOKEN_RE.finditer(sok):
            idx = [gi + j for j, (a, b) in enumerate(spann)
                   if a < m.end() and b > m.start()]
            traffar.append({
                "segment": si,
                "ord": idx,
                "token": m.group(0),
                "start": seg.get("start"),
                "helt_segment": bool(ord_) and len(idx) == len(ord_),
            })
        gi += len(ord_)
    return traffar


# --------------------------------------------------------------------------- #
# Är transkriptionen användbar över huvud taget?
#
# Whisper kan fastna i en upprepningsloop och producera flytande nonsens:
# 'Jag tackar för mig. Jag tackar för mig själv. Jag tackar för mig.' i elva
# minuter. Det ser inte ut som ett fel — texten är välformad svenska — och
# ingenting nedströms fångar det. Detektorn flaggar enskilda ord, inte att hela
# filen saknar innehåll.
#
# Trösklarna är satta i tomrum i mätningen (2026-09-21, 282 filer över en minut),
# inte nära datan:
#
#   andel unika segment   loopfilerna 0,11–0,50   riktiga filer >= 0,92   p5 = 0,98
#   ord per minut         loopfilerna 1,6–18,6    nästa riktiga 39        p5 = 76,6
#
# Whispers egna mått dög inte. `compression_ratio` räknas per segment och ser
# därför inte en loop som går ÖVER segmentgränser (max i arkivet: 2,23, under
# Whispers egen larmgräns 2,4). `no_speech_prob` är 0,00 i hela arkivet.
# --------------------------------------------------------------------------- #

MIN_SEKUNDER = 60.0          # kortare filer ger för brusiga mått
MIN_ORD_PER_MINUT = 30.0
MIN_UNIKA_SEGMENT = 0.80
MIN_TACKNING = 0.85


def transkriptionsmatt(data: dict) -> dict | None:
    """Mått på om en transkription är användbar, eller None när filen är för
    kort för att bedöma.

    `ord_per_minut` räknar bort specialtoken — annars får en fil som är full av
    '<|nospeech|>' ett respektabelt ordtempo på ren skräp."""
    dur = data.get("duration")
    segment = data.get("segments") or []
    if not dur or dur < MIN_SEKUNDER or not segment:
        return None
    ord_antal = sum(len(s.get("words") or []) for s in segment)
    token = sum(len(t["ord"]) for t in specialtoken_traffar(segment))
    texter = [(s.get("text") or "").strip() for s in segment]
    return {
        "ord_per_minut": (ord_antal - token) / (dur / 60),
        "unika_segment": len(set(texter)) / len(texter),
        "tackning": max((s.get("end") or 0) for s in segment) / dur,
        "ord": ord_antal,
        "minuter": dur / 60,
    }


def transkriptionsproblem(matt: dict | None) -> list[str]:
    """Vad som är fel med transkriptionen, i klartext. Tom lista = inget fel.

    Loopen först: den förklarar nästan alltid också varför ordtempot är lågt,
    och den är det säkra omdömet. Lågt ordtempo utan loop kan vara en människa
    som tänker länge mellan meningarna — det kräver en lyssning, inte ett
    beslut i ett skript."""
    if not matt:
        return []
    problem = []
    if matt["unika_segment"] < MIN_UNIKA_SEGMENT:
        problem.append(
            f"upprepningsloop: bara {matt['unika_segment']:.0%} av segmenten är unika")
    if matt["ord_per_minut"] < MIN_ORD_PER_MINUT:
        problem.append(
            f"ovanligt få ord: {matt['ord_per_minut']:.0f} ord/minut "
            f"(arkivets median är 96)")
    if matt["tackning"] < MIN_TACKNING:
        problem.append(
            f"avkortad: texten slutar {matt['tackning']:.0%} in i ljudet")
    return problem


# --------------------------------------------------------------------------- #
# Kluster och kontextfönster
# --------------------------------------------------------------------------- #

def cluster(flagged: list[int], merge_gap: int) -> list[list[int]]:
    """Slå ihop flaggade ord som ligger nära varandra till gemensamma block."""
    clusters: list[list[int]] = []
    for i in sorted(flagged):
        if clusters and i - clusters[-1][-1] <= merge_gap:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    return clusters


def context_window(words: list[dict], cluster_idx: list[int],
                   min_words: int, max_words: int) -> tuple[int, int]:
    """Ett fönster på min–max ord som alltid rymmer hela klustret, centrerat runt
    det. Returnerar (start, slut) som index i words (slut exklusivt)."""
    first, last = cluster_idx[0], cluster_idx[-1]
    center = (first + last) // 2
    half = max_words // 2
    start = max(0, center - half)
    end = min(len(words), start + max_words)
    start = max(0, end - max_words)
    start = min(start, first)
    end = max(end, last + 1)
    if end - start < min_words:
        end = min(len(words), start + min_words)
        start = max(0, end - min_words)
    return start, end


def context_text(words: list[dict], start: int, end: int) -> str:
    """Kontextsträngen: orden råa (Whisper-tokens bär redan sina mellanslag)."""
    return "".join(w["word"] for w in words[start:end]).strip()


# --------------------------------------------------------------------------- #
# Utskrift av corrections-filen
# --------------------------------------------------------------------------- #

def format_anchor(start) -> str:
    return f"@{start:.2f}" if start is not None else "@?"


def build_file(words: list[dict], clusters: list[list[int]],
               min_words: int, max_words: int,
               annotations: dict[int, str] | None = None) -> str:
    """Bygg hela corrections-filens text i Lars format.

    annotations: valfri karta global ordindex -> kommentar (t.ex. LLM:ns
    föreslagna rättelse och skäl). Läggs efter ankaret som '# ...' så att den
    syns men inte tas för Lars beslut; apply-steget ignorerar allt efter '#'.
    """
    annotations = annotations or {}
    all_flagged = [i for c in clusters for i in c]
    left_width = max(
        (len(words[i]["word"].strip()) + 1 for i in all_flagged),
        default=0,
    )

    blocks: list[str] = []
    for c in clusters:
        start, end = context_window(words, c, min_words, max_words)
        lines = [f'"context": "{context_text(words, start, end)}"']
        for i in c:
            w = words[i]
            left = f'{w["word"].strip()}='
            row = f"{left.ljust(left_width)}  {format_anchor(w['start'])}"
            if i in annotations:
                row += f"   # {annotations[i]}"
            lines.append(row)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


# --------------------------------------------------------------------------- #
# Härledda format (.srt, .txt) — delade av transkribera.py (steg a) och
# applicera-corrections.py (steg 2). JSON är sanningskällan; dessa härleds ur den.
# --------------------------------------------------------------------------- #

def format_timestamp(seconds: float) -> str:
    """SRT-tid: HH:MM:SS,mmm."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[dict], path) -> None:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_txt(segments: list[dict], path) -> None:
    # En rad per segment. JSON är sanningskällan; detta är läsbar bekvämlighet.
    lines = [seg["text"].strip() for seg in segments]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
