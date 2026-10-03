"""Radera ett memo ur systemet — ljud, transkript och allt härlett.

    radera.py <stam>                       visa vad som skulle raderas
    radera.py <stam> --kor                 gör det
    radera.py <stam> --kor --totalt        ta .md och -borttaget.txt också
    radera.py <stam> --kor --till <mapp>   flytta dem dit i stället för att
                                           lämna dem kvar

**`--dry-run` är standard.** Skriptet skriver bara med `--kor`, av samma skäl
som `synka-namn.py`: här ligger färdigt material, och en radering ångras inte
med en knapp.

**Ingen upptäck-och-radera.** En stam i taget, utpekad för hand. Ljudet är det
enda i projektet som inte kan återskapas, och ett svep som gissar vilka memon
som är "klara" skulle sätta hela arkivet på spel för att spara några
tangenttryck.

**`.md` och `-borttaget.txt` behålls som standard.** De är memots innehåll;
resten är maskineri. `-borttaget.txt` bär dessutom det steg c föreslog att kapa,
och det materialet finns *bara* där och i JSON:en — raderas JSON:en utan att
`-borttaget.txt` sparas är det borta för gott.

Se CLAUDE.md.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import korrigeringar as k

LOGGFIL = k.PROJECT_ROOT / "logs" / "raderat.log"
BEHALL_SUFFIX = (".md", "-borttaget.txt")


# --------------------------------------------------------------------------- #
# Hitta memot
# --------------------------------------------------------------------------- #

def hor_till(p: Path, stam: str) -> bool:
    """Hör filen till memot? Ljudet matchas på stammen, allt annat på det
    härledda suffixet — så `zego-x-2.json` inte dras med av `zego-x`."""
    if p.suffix.lower() in k.LJUDANDELSER:
        return p.stem == stam
    return k.stam_av(p.name) == stam


def familj_i(mapp: Path, stam: str) -> list[Path]:
    """Memots filer i en mapp."""
    return sorted(p for p in mapp.iterdir() if p.is_file() and hor_till(p, stam))


def hitta_mapp(root: Path, stam: str) -> tuple[Path | None, list[Path]]:
    """Mappen memot ligger i, och alla filer som hör till stammen.

    Letar i hela arkivet: en stam ska vara unik överallt (`granska/state/` är
    platt — se namnvakten), så fler än en mapp är ett fall skriptet vägrar.
    """
    traffar: dict[Path, list[Path]] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if set(p.relative_to(root).parts) & k.EXCLUDE_DIRS:
            continue
        if hor_till(p, stam):
            traffar.setdefault(p.parent, []).append(p)
    if not traffar:
        return None, []
    if len(traffar) > 1:
        return None, [q for lista in traffar.values() for q in lista]
    mapp, filer = next(iter(traffar.items()))
    return mapp, sorted(filer)


def ljudlangd(p: Path) -> float | None:
    try:
        ut = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return float(ut) if ut else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


# --------------------------------------------------------------------------- #
# Vad kastar du?
# --------------------------------------------------------------------------- #

def beslut_i_sidecar(side: Path) -> tuple[int, int]:
    """(antal flaggor, antal fattade beslut) i en sidecar."""
    try:
        s = json.loads(side.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0, 0
    fl = s.get("flags") or s.get("flaggor") or []
    tagna = sum(1 for f in fl
                if (f.get("decision") or f.get("beslut")) not in (None, "", "pending"))
    return len(fl), tagna


def rapportera(cfg: dict, mapp: Path, stam: str, filer: list[Path],
               root: Path) -> list[str]:
    """Skriv ut vad som finns och vad som kastas. Returnerar varningar."""
    varningar: list[str] = []
    ljud = [q for q in filer if q.suffix.lower() in k.LJUDANDELSER]
    json_path = mapp / f"{stam}.json"

    print(f"Memo:    {mapp.relative_to(root).as_posix()}/{stam}")
    bytes_tot = sum(q.stat().st_size for q in filer)
    print(f"Filer:   {len(filer)} st, {bytes_tot / 1024 / 1024:.1f} MB")
    print()

    # Ljudet — det enda som inte kan återskapas.
    for q in ljud:
        sek = ljudlangd(q)
        langd = f"{sek / 60:.1f} min" if sek else "okänd längd"
        print(f"  LJUD   {q.name}  ({langd})  — går inte att återskapa")
    if not ljud:
        varningar.append("ingen ljudfil hittades — bara härledda filer finns kvar")

    # Transkriptet: modell, längd, applicerad.
    if json_path.is_file():
        try:
            d = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            d = {}
        modell = (d.get("model") or "okänd").replace("KBLab/kb-whisper-", "")
        app = d.get("corrections_applied_at")
        print(f"  TEXT   {stam}.json  (modell {modell}, "
              f"{'applicerad ' + str(app)[:10] if app else 'ej applicerad'})")
        dur = d.get("duration")
        if dur and ljud:
            sek = ljudlangd(ljud[0])
            if sek and abs(sek - float(dur)) > 1.0:
                varningar.append(
                    f"ljudets längd ({sek:.1f} s) stämmer inte med JSON:ens "
                    f"duration ({float(dur):.1f} s) — är det samma inspelning?")

    # Granskningsarbete.
    side, runda, var = k.valj_sidecar(json_path)
    if side:
        n, tagna = beslut_i_sidecar(Path(side))
        print(f"  BESLUT {n} flaggor, {tagna} fattade  (runda {runda}, {var})")
        if tagna and json_path.is_file():
            try:
                app = bool(json.loads(json_path.read_text(encoding="utf-8"))
                           .get("corrections_applied_at"))
            except (json.JSONDecodeError, OSError):
                app = False
            if not app:
                varningar.append(
                    f"{tagna} fattade beslut är INTE applicerade — den "
                    f"granskningstiden går förlorad")

    # Det som behålls om du inte säger --totalt.
    for suffix in BEHALL_SUFFIX:
        q = mapp / f"{stam}{suffix}"
        if not q.is_file():
            continue
        if suffix == "-borttaget.txt":
            rader = sum(1 for r in q.read_text(encoding="utf-8").splitlines()
                        if r.startswith("["))
            print(f"  BEHÅLL {q.name}  ({rader} föreslagna strykningar — finns "
                  f"bara här och i JSON:en)")
        else:
            print(f"  BEHÅLL {q.name}  ({q.stat().st_size / 1024:.1f} kB)")
    return varningar


# --------------------------------------------------------------------------- #
# De tre ställena utanför datamappen
# --------------------------------------------------------------------------- #

def stada_gui(cfg: dict, stam: str, json_path: Path, kor: bool) -> list[str]:
    """Arbetskopior, statusmärkning och GUI:ts filval.

    Lämnas de kvar blir de spöken i väljaren — och `granska/state/` slås upp på
    **bara stammen**, så en framtida inspelning med samma stam skulle ärva de
    gamla besluten och få flaggor ritade på fel ord.
    """
    gjort: list[str] = []
    state = k.PROJECT_ROOT / "granska" / "state"
    for q in sorted(state.glob(f"{stam}-corrections*.json")):
        gjort.append(f"granska/state/{q.name}")
        if kor:
            q.unlink()

    status_fil = k.PROJECT_ROOT / "granska" / "status.json"
    alla = k.las_status(cfg)
    try:
        rel = k.rel_to_root(cfg, json_path)
    except ValueError:
        rel = None
    nycklar = [r for r in alla
               if r == rel or Path(r).name == f"{stam}.json"]
    for r in nycklar:
        gjort.append(f"granska/status.json: {r}")
        if kor:
            del alla[r]
    if kor and nycklar:
        status_fil.write_text(json.dumps(alla, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")

    cur_fil = k.PROJECT_ROOT / "granska" / "current.json"
    if cur_fil.is_file():
        try:
            cur = json.loads(cur_fil.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cur = {}
        if cur.get("stem") == stam:
            gjort.append("granska/current.json (GUI:ts filval nollställs)")
            if kor:
                cur_fil.write_text("{}\n", encoding="utf-8")
    return gjort


def logga(mapp: Path, stam: str, root: Path, rad: dict) -> None:
    """En rad per radering. Utan den krymper arkivet tyst, och en medveten
    radering går inte att skilja från en bugg."""
    LOGGFIL.parent.mkdir(parents=True, exist_ok=True)
    delar = [datetime.now().isoformat(timespec="seconds"),
             f"stam={stam}",
             f"mapp={mapp.relative_to(root).as_posix()}"]
    delar += [f"{n}={v}" for n, v in rad.items()]
    with LOGGFIL.open("a", encoding="utf-8") as f:
        f.write("  ".join(delar) + "\n")


# --------------------------------------------------------------------------- #

def main() -> int:
    args = sys.argv[1:]
    kor = "--kor" in args
    totalt = "--totalt" in args
    till = None
    if "--till" in args:
        i = args.index("--till")
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            print("FEL: --till kräver en mapp.", file=sys.stderr)
            return 2
        till = args[i + 1]
    # --dry-run är redan standard, men den som skriver det ska inte mötas av ett
    # felmeddelande — flaggan finns i varje annat skript i projektet.
    kanda = {"--kor", "--totalt", "--till", "--dry-run", till}
    fria = [a for a in args if a not in kanda and not a.startswith("--")]
    okanda = [a for a in args if a.startswith("--") and a not in kanda]
    if okanda:
        print(f"FEL: okänd flagga {okanda[0]!r}.", file=sys.stderr)
        return 2
    if len(fria) != 1:
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    if totalt and till:
        print("FEL: --totalt och --till är varandras motsatser. Välj en.",
              file=sys.stderr)
        return 2

    cfg = k.load_config()
    root = Path(cfg["data"]["root"])
    stam = k.normalize_stem(fria[0].removesuffix(".json"))

    mapp, filer = hitta_mapp(root, stam)
    if mapp is None and not filer:
        print(f"FEL: hittar inget som heter {stam!r} i arkivet.", file=sys.stderr)
        return 2
    if mapp is None:
        print(f"FEL: {stam!r} finns i fler än en mapp — radera inget förrän "
              f"det är utrett:", file=sys.stderr)
        for q in sorted(filer):
            print(f"  {q.relative_to(root).as_posix()}", file=sys.stderr)
        return 2

    json_path = mapp / f"{stam}.json"
    varningar = rapportera(cfg, mapp, stam, filer, root)

    behall = [q for q in filer
              if any(q.name == f"{stam}{s}" for s in BEHALL_SUFFIX)] if not totalt else []
    raderas = [q for q in filer if q not in behall]
    gui = stada_gui(cfg, stam, json_path, kor=False)

    print()
    if gui:
        print("Utanför datamappen:")
        for rad in gui:
            print(f"  {rad}")
        print()
    if behall:
        vart = f"flyttas till {till}" if till else "lämnas kvar"
        print(f"Behålls ({vart}): {', '.join(q.name for q in behall)}")
    if totalt:
        print("--totalt: .md och -borttaget.txt raderas också.")
    for v in varningar:
        print(f"VARNING: {v}")

    if not kor:
        print()
        print(f"--dry-run (standard). {len(raderas)} filer skulle raderas.")
        print(f"Kör skarpt:  radera.py {stam} --kor"
              + (" --totalt" if totalt else "")
              + (f" --till {till}" if till else ""))
        return 0

    if not raderas and not gui and not (behall and till):
        print()
        print("Inget att radera — bara behållna filer finns kvar. "
              "Lägg till --totalt om de också ska bort.")
        return 0

    # Behållarna först: ett fel där ska inträffa innan något raderats.
    if behall and till:
        mal = Path(till)
        mal = mal if mal.is_absolute() else (root / till)
        if not mal.is_dir():
            print(f"FEL: {mal} finns inte. Ingenting raderat.", file=sys.stderr)
            return 2
        for q in behall:
            if (mal / q.name).exists():
                print(f"FEL: {mal / q.name} finns redan. Ingenting raderat.",
                      file=sys.stderr)
                return 1
        for q in behall:
            shutil.move(str(q), str(mal / q.name))
            print(f"  flyttad  {q.name}  ->  {mal}")

    print()
    misslyckade = []
    bytes_tot = 0
    for q in raderas:
        try:
            bytes_tot += q.stat().st_size
            q.unlink()
            print(f"  raderad  {q.name}")
        except OSError as e:
            misslyckade.append(f"{q.name}: {e}")
    for rad in stada_gui(cfg, stam, json_path, kor=True):
        print(f"  städad   {rad}")

    # Blev det komplett? Samma kontroll namnvakten gör, men direkt — och bara i
    # memots egen mapp. Letar man i hela arkivet räknas filer som --till just
    # flyttat till en annan mapp som kvarglömda.
    behallna_namn = {q.name for q in behall}
    kvar = [q for q in familj_i(mapp, stam) if q.name not in behallna_namn]
    if kvar:
        misslyckade += [f"kvar: {q.name}" for q in kvar]

    print()
    if misslyckade:
        print("OFULLSTÄNDIG radering:", file=sys.stderr)
        for m in misslyckade:
            print(f"  {m}", file=sys.stderr)
    else:
        print(f"Klart: {len(raderas)} filer raderade, "
              f"{bytes_tot / 1024 / 1024:.1f} MB.")

    logga(mapp, stam, root, {
        "raderade": len(raderas) - len(misslyckade),
        "mb": f"{bytes_tot / 1024 / 1024:.1f}",
        "behallna": ",".join(q.name for q in behall) or "-",
        "till": till or "-",
        "lage": "totalt" if totalt else "behall-md",
        "ofullstandig": len(misslyckade) or "-",
    })
    print(f"Loggat i {LOGGFIL.relative_to(k.PROJECT_ROOT).as_posix()}")
    return 1 if misslyckade else 0


if __name__ == "__main__":
    raise SystemExit(main())
