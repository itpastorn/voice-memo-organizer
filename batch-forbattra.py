"""Batch för steg c: gör läsbar markdown av alla applicerade transkriptioner.

Anrop:
    venv/Scripts/python.exe batch-forbattra.py --dry-run
        listar kön och uppskattar kostnaden. Inga API-anrop.
    venv/Scripts/python.exe batch-forbattra.py
        kör steg c på alla filer som saknar .md, och negationsvakten efter varje.
    venv/Scripts/python.exe batch-forbattra.py Kirk-.../zego-x.json ...
        kör de angivna filerna.

Flaggor: --antal=N, --igen (gör om filer som redan har .md).

Till skillnad från batch-applicera.py KOSTAR den här pengar — steg c anropar
Claude. Därför uppskattas kostnaden alltid först, och --dry-run gör inget annat.

**Negationsvakten körs direkt efter varje fil.** Den är gratis och deterministisk,
och den är hela skyddet mot steg c:s enda allvarliga risk: att städningen gör en
tappad negation osynlig. Att köra den för hand tio gånger efter en batch vore
samma klickarbete batchen finns för att slippa. Avvikelser rapporteras men
avbryter inte — falska positiva är väntade (ihopslagna meningar, raderade block).

Rör INTE granska/current.json. Idempotent: filer som redan har .md hoppas över.
Se CLAUDE.md steg c.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import korrigeringar as k

PROJECT_ROOT = Path(__file__).resolve().parent

# Uppmätt 2026-08-13 på claude-opus-4-8:
#   - 2,13 tecken per token på promptens svenska text (mätt med count_tokens över
#     23 anrop, 128k tecken). Den vanliga tumregeln ~3,3 gäller engelska och
#     underskattade kostnaden med en tredjedel här.
#   - structured output-schemat lägger ~766 tokens ovanpå varje anrop utöver
#     prompten, konstant oavsett filstorlek.
#   - utdata (inklusive adaptiv thinking) blev 0,35 respektive 0,36 av indata på
#     två filer (3,3 och 13,0 min).
# Ger ~$0,010 per ljudminut. Mät om detta om modellen eller prompten byts.
TECKEN_PER_TOKEN = 2.13
SCHEMA_OVERHEAD = 766
UT_PER_IN = 0.355


def ladda(namn: str):
    """Ladda en modul vars filnamn innehåller bindestreck."""
    spec = importlib.util.spec_from_file_location(
        namn.replace("-", "_"), PROJECT_ROOT / f"{namn}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fb = ladda("forbattra")
nv = ladda("negationsvakt")


def las(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def uppskatta(data: dict, per: int) -> tuple[int, int, float]:
    """(anrop, indata-tokens, kostnad i dollar) utan att anropa något.

    Indata approximeras ur promptens tecken. Att räkna exakt kräver ett
    count_tokens-anrop per bit — nätverk och väntan för en uppskattning som ska
    vara gratis och ögonblicklig. Konstanterna ovan är mätta mot count_tokens,
    så avvikelsen ligger inom någon procent."""
    segs = data.get("segments", [])
    if not segs:
        return 0, 0, 0.0
    anrop = max(1, -(-len(segs) // per))
    tecken = len(fb.SYSTEM) * anrop      # systemprompten skickas med varje anrop
    for s in range(0, len(segs), per):
        tecken += len(fb.build_prompt(segs, s, min(s + per, len(segs))))
    in_tok = int(tecken / TECKEN_PER_TOKEN) + SCHEMA_OVERHEAD * anrop
    ut_tok = int(in_tok * UT_PER_IN)
    return anrop, in_tok, in_tok / 1e6 * fb.PRIS_IN + ut_tok / 1e6 * fb.PRIS_UT


def upptack(cfg: dict, root: Path, *, igen: bool):
    """(kö, skäl till överhopp). Steg c kräver att besluten är inskrivna:
    en .md byggd på ogranskad text skulle bära felhörningarna vidare, och
    negationsvakten jämför mot samma JSON."""
    status = k.las_status(cfg)
    ko, skal = [], {}

    def hoppa(namn):
        skal[namn] = skal.get(namn, 0) + 1

    for p in k.iter_transkript(root):
        st = k.status_for(cfg, p, status)
        if st and st.get("status") == "ny-transkription":
            hoppa("märkta 'ny transkription behövs'")
            continue
        data = las(p)
        if not data.get("segments"):
            hoppa("utan segment")
            continue
        if not data.get("corrections_applied_at"):
            hoppa("ej applicerade")
            continue
        if p.with_suffix(".md").is_file() and not igen:
            hoppa("har redan .md")
            continue
        ko.append((p, data))

    ko.sort(key=lambda par: par[1].get("duration") or 0)   # billigast först
    return ko, skal


def main() -> int:
    cfg = k.load_config()
    root = Path(cfg["data"]["root"])
    per = int(cfg.get("forbattring", {}).get("segment_per_anrop", 90))

    dry_run = "--dry-run" in sys.argv
    igen = "--igen" in sys.argv
    antal = None
    explicit: list[str] = []
    for a in sys.argv[1:]:
        if a.startswith("--antal="):
            antal = int(a.split("=", 1)[1])
        elif a.startswith("--"):
            continue
        else:
            explicit.append(a)

    if explicit:
        ko, skal = [], {}
        for a in explicit:
            p = Path(a)
            p = p if p.is_absolute() else (root / a)
            if not p.is_file():
                print(f"Saknas, hoppar över: {p}", file=sys.stderr)
                continue
            ko.append((p, las(p)))
    else:
        ko, skal = upptack(cfg, root, igen=igen)

    if antal is not None:
        ko = ko[:antal]

    if not ko:
        print("Inget att göra — alla applicerade filer har redan .md.")
        if skal:
            print("Överhoppade: " + ", ".join(f"{v} {n}" for n, v in skal.items()))
        return 0

    kostnad_tot = 0.0
    minuter_tot = 0.0
    print(f"=== Batch steg c: {len(ko)} fil(er) ===")
    for i, (p, data) in enumerate(ko, 1):
        anrop, in_tok, kostnad = uppskatta(data, per)
        kostnad_tot += kostnad
        mi = (data.get("duration") or 0) / 60
        minuter_tot += mi
        print(f"  {i}. {p.relative_to(root).as_posix()}   "
              f"{mi:.1f} min, {anrop} anrop, ~${kostnad:.2f}")
    if skal:
        print("Hoppar över: " + ", ".join(f"{v} {n}" for n, v in skal.items()))
    print(f"Uppskattad kostnad: ~${kostnad_tot:.2f} för {minuter_tot:.0f} ljudminuter")
    if dry_run:
        print("--dry-run: inget kört.")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("FEL: ANTHROPIC_API_KEY saknas (lägg i .env).", file=sys.stderr)
        return 1
    import anthropic
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError as e:
        print(f"FEL: kunde inte skapa klient: {e}", file=sys.stderr)
        return 1

    print()
    klara = 0
    in_tot = ut_tot = 0
    med_avvikelse: list[tuple[Path, int]] = []

    for i, (p, _data) in enumerate(ko, 1):
        print(f"[{i}/{len(ko)}] {p.name}")
        try:
            u = fb.forbattra_en(client, cfg, p, tyst=True)
        except anthropic.AuthenticationError:
            # Nyckelfel gäller alla återstående filer — fortsätt inte.
            print("       AVBRYTER: ogiltig ANTHROPIC_API_KEY.", file=sys.stderr)
            return 1
        except fb.ForbattraFel as e:
            print(f"       HOPPAR ÖVER: {e}", file=sys.stderr)
            continue
        except Exception as e:                                   # noqa: BLE001
            print(f"       HOPPAR ÖVER: {type(e).__name__}: {e}", file=sys.stderr)
            continue

        klara += 1
        in_tot += u.in_tok
        ut_tot += u.ut_tok
        print(f"       {u.block} block ({u.behallna} behållna, "
              f"{u.borttagna} föreslagna för radering), {u.anrop} anrop, "
              f"~${u.kostnad:.2f}")

        try:
            antal_block, avvikelser = nv.kontrollera(p)
        except nv.VaktFel as e:
            print(f"       negationsvakt: {e}", file=sys.stderr)
            continue
        if avvikelser:
            med_avvikelse.append((p, len(avvikelser)))
            print(f"       negationsvakt: {len(avvikelser)} av {antal_block} block "
                  "avviker")
            for rad in avvikelser:
                print(f"          {rad}")
        else:
            print(f"       negationsvakt: {antal_block} block, inga avvikelser")

    kostnad = in_tot / 1e6 * fb.PRIS_IN + ut_tot / 1e6 * fb.PRIS_UT
    print()
    print(f"=== Klart: {klara}/{len(ko)} filer ===")
    print(f"Tokens: in {in_tot}, ut {ut_tot}  (~${kostnad:.2f}, "
          f"uppskattat ~${kostnad_tot:.2f})")
    if med_avvikelse:
        print("Negationsvakten flaggade — granska dessa block mot ljudet:")
        for p, n in med_avvikelse:
            print(f"   {p.relative_to(root).as_posix()}: {n} block")
        print("Falska positiva är väntade (ihopslagna meningar, raderade block).")
    else:
        print("Negationsvakten: inga avvikelser i någon fil.")
    print("Granska också <namn>-borttaget.txt: vad steg c föreslog stryka.")
    return 0 if klara == len(ko) else 1


if __name__ == "__main__":
    raise SystemExit(main())
