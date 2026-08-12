"""Batch för steg 4: skriv in granskningsbesluten i alla färdiggranskade filer.

Anrop:
    venv/Scripts/python.exe batch-applicera.py
        applicerar alla filer som är färdiggranskade och ännu inte applicerade.
    venv/Scripts/python.exe batch-applicera.py --dry-run
        räknar igenom allt och skriver INGENTING. Siffrorna är riktiga, inte
        uppskattade: arbetet är lokalt och gratis, så torrkörningen gör hela
        beräkningen och låter varje vakt smälla — den bara avstår från att skriva.
    venv/Scripts/python.exe batch-applicera.py Kirk-.../zego-x.json ...
        kör de angivna filerna (relativt data.root eller absoluta).

Flaggor: --antal=N, --igen (även redan applicerade), --tillat-ogranskade,
--aven-tomma.

`applicera-corrections.py` tar EN fil, den GUI:t pekar ut. Det räckte när
filerna kom en och en; efter en batchtranskribering ligger tiotals granskade
filer och väntar, och att välja dem en och en i väljaren är rent klickarbete.

Rör INTE granska/current.json: vilket memo som granskas är ditt val i GUI:t,
inte en biverkning av en batchkörning.

Idempotent: en applicerad fil hoppas över nästa gång. Se CLAUDE.md steg b.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import korrigeringar as k

PROJECT_ROOT = Path(__file__).resolve().parent


def ladda(namn: str):
    """Ladda en modul vars filnamn innehåller bindestreck. `import
    applicera-corrections` går inte; filnamnen följer projektets namnkonvention
    och byts inte för Pythons skull."""
    spec = importlib.util.spec_from_file_location(
        namn.replace("-", "_"), PROJECT_ROOT / f"{namn}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ap = ladda("applicera-corrections")


def las(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def kollisioner(filer: list[Path]) -> dict[str, list[Path]]:
    """Transkript som delar filnamnsstam över temamappar.

    granska/state/ är PLATT (index.php tar basename), så två memon med samma
    stam delar arbetskopia — då skulle den enas beslut skrivas in i den andra.
    Finns inte i arkivet idag, men en batch som skriver tio filer i rad ska inte
    vara det som upptäcker det."""
    per_stam: dict[str, list[Path]] = defaultdict(list)
    for p in filer:
        per_stam[p.stem].append(p)
    return {s: ps for s, ps in per_stam.items() if len(ps) > 1}


def upptack(cfg: dict, root: Path, *, igen: bool, tillat_ogranskade: bool,
            aven_tomma: bool) -> tuple[list[tuple[Path, Path]], Counter, list[str]]:
    """(kö, skäl till överhopp, anmärkningar).

    Kön är par av (transkript, sidecar) — sidecaren väljs här och skickas med
    till apply, så att det som rapporteras och det som appliceras garanterat är
    samma fil."""
    status = k.las_status(cfg)
    alla = [p for p in k.iter_transkript(root) if p.is_file()]

    skal: Counter = Counter()
    anm: list[str] = []

    krock = kollisioner(alla)
    for stam, ps in krock.items():
        anm.append(f"FEL: stammen {stam} finns i flera temamappar — arbetskopian i "
                   "granska/state/ är delad, båda hoppas över: "
                   + ", ".join(p.relative_to(root).as_posix() for p in ps))
    krockande = {p for ps in krock.values() for p in ps}

    ko: list[tuple[Path, Path]] = []
    for p in sorted(alla):
        if p in krockande:
            skal["stamkollision"] += 1
            continue

        # Märkningen går före allt annat: en transkription du underkänt ska
        # aldrig få beslut inskrivna, hur färdiggranskad den än är.
        st = k.status_for(cfg, p, status)
        if st and st.get("status") == "ny-transkription":
            skal["märkta 'ny transkription behövs'"] += 1
            continue

        sidecar, _runda, _var = k.valj_sidecar(p)
        if sidecar is None:
            skal["ej flaggade"] += 1
            continue

        side = las(sidecar)
        if not k.antal_operationer(side) and not aven_tomma:
            skal["utan operationer"] += 1
            continue

        data = las(p)
        if data.get("corrections_applied_at") and not igen:
            skal["redan applicerade"] += 1
            continue

        kvar = k.antal_ogranskade(side)
        if kvar and not tillat_ogranskade:
            skal["ogranskade"] += 1
            if kvar <= 2:
                anm.append(f"  nästan klar: {p.relative_to(root).as_posix()} "
                           f"— {kvar} flagga(or) kvar")
            continue

        ko.append((p, sidecar))

    # Nyaste granskning först: --antal=N ska betyda "de N jag nyss granskade".
    ko.sort(key=lambda par: par[1].stat().st_mtime, reverse=True)
    return ko, skal, anm


def sammanfatta(u) -> str:
    """Beslutsraden för en fil, bara det som faktiskt förekom."""
    namn = [("replace", "ersättning", "ersättningar"), ("accept", "bekräftat", "bekräftade"),
            ("delete", "radering", "raderingar"), ("fras", "fras", "fraser"),
            ("infogning", "infogning", "infogningar")]
    delar = [f"{u.tillampat[nyckel]} {ental if u.tillampat[nyckel] == 1 else flertal}"
             for nyckel, ental, flertal in namn if u.tillampat[nyckel]]
    return ", ".join(delar) or "inga ändringar"


def main() -> int:
    cfg = k.load_config()
    root = Path(cfg["data"]["root"])

    dry_run = "--dry-run" in sys.argv
    igen = "--igen" in sys.argv
    tillat_ogranskade = "--tillat-ogranskade" in sys.argv
    aven_tomma = "--aven-tomma" in sys.argv
    antal = None
    explicit: list[str] = []
    for a in sys.argv[1:]:
        if a.startswith("--antal="):
            antal = int(a.split("=", 1)[1])
        elif a.startswith("--"):
            continue
        else:
            explicit.append(a)

    anm: list[str] = []
    if explicit:
        # Namngivna filer: du har valt dem med flit, så kön filtreras inte. Men
        # vakterna inne i applicera_en gäller fortfarande, och en märkt fil
        # varnas det för — precis som i enfilsskriptet.
        ko = []
        status = k.las_status(cfg)
        for a in explicit:
            p = Path(a)
            p = p if p.is_absolute() else (root / a)
            if not p.is_file():
                print(f"Saknas, hoppar över: {p}", file=sys.stderr)
                continue
            if not k.ar_transkript(p.name):
                print(f"Inte ett transkript, hoppar över: {p.name}", file=sys.stderr)
                continue
            sidecar, _r, _v = k.valj_sidecar(p)
            if sidecar is None:
                print(f"Ingen sidecar, hoppar över: {p.name}", file=sys.stderr)
                continue
            st = k.status_for(cfg, p, status)
            if st and st.get("status") == "ny-transkription":
                print(f"VARNING: {p.name} är märkt 'ny transkription behövs'",
                      file=sys.stderr)
            ko.append((p, sidecar))
        skal: Counter = Counter()
    else:
        ko, skal, anm = upptack(cfg, root, igen=igen,
                                tillat_ogranskade=tillat_ogranskade,
                                aven_tomma=aven_tomma)

    if antal is not None:
        ko = ko[:antal]

    for rad in anm:
        print(rad, file=sys.stderr)

    if not ko:
        print("Inget att applicera — inga färdiggranskade filer väntar.")
        if skal:
            print("Överhoppade: " + ", ".join(f"{v} {n}" for n, v in skal.most_common()))
        return 0

    print(f"=== Batch steg 4 (apply): {len(ko)} fil(er) ===")
    for i, (p, sidecar) in enumerate(ko, 1):
        var = "arbetskopia" if sidecar.parent.name == "state" else "frö i datamappen"
        print(f"  {i}. {p.relative_to(root).as_posix()}   ({var})")
    if skal:
        print("Hoppar över: " + ", ".join(f"{v} {n}" for n, v in skal.most_common()))
    print()

    klara = 0
    totalt: Counter = Counter()
    nya_backuper = 0
    inaktuell_md: list[Path] = []

    for i, (p, sidecar) in enumerate(ko, 1):
        print(f"[{i}/{len(ko)}] {p.name}")
        try:
            u = ap.applicera_en(p, sidecar_path=sidecar, torrkorning=dry_run)
        except ap.ApplyFel as e:
            print(f"       HOPPAR ÖVER: {e}", file=sys.stderr)
            if e.atgard:
                print(f"       {e.atgard}", file=sys.stderr)
            continue
        except OSError as e:
            # Skrivfel är inte ett filproblem utan ett diskproblem: full disk,
            # Dropbox-lås, borttagen mapp. Att köra vidare skulle upprepa felet
            # för varje återstående fil.
            print(f"       AVBRYTER: skrivfel på {p.name}: {e}", file=sys.stderr)
            return 1
        except Exception as e:                                  # noqa: BLE001
            print(f"       HOPPAR ÖVER: {type(e).__name__}: {e}", file=sys.stderr)
            continue

        print(f"       runda {u.runda} · {u.sidecar_var} · källa {u.kalla.name}")
        print(f"       {sammanfatta(u)}")
        print(f"       ord {u.ord_fore} -> {u.ord_efter}, "
              f"segment {u.segment_fore} -> {u.segment_efter}")
        kvarlamnat = u.tillampat.get("osaker", 0) + u.tillampat.get("pending", 0)
        if kvarlamnat or u.olosta or u.otolkade:
            print(f"       lämnat orört: {kvarlamnat} ord"
                  + (f", {u.olosta} flagga(or) utan ordindex" if u.olosta else "")
                  + (f", {u.otolkade} otolkad(e) rad(er)" if u.otolkade else ""))
        print(f"       {u.bak_meddelande}")

        klara += 1
        totalt.update(u.tillampat)
        if "skapade" in u.bak_meddelande or "skulle skapa" in u.bak_meddelande:
            nya_backuper += 1
        if p.with_suffix(".md").is_file():
            inaktuell_md.append(p)

    print()
    if dry_run:
        print(f"=== --dry-run: {klara}/{len(ko)} filer skulle appliceras "
              "(inget skrevs) ===")
    else:
        print(f"=== Klart: {klara}/{len(ko)} filer ===")
    print(f"Tillämpat: replace {totalt['replace']}, accept {totalt['accept']}, "
          f"delete {totalt['delete']}, fras {totalt['fras']}, "
          f"infogning {totalt['infogning']}")
    print(f"Backup: {nya_backuper} ny(a) -bak.json")
    if inaktuell_md:
        # .md:n är ett derivat av JSON:en och blir inaktuell i samma stund
        # texten ändras. Ingenting annat upptäcker det — negationsvakten
        # jämför .md mot JSON och skulle antingen larma falskt eller dölja
        # en verklig ändring.
        print("Kör om steg c för dessa — deras .md är nu inaktuell:")
        for p in inaktuell_md:
            print(f"   {p.relative_to(root).as_posix()}")
    return 0 if klara == len(ko) else 1


if __name__ == "__main__":
    raise SystemExit(main())
