"""Kör nästa steg efter granskningen på begäran från webb-GUI:t.

    arbetare.py --en-gang      kör det jobb som ligger i granska/state/, avsluta
    arbetare.py --poll=2       bevaka och kör jobb i en loop (arbetarcontainern)

GUI:t kan inte göra det här själv: PHP-containern har ingen Python, monterar
datamappen read-only och ser inte projektroten. `granska/jobb.php` lägger därför
ett jobb i `granska/state/`, och den här processen utför det.

**En skrivare per fil — inget lås.** `granska/save.php` skriver i sidecaren under
`flock`; ett rådgivande lås från Python propagerar inte pålitligt över Docker
Desktops bind-mount från Windows, och ett lås som tyst inte fungerar är sämre än
inget. Därför rör arbetaren **aldrig** `<stam>-corrections.json`:

- propageringens förslag läggs i `<stam>-propagering.json`, och `index.php`
  fogar in dem på index som saknar flagga — Lars beslut kan aldrig skrivas över
- appliceringen skyddas av jobbets `sidecar_sha256`: ändrades sidecaren efter
  klicket vägrar arbetaren, och ändras den under körningen anmärks det

**Vaktkedjan är batchens, inte enfilsvägens.** `applicera-corrections.py:main()`
saknar kontroll av ogranskade flaggor och stämplar `corrections_applied_at` ändå
— här följs `batch-applicera.py:107-130`, som vägrar.

**Rapporten formateras en gång.** De befintliga utskrifterna fångas med
`redirect_stdout` och skickas både till terminalen (syns i `docker compose up`)
och till `jobb-status.json`, så panelen och terminalen aldrig kan säga olika.

Se CLAUDE.md.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import korrigeringar as k

ap = k.ladda("applicera-corrections")
pn = k.ladda("propagera-namn")

STATE = k.PROJECT_ROOT / "granska" / "state"
JOBB = STATE / "jobb.json"
STATUS = STATE / "jobb-status.json"
HJARTSLAG = STATE / "arbetare.json"

GILTIGA_ATGARDER = ("propagera-applicera",)


def nu() -> str:
    return datetime.now().isoformat(timespec="seconds")


def skriv_atomiskt(path: Path, data: dict) -> None:
    """Tmp + os.replace. En läsare ska se gammal eller ny fil, aldrig halv."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def las_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Rapport
# --------------------------------------------------------------------------- #

class Rapport:
    """Samlar rader och skriver dem till terminalen samtidigt."""

    def __init__(self) -> None:
        self.rader: list[str] = []

    def rad(self, text: str = "") -> None:
        self.rader.append(text)
        print(text, flush=True)

    def fanga(self, fn, *args, **kwargs) -> None:
        """Kör en befintlig utskriftsfunktion och ta dess rader."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*args, **kwargs)
        for r in buf.getvalue().rstrip("\n").split("\n"):
            self.rad(r)


# --------------------------------------------------------------------------- #
# Jobbet
# --------------------------------------------------------------------------- #

def skriv_status(jobb: dict, lage: str, *, steg: list[dict], rapport: Rapport,
                 sammanfattning: str = "", nasta: str = "inget",
                 fel: dict | None = None, startad: str = "") -> None:
    skriv_atomiskt(STATUS, {
        "id": jobb.get("id"),
        "stem": jobb.get("stem"),
        "lage": lage,
        "startad": startad or nu(),
        "avslutad": None if lage == "kor" else nu(),
        "steg": steg,
        "sammanfattning": sammanfattning,
        "nasta": nasta,
        "fel": fel,
        "rader": rapport.rader,
    })


def kor_jobb(jobb: dict) -> int:
    """Utför ett jobb. Returnerar 0 när allt gick, 1 vid vägran eller fel."""
    cfg = k.load_config()
    root = Path(cfg["data"]["root"])
    # _ljudindex är lru_cachad med antagandet "kan inte bli inaktuell mitt i ett
    # jobb". Det gäller korta skript, inte en process som lever i dagar.
    k.nollstall_namnindex()

    startad = nu()
    rapport = Rapport()
    steg: list[dict] = []

    def vagra(skal: str, atgard: str = "") -> int:
        rapport.rad(f"VÄGRAT: {skal}")
        if atgard:
            rapport.rad(f"        {atgard}")
        skriv_status(jobb, "vagrad", steg=steg, rapport=rapport,
                     sammanfattning=skal, startad=startad,
                     fel={"meddelande": skal, "atgard": atgard})
        return 1

    if jobb.get("atgard") not in GILTIGA_ATGARDER:
        return vagra(f"okänd åtgärd {jobb.get('atgard')!r}")

    json_path = root / jobb["transcript_json"]
    rapport.rad(f"=== jobb {jobb['id']} ===")
    rapport.rad(f"Fil:      {json_path.name}")
    if not json_path.is_file():
        return vagra(f"JSON saknas: {json_path}")

    # Namnvakten: en kolliderande stam får inte appliceras, eftersom sidecaren i
    # den platta state/ kan tillhöra en annan fil. Ingen SystemExit här — det är
    # en loop.
    try:
        for varning in k.vakta_transkript(cfg, json_path):
            rapport.rad(f"VARNING:  {varning}")
    except k.NamnFel as e:
        return vagra(str(e), e.atgard)

    if k.status_for(cfg, json_path):
        return vagra('filen är märkt "ny transkription behövs"',
                     "Avmarkera i GUI:t, eller transkribera om filen.")

    sidecar_path, runda, var = k.valj_sidecar(json_path)
    if sidecar_path is None:
        return vagra(f"ingen sidecar för {json_path.name}", "Kör steg b först.")
    sidecar_path = Path(sidecar_path)

    # Innehållsstämpeln ersätter låset: ändrades sidecaren efter att knappen
    # trycktes är det inte längre det underlag Lars godkände.
    sha_nu = sha256(sidecar_path)
    if jobb.get("sidecar_sha256") and sha_nu != jobb["sidecar_sha256"]:
        return vagra("sidecaren ändrades efter att du tryckte",
                     "Tryck igen — då räknas dina senaste beslut med.")

    side = las_json(sidecar_path)
    if side is None:
        return vagra(f"{sidecar_path.name} går inte att tolka")
    rapport.rad(f"Sidecar:  {sidecar_path.name} (runda {runda}, {var})")

    # ---- Steg 1: propagering -------------------------------------------------
    try:
        nya, n_ankare, n_provade = pn.propagera(
            json_path, sidecar_path, side, pn.TROSKEL, cfg)
    except ValueError as e:
        return vagra(str(e))

    rapport.rad(f"Ankare:   {n_ankare} namnlika former ur "
                f"{len(pn.rattelselogg(side))} beslut")
    rapport.rad(f"Prövade:  {n_provade} obeslutade ord, tröskel {pn.TROSKEL:.2f}")

    # Bara förslag på index som ännu är fria — samma regel som infogningen i
    # index.php följer, så siffran i rapporten är den Lars faktiskt får se.
    tackta = k.fras_tackta(side)
    har_flagga = {f.get("global_index") for f in side.get("flags", [])}
    nya = [f for f in nya
           if f["global_index"] not in har_flagga
           and f["global_index"] not in tackta]

    if nya:
        for f in sorted(nya, key=lambda x: x["global_index"]):
            rapport.rad(f"  ord {f['global_index']:<6} {f['heard']:<18} -> "
                        f"{f['ai_guess']:<18} ({f['_poang']:.2f})")
            rapport.rad(f"      {f['ai_reason']}")
        forslag = STATE / f"{json_path.stem}-propagering.json"
        skriv_atomiskt(forslag, {
            "jobb_id": jobb["id"],
            "stem": json_path.stem,
            "sidecar_sha256": sha_nu,
            "skapad": nu(),
            "troskel": pn.TROSKEL,
            "ankare": n_ankare,
            "provade": n_provade,
            "flaggor": nya,
        })
        steg.append({"namn": "propagering", "lage": "klar", "nya_flaggor": len(nya),
                     "ankare": n_ankare, "provade": n_provade})
        steg.append({"namn": "applicering", "lage": "hoppad",
                     "skal": f"{len(nya)} nya flaggor att granska först"})
        rapport.rad()
        rapport.rad(f"{len(nya)} nya flaggor — INGET applicerat.")
        rapport.rad(f"Skrev {forslag.name}. Ladda om granskningsvyn, avgör dem, "
                    f"och tryck igen.")
        skriv_status(jobb, "klar", steg=steg, rapport=rapport, startad=startad,
                     sammanfattning=f"{len(nya)} nya flaggor från propageringen "
                                    f"— inget applicerat. Granska dem först.",
                     nasta="ladda-om")
        return 0

    steg.append({"namn": "propagering", "lage": "klar", "nya_flaggor": 0,
                 "ankare": n_ankare, "provade": n_provade})
    rapport.rad("Inga nya förslag från propageringen.")
    rapport.rad()

    # ---- Steg 2: applicering ------------------------------------------------
    # Vaktordningen är batch-applicera.py:107-130, inte enfilsvägens (som
    # saknar kontrollen av ogranskade flaggor och stämplar ändå).
    if not k.antal_operationer(side):
        return vagra("sidecaren har inga operationer att tillämpa",
                     "Inget att applicera — filen är redan ren.")
    data = las_json(json_path) or {}
    if data.get("corrections_applied_at"):
        return vagra("filen är redan applicerad",
                     "Nya beslut kräver 'applicera --igen <fil>' på värden, "
                     "vilket knappen med flit inte erbjuder.")
    kvar = k.antal_ogranskade(side)
    if kvar:
        return vagra(f"{kvar} flagga(or) saknar beslut",
                     "Räkningen är strängare än GUI:ts: tomt decision-fält "
                     "räknas också. Granska dem och tryck igen.")

    try:
        u = ap.applicera_en(json_path, sidecar_path=sidecar_path)
    except ap.ApplyFel as e:
        rapport.rad(f"FEL: {e}")
        if e.atgard:
            rapport.rad(f"     {e.atgard}")
        steg.append({"namn": "applicering", "lage": "fel", "skal": str(e)})
        skriv_status(jobb, "fel", steg=steg, rapport=rapport, startad=startad,
                     sammanfattning=str(e),
                     fel={"meddelande": str(e), "atgard": e.atgard})
        return 1
    except OSError as e:
        rapport.rad(f"FEL: skrivfel — {e}")
        rapport.rad("     Dropbox kan hålla filen. Pausa synken och tryck igen.")
        rapport.rad("     Obs: om .json hann skrivas kan .srt/.txt vara "
                    "inaktuella — kör 'applicera --igen' på värden.")
        steg.append({"namn": "applicering", "lage": "fel", "skal": str(e)})
        skriv_status(jobb, "fel", steg=steg, rapport=rapport, startad=startad,
                     sammanfattning=f"skrivfel: {e}",
                     fel={"meddelande": f"skrivfel: {e}",
                          "atgard": "Pausa Dropbox-synken och tryck igen."})
        return 1

    rapport.fanga(ap.skriv_rapport, u, f"jobb {jobb['id']}")
    steg.append({"namn": "applicering", "lage": "klar",
                 "ord_fore": u.ord_fore, "ord_efter": u.ord_efter,
                 "tillampat": dict(u.tillampat)})

    lage, anm = "klar", ""
    if sha256(sidecar_path) != sha_nu:
        lage, anm = "klar-med-anmarkning", (
            "sidecaren ändrades under körningen — kontrollera att dina sista "
            "beslut kom med")
        rapport.rad(f"ANMÄRKNING: {anm}")

    rapport.rad()
    rapport.rad("Applicerat. Kör steg c (`forbattra`) för att skriva om .md:n — "
                "negationsvakten följer med där.")
    skriv_status(jobb, lage, steg=steg, rapport=rapport, startad=startad,
                 sammanfattning=anm or "Applicerat. Kör steg c för .md:n.",
                 nasta="oppna-valjaren")
    return 0


# --------------------------------------------------------------------------- #

def hjartslag(poll: float) -> None:
    skriv_atomiskt(HJARTSLAG, {"hjartslag": nu(), "version": "arbetare.py",
                               "pollsekunder": poll})


def ett_varv() -> int:
    """Kör jobbet i state/ om det är nytt. 0 = inget att göra eller klart."""
    jobb = las_json(JOBB)
    if not jobb or not jobb.get("id"):
        return 0
    gammal = las_json(STATUS) or {}
    if gammal.get("id") == jobb["id"]:
        return 0                      # redan utfört
    return kor_jobb(jobb)


def main() -> int:
    args = sys.argv[1:]
    poll = 0.0
    for a in args:
        if a.startswith("--poll"):
            poll = float(a.split("=", 1)[1]) if "=" in a else 2.0
        elif a != "--en-gang":
            print(f"FEL: okänd flagga {a!r}.", file=sys.stderr)
            return 2
    STATE.mkdir(parents=True, exist_ok=True)

    if not poll:
        if not JOBB.is_file():
            print(f"Inget jobb i {JOBB.relative_to(k.PROJECT_ROOT).as_posix()}.")
            return 0
        return ett_varv()

    print(f"Arbetaren bevakar {JOBB.relative_to(k.PROJECT_ROOT).as_posix()} "
          f"var {poll:g} s. Avbryt med Ctrl+C.", flush=True)
    while True:
        hjartslag(poll)
        try:
            ett_varv()
        except Exception as e:                      # loopen får aldrig dö
            print(f"OVÄNTAT FEL: {e!r}", file=sys.stderr, flush=True)
        time.sleep(poll)


if __name__ == "__main__":
    raise SystemExit(main())
