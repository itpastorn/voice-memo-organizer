"""Låt de härledda filerna följa med när ljudet döpts om eller flyttats.

Ljudet döps om för hand — det är Lars filer och hans taxonomi. Men `.json`,
`.srt`, `.txt`, `.md`, `-bak*.json`, `-corrections*` och `-borttaget.txt` bär
stammen i sitt namn OCH i sitt innehåll, och de följer inte med av sig själv.
Namnvakten ser resultatet ("ingen ljudfil med den stammen i mappen") men kan
bara larma. Det här skriptet åtgärdar.

    synka-namn.py            visar vad som skulle göras (STANDARD — skriver inget)
    synka-namn.py --kor      genomför
    synka-namn.py --bara-falt   hoppa över omdöpningar, laga bara innehållsfält

Två fel lagas, båda tysta:

1. **Föräldralösa grupper.** Hela familjen kring en stam döps om (och flyttas,
   när ljudet bytt temamapp), inklusive arbetskopian i granska/state/.
2. **Innehållsfält som pekar fel.** `audio_file`, `transcript_json`,
   `base_json`, och `.md`:ns frontmatter. Skiftlägesexakt: Windows döljer
   `zego-Kirk-debatt.m4a` mot `zego-kirk-debatt.m4a`, men GUI:t kör i Docker på
   Linux där valj.php läser fältet rakt av och ljudknappen dör.

**Parningen bevisas, den gissas inte.** En kandidat godtas bara när ljudlängden
stämmer med transkriptet. Vid flera eller inga kandidater görs ingenting och
fallet rapporteras — hellre en fil kvar att reda ut för hand än en `.md` som
hamnat på fel memo.

Segment och ord rörs aldrig. Bara pekarfält skrivs om.

Se CLAUDE.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import korrigeringar as k

# Ljudlängd mot JSON:ens duration. Whisper skriver containerns längd, så
# avvikelsen är hundradelar när det är rätt fil och minuter när det är fel.
TOLERANS_DURATION = 1.0

# Äldre transkript (word_segments/text-schemat) saknar duration. Då används
# sista segmentets sluttid, som alltid ligger strax FÖRE ljudslutet — tystnad i
# slutet räknas inte som tal. 60 s rymmer en lång eftersnack-tystnad utan att
# släppa igenom en helt annan inspelning.
TOLERANS_SLUT = 60.0
MIN_NAMNLIKHET = 0.7

STATE_DIR = k.PROJECT_ROOT / "granska" / "state"


class SynkFel(Exception):
    """Vägran: åtgärden skulle förstöra något. Bär en åtgärdstext."""

    def __init__(self, meddelande: str, *, atgard: str = "") -> None:
        super().__init__(meddelande)
        self.atgard = atgard


@dataclass(slots=True)
class Omdopning:
    gammal_mapp: Path
    gammal_stam: str
    ny_mapp: Path
    ny_stam: str
    filer: list[Path]
    bevis: str
    state_filer: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class Faltfix:
    fil: Path                 # sökvägen EFTER en eventuell omdöpning
    falt: str
    gammalt: str
    nytt: str


# --------------------------------------------------------------------------- #
# Ljudlängd
# --------------------------------------------------------------------------- #

def ljudlangd(path: Path) -> float | None:
    """Ljudfilens längd i sekunder via ffprobe, eller None när den inte går att
    läsa. ffmpeg finns i PATH (se CLAUDE.md, Hårdvara)."""
    try:
        ut = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return float(ut.stdout.strip())
    except ValueError:
        return None


def langdindex(filer: list[Path]) -> dict[Path, float]:
    """Ljudlängd för många filer på en gång.

    ffprobe är en process per fil, ~100 ms. Naivt (en probe per kandidat och
    föräldralös grupp) blev det tusentals anrop och minuter av väntan; här
    probas varje fil EN gång, och trådpoolen döljer processtarterna eftersom
    arbetet är I/O och inte CPU."""
    from concurrent.futures import ThreadPoolExecutor

    if not filer:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        langder = list(pool.map(ljudlangd, filer))
    return {p: d for p, d in zip(filer, langder) if d is not None}


def transkriptets_langd(data: dict) -> tuple[float | None, float | None]:
    """(duration, sista segmentets sluttid). Båda kan saknas."""
    dur = data.get("duration")
    segment = data.get("segments") or []
    slut = segment[-1].get("end") if segment else None
    return (float(dur) if isinstance(dur, (int, float)) else None,
            float(slut) if isinstance(slut, (int, float)) else None)


# --------------------------------------------------------------------------- #
# Kartläggning
# --------------------------------------------------------------------------- #

def harledda_grupper(root: Path) -> dict[tuple[Path, str], list[Path]]:
    """Härledda filer grupperade på (mapp, stam)."""
    grupper: dict[tuple[Path, str], list[Path]] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() in k.LJUDANDELSER:
            continue
        rel = p.relative_to(root)
        if {d.lower() for d in rel.parts[:-1]} & k.EXCLUDE_DIRS:
            continue
        stam = k.stam_av(p.name)
        if stam:
            grupper.setdefault((p.parent, stam), []).append(p)
    return grupper


def ljud_utan_transkript(root: Path) -> list[Path]:
    """Ljudfiler som ännu inte har en .json bredvid sig — kandidaterna att para
    en föräldralös grupp med."""
    ut = []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in k.LJUDANDELSER or not p.is_file():
            continue
        if {d.lower() for d in p.relative_to(root).parts[:-1]} & k.EXCLUDE_DIRS:
            continue
        if not (p.parent / f"{p.stem}.json").is_file():
            ut.append(p)
    return ut


def bevisa(data: dict, ljud: Path, gammal_stam: str,
           langd: float | None) -> str | None:
    """Är ljudfilen samma inspelning som transkriptet? Returnerar bevistexten,
    eller None när det inte går att styrka.

    Längden är det starka beviset — namn kan ha ändrats hur som helst, men en
    22-minutersinspelning är 22 minuter lång. Namnlikhet används bara som extra
    krav i det svaga fallet, aldrig som ensamt skäl."""
    if langd is None:
        return None
    dur, slut = transkriptets_langd(data)

    if dur is not None:
        if abs(dur - langd) <= TOLERANS_DURATION:
            return f"duration {dur:.1f} s mot ljudets {langd:.1f} s"
        return None

    if slut is None:
        return None
    # Svagt fall: äldre schema utan duration. Kräv både rimlig sluttid OCH
    # namnsläktskap, så att två lika långa memon inte kan förväxlas.
    if not (0 <= langd - slut <= TOLERANS_SLUT):
        return None
    a, b = k.normalisera_ord(gammal_stam), k.normalisera_ord(ljud.stem)
    slakt = a.endswith(b) or b.endswith(a) or k.likhet(a, b) >= MIN_NAMNLIKHET
    if not slakt:
        return None
    return (f"sista segment {slut:.1f} s strax före ljudets {langd:.1f} s, "
            f"och namnen hör ihop (saknar duration-fält)")


def planera_omdopningar(root: Path) -> tuple[list[Omdopning], list[str]]:
    """Föräldralösa grupper parade med sitt ljud. Returnerar (planer, olösta)."""
    planer: list[Omdopning] = []
    olosta: list[str] = []

    # Bara grupper som faktiskt saknar ljud är intressanta. Utan den här
    # gallringen probas hela arkivet i onödan när ingenting är trasigt.
    foraldralosa = []
    for (mapp, stam), filer in sorted(harledda_grupper(root).items(),
                                      key=lambda x: (str(x[0][0]), x[0][1])):
        if any((mapp / f"{stam}{e}").is_file() for e in k.LJUDANDELSER):
            continue                                    # ljudet finns kvar
        if (mapp / f"{stam}.json").is_file():
            foraldralosa.append((mapp, stam, filer))
        # utan .json finns inget att bevisa mot (texta-mig/ m.fl.) — hoppas över
    if not foraldralosa:
        return [], []

    kandidater = ljud_utan_transkript(root)
    langder = langdindex(kandidater)

    for mapp, stam, filer in foraldralosa:
        json_path = mapp / f"{stam}.json"
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            olosta.append(f"{json_path.relative_to(root).as_posix()}: går inte att läsa")
            continue

        traffar = [(ljud, b) for ljud in kandidater
                   if (b := bevisa(data, ljud, stam, langder.get(ljud)))]
        rel = f"{mapp.relative_to(root).as_posix() or '(roten)'}/{stam}"
        if not traffar:
            olosta.append(f"{rel}: ingen ljudfil vars längd stämmer")
            continue
        if len(traffar) > 1:
            namn = ", ".join(p.name for p, _ in traffar)
            olosta.append(f"{rel}: flera lika långa kandidater ({namn}) — reds ut för hand")
            continue

        ljud, bevis = traffar[0]
        planer.append(Omdopning(
            gammal_mapp=mapp, gammal_stam=stam,
            ny_mapp=ljud.parent, ny_stam=ljud.stem,
            filer=sorted(filer), bevis=bevis,
            state_filer=sorted(STATE_DIR.glob(f"{stam}-corrections*.json")),
        ))
    return planer, olosta


# --------------------------------------------------------------------------- #
# Omdöpning
# --------------------------------------------------------------------------- #

def nytt_namn(fil: Path, gammal_stam: str, ny_stam: str) -> str:
    suffix = fil.name[len(gammal_stam):]
    return f"{ny_stam}{suffix}"


def kontrollera(plan: Omdopning) -> None:
    """Vägra innan något rörts. Ett halvt utfört byte är värre än inget."""
    for fil in plan.filer:
        mal = plan.ny_mapp / nytt_namn(fil, plan.gammal_stam, plan.ny_stam)
        if mal.exists():
            raise SynkFel(
                f"{mal.name} finns redan i {plan.ny_mapp.name}",
                atgard="Ta bort eller flytta undan målfilen först — den kan höra "
                       "till en annan inspelning.")
    for fil in plan.state_filer:
        mal = STATE_DIR / nytt_namn(fil, plan.gammal_stam, plan.ny_stam)
        if mal.exists():
            raise SynkFel(
                f"granska/state/{mal.name} finns redan",
                atgard="Två arbetskopior skulle slås ihop. Avgör vilken som "
                       "gäller och ta bort den andra.")


def genomfor(plan: Omdopning) -> list[tuple[Path, Path]]:
    gjorda: list[tuple[Path, Path]] = []
    for fil in plan.filer:
        mal = plan.ny_mapp / nytt_namn(fil, plan.gammal_stam, plan.ny_stam)
        fil.rename(mal)
        gjorda.append((fil, mal))
    for fil in plan.state_filer:
        mal = STATE_DIR / nytt_namn(fil, plan.gammal_stam, plan.ny_stam)
        fil.rename(mal)
        gjorda.append((fil, mal))
    return gjorda


# --------------------------------------------------------------------------- #
# Innehållsfält
# --------------------------------------------------------------------------- #

def skriv_atomiskt(path: Path, text: str) -> None:
    """Tempfil + atomiskt byte. Ett avbrott mitt i får inte lämna en halv fil —
    det här är granskat material."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def ljud_for(json_path: Path) -> str | None:
    """Ljudfilens FAKTISKA namn bredvid ett transkript, med rätt skiftläge.

    None när det inte är entydigt. Två ljudfiler med samma stam är just det
    fallet namnvakten spärrar, och att välja den alfabetiskt första vore att
    gissa: zego-torpseminarium.aac och .m4a är två OLIKA inspelningar, och
    transkriptet kommer ur .m4a."""
    stam = json_path.stem
    traffar = [p.name for p in sorted(json_path.parent.iterdir())
               if p.is_file() and p.suffix.lower() in k.LJUDANDELSER
               and p.stem == stam]
    return traffar[0] if len(traffar) == 1 else None


def byt_stam(varde: str, ratt_stam: str) -> str | None:
    """Byt stammen i ett pekarvärde men BEHÅLL suffixet. None när värdet redan
    är rätt eller inte går att tolka.

    Suffixet får aldrig härledas om. En runda 2-sidecar pekar med flit på
    '<stam>-bak2.json' — rundans orörda bas — och att "rätta" den till
    '<stam>.json' vore att peka granskningen mot den redan applicerade texten."""
    for suffix in k.HARLEDDA_SUFFIX:
        if varde.endswith(suffix) and len(varde) > len(suffix):
            ny = f"{ratt_stam}{suffix}"
            return None if ny == varde else ny
    return None


def planera_falt(root: Path) -> list[Faltfix]:
    """Pekarfält som inte stämmer med disk. Körs EFTER omdöpningarna, så den
    ser de nya namnen och behöver inte simulera dem."""
    cfg = k.load_config()
    fixar: list[Faltfix] = []
    for json_path in sorted(k.iter_transkript(root)):
        # Spärrade filer rörs inte. Namnvakten är den som avgör vad som är
        # tvetydigt, och en fil vars stam har två ljudfiler går inte att laga
        # utan att först välja vilken inspelning som gäller — Lars beslut.
        try:
            k.vakta_transkript(cfg, json_path)
        except k.NamnFel:
            continue
        ljud = ljud_for(json_path)
        if ljud is None:
            continue                    # namnvakten larmar om detta separat
        stam = json_path.stem

        # 1. Transkriptet och dess baskopior.
        for namn in (f"{stam}.json", f"{stam}-bak.json", f"{stam}-bak2.json"):
            p = json_path.with_name(namn)
            if not p.is_file():
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if "audio_file" in d and d["audio_file"] != ljud:
                fixar.append(Faltfix(p, "audio_file", d["audio_file"], ljud))

        # 2. Sidecars, både fröet i datamappen och arbetskopian i state/.
        for namn in (f"{stam}-corrections.json", f"{stam}-corrections-2.json"):
            for p in (json_path.with_name(namn), STATE_DIR / namn):
                if not p.is_file():
                    continue
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if "audio_file" in d and d["audio_file"] != ljud:
                    fixar.append(Faltfix(p, "audio_file", d["audio_file"], ljud))
                # transcript_json och base_json får bara byta STAM. Vilken fil
                # de pekar på är rundans logik, inte vår.
                for falt in ("transcript_json", "base_json"):
                    if isinstance(d.get(falt), str):
                        ny = byt_stam(d[falt], stam)
                        if ny:
                            fixar.append(Faltfix(p, falt, d[falt], ny))

        # 3. Markdownens frontmatter.
        md = json_path.with_name(f"{stam}.md")
        if md.is_file():
            fixar.extend(md_fixar(md, stam, ljud))

        # 4. Rubrikstycket i -borttaget.txt.
        bt = json_path.with_name(f"{stam}-borttaget.txt")
        if bt.is_file():
            fixar.extend(borttaget_fixar(bt, stam))
    return fixar


def md_frontmatter(text: str) -> tuple[list[str], int, int] | None:
    """(rader, start, slut) för YAML-blocket mellan de två första '---'."""
    rader = text.splitlines()
    if not rader or rader[0].strip() != "---":
        return None
    for i in range(1, len(rader)):
        if rader[i].strip() == "---":
            return rader, 1, i
    return None


def md_fixar(md: Path, stam: str, ljud: str) -> list[Faltfix]:
    fm = md_frontmatter(md.read_text(encoding="utf-8"))
    if fm is None:
        return []
    rader, start, slut = fm
    ut = []
    for i in range(start, slut):
        nyckel, _, varde = rader[i].partition(":")
        nyckel, varde = nyckel.strip(), varde.strip()
        if nyckel == "titel":
            # Steg c sätter titel till stammen: uppmätt 54 av 55 .md-filer, och
            # den enda avvikaren var en fil vars ljud döpts om. En titel som
            # redan är normaliserad är alltså maskinsatt och ska följa med
            # stammen. Har du skrivit en riktig rubrik — med blanksteg eller
            # versal — är den innehåll, och stammen trycks inte över den.
            if k.normalize_stem(varde) != varde:
                continue
            ny = stam if varde != stam else None
        elif nyckel == "ljudfil":
            ny = ljud if varde != ljud else None
        elif nyckel == "kalla_json":
            ny = byt_stam(varde, stam)
        else:
            continue
        if ny:
            ut.append(Faltfix(md, f"frontmatter/{nyckel}", varde, ny))
    return ut


def borttaget_fixar(bt: Path, stam: str) -> list[Faltfix]:
    """Rubrikstycket nämner både .json och .md vid namn. Bara det stycket rörs —
    resten är citat ur memot och ska inte sökas igenom efter filnamn."""
    text = bt.read_text(encoding="utf-8")
    huvud = text.split("\n\n", 1)[0]
    # Skalet av skiljetecken FÖRST, sedan ändelsekontrollen. Tvärtom missas
    # 'kirk-owens-posobiec.md.' sist i meningen — den slutar på punkt, inte
    # på '.md', och blev tyst kvar när syskonet före kommatecknet rättades.
    gamla = {rensad for ord_ in huvud.split()
             if (rensad := ord_.strip(".,;:()")).endswith((".json", ".md"))}
    ut = []
    for g in sorted(gamla):
        ratt = f"{stam}{'.json' if g.endswith('.json') else '.md'}"
        if g != ratt:
            ut.append(Faltfix(bt, "rubrikstycke", g, ratt))
    return ut


def tillampa_falt(fix: Faltfix) -> None:
    if fix.fil.suffix == ".json":
        d = json.loads(fix.fil.read_text(encoding="utf-8"))
        d[fix.falt] = fix.nytt
        skriv_atomiskt(fix.fil, json.dumps(d, ensure_ascii=False, indent=2))
        return
    if fix.fil.suffix == ".md":
        text = fix.fil.read_text(encoding="utf-8")
        rader, start, slut = md_frontmatter(text)
        nyckel = fix.falt.split("/", 1)[1]
        for i in range(start, slut):
            if rader[i].split(":", 1)[0].strip() == nyckel:
                rader[i] = f"{nyckel}: {fix.nytt}"
        skriv_atomiskt(fix.fil, "\n".join(rader) + "\n")
        return
    # -borttaget.txt: byt bara i rubrikstycket, och bara på hela filnamn. En rå
    # replace av 'x.json' träffar mitt inne i 'zego-x.json' och ger 'zego-zego-x'
    # — därför en vakt mot att träffen föregås av ett namntecken.
    text = fix.fil.read_text(encoding="utf-8")
    huvud, _, resten = text.partition("\n\n")
    ny_huvud = re.sub(rf"(?<![\w-]){re.escape(fix.gammalt)}", fix.nytt, huvud)
    skriv_atomiskt(fix.fil, ny_huvud + "\n\n" + resten)


# --------------------------------------------------------------------------- #
# GUI:ts eget tillstånd
# --------------------------------------------------------------------------- #

def sokvagsbyten(root: Path, planer: list[Omdopning]) -> dict[str, str]:
    """Gammal -> ny sökväg relativt datamappen, för varje omdöpt fil.

    Hela sökvägen och inte bara stammen: planering-med-ai flyttade dessutom från
    inkorgen till meta-admin-todo-fix/, och en ren stamersättning hade lämnat
    mappdelen fel."""
    byten: dict[str, str] = {}
    for p in planer:
        for fil in p.filer:
            ny = p.ny_mapp / nytt_namn(fil, p.gammal_stam, p.ny_stam)
            byten[fil.relative_to(root).as_posix()] = ny.relative_to(root).as_posix()
    return byten


def synka_gui(root: Path, planer: list[Omdopning]) -> list[str]:
    """current.json och status.json pekar med stam respektive relativ sökväg.
    Lämnas de kvar öppnar GUI:t en fil som inte finns."""
    gjort: list[str] = []
    byten = sokvagsbyten(root, planer)
    stammar = {p.gammal_stam: p.ny_stam for p in planer}

    cur_p = k.PROJECT_ROOT / "granska" / "current.json"
    if cur_p.is_file():
        cur = json.loads(cur_p.read_text(encoding="utf-8"))
        ny = dict(cur)
        for falt, varde in cur.items():
            if not isinstance(varde, str):
                continue
            if falt == "stem" and varde in stammar:
                ny[falt] = stammar[varde]
            elif varde in byten:
                ny[falt] = byten[varde]
        if ny != cur:
            skriv_atomiskt(cur_p, json.dumps(ny, ensure_ascii=False, indent=4))
            gjort.append("granska/current.json")

    st_p = k.PROJECT_ROOT / "granska" / "status.json"
    if st_p.is_file():
        st = json.loads(st_p.read_text(encoding="utf-8"))
        ny_st = {byten.get(nyckel, nyckel): v for nyckel, v in st.items()}
        if ny_st != st:
            skriv_atomiskt(st_p, json.dumps(ny_st, ensure_ascii=False, indent=4))
            gjort.append("granska/status.json")
    return gjort


# --------------------------------------------------------------------------- #
# Huvudflöde
# --------------------------------------------------------------------------- #

def main() -> int:
    kor = "--kor" in sys.argv
    bara_falt = "--bara-falt" in sys.argv
    for a in sys.argv[1:]:
        if a not in ("--kor", "--bara-falt", "--dry-run"):
            print(f"FEL: okänt argument {a!r}", file=sys.stderr)
            return 2

    cfg = k.load_config()
    root = Path(cfg["data"]["root"])
    print(f"Datamapp: {root}")
    print()

    planer, olosta = ([], []) if bara_falt else planera_omdopningar(root)

    # Vägra allt innan något rörts.
    vagrade = []
    for p in list(planer):
        try:
            kontrollera(p)
        except SynkFel as e:
            planer.remove(p)
            vagrade.append((p, e))

    if planer:
        print(f"OMDÖPNINGAR — {len(planer)} grupp(er)")
        print()
        for p in planer:
            gm = p.gammal_mapp.relative_to(root).as_posix() or "(roten)"
            nm = p.ny_mapp.relative_to(root).as_posix() or "(roten)"
            flytt = f"   FLYTT {gm} -> {nm}" if p.gammal_mapp != p.ny_mapp else ""
            print(f"  {gm}/{p.gammal_stam}  ->  {p.ny_stam}{flytt}")
            print(f"      bevis: {p.bevis}")
            for f in p.filer:
                print(f"      {f.name}  ->  {nytt_namn(f, p.gammal_stam, p.ny_stam)}")
            for f in p.state_filer:
                print(f"      granska/state/{f.name}  ->  "
                      f"{nytt_namn(f, p.gammal_stam, p.ny_stam)}")
            print()
    elif not bara_falt:
        print("OMDÖPNINGAR: inga — alla härledda filer har sitt ljud.")
        print()

    if vagrade:
        print(f"VÄGRADE — {len(vagrade)}")
        for p, e in vagrade:
            print(f"  {p.gammal_stam}: {e}")
            if e.atgard:
                print(f"      {e.atgard}")
        print()

    if olosta:
        print(f"OLÖSTA — {len(olosta)} grupp(er) utan bevisbar ljudfil")
        print("  Ingenting görs med dessa. Tidigare försök utan ljud hör hit.")
        for rad in olosta:
            print(f"  {rad}")
        print()

    if not kor:
        # Fältfixarna beräknas mot nuvarande namn. Efter omdöpningen tillkommer
        # de som gruppen ovan för med sig — därför räknas de separat här.
        fixar = planera_falt(root)
        print(f"INNEHÅLLSFÄLT — {len(fixar)} som pekar fel idag")
        for f in fixar[:20]:
            try:
                var = f.fil.relative_to(root).as_posix()
            except ValueError:
                var = f"granska/state/{f.fil.name}"
            print(f"  {var}")
            print(f"      {f.falt}: {f.gammalt!r} -> {f.nytt!r}")
        if len(fixar) > 20:
            print(f"  ... och {len(fixar) - 20} till")
        print()
        print("Detta var en torrkörning. Kör med --kor för att genomföra.")
        return 0

    # --- skarpt ---
    # GUI-tillståndet synkas FÖRE omdöpningen: sokvagsbyten() beskriver planen,
    # och den behöver inte disken för att göra det.
    gjort = synka_gui(root, planer) if planer else []

    for p in planer:
        antal = len(genomfor(p))
        print(f"omdöpt: {p.gammal_stam} -> {p.ny_stam} ({antal} filer)")
    if planer:
        print()

    fixar = planera_falt(root)
    for f in fixar:
        tillampa_falt(f)
    print(f"innehållsfält: {len(fixar)} rättade")

    for g in gjort:
        print(f"uppdaterad: {g}")

    print()
    print(f"Klart. {len(planer)} grupp(er) omdöpta, {len(fixar)} fält rättade.")
    print("Kör namnvakt.py för att bekräfta att inget är kvar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
