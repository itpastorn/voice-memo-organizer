"""Döp om ett memo — ljud, transkript, allt härlett och varje pekare.

    byt-namn.py <gammal-stam> <ny-stam>              gör det
    byt-namn.py <gammal-stam> <ny-stam> --dry-run    visa planen först

Till skillnad från `radera.py` och `synka-namn.py` är **`--dry-run` inte
standard**: ingenting går förlorat av ett namnbyte, och kontrollen sker ändå
innan första filen rörs. Blir namnet ändå fel är åtgärden att köra skriptet en
gång till med rätt namn.

**Det här är `synka-namn.py` baklänges.** Där lagar skriptet i efterhand det som
blev föräldralöst när Lars bytt namn på ljudet för hand; här sker bytet och
lagningen i ett svep, så fönstret där arkivet är inkonsekvent aldrig uppstår.
Maskineriet är därför synka-namns — `Namnbyte`, `kontrollera`, `genomfor`,
`planera_falt`, `tillampa_falt`, `synka_gui` — och inte en andra uppsättning
regler som kan hamna i otakt med den första.

**Kollisionskontroll före allt annat.** Den nya stammen måste vara ledig i
*hela* arkivet, inte bara i mappen: `granska/state/` är platt, så två memon med
samma stam skulle få sina sidecars att skriva över varandra. Se namnvakten.

Se CLAUDE.md.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import korrigeringar as k

LOGGFIL = k.PROJECT_ROOT / "logs" / "arkivlogg.log"




ladda = k.ladda          # delad byggsten, se korrigeringar.py


sn = ladda("synka-namn")


def logga(rad: dict) -> None:
    """Samma arkivlogg som raderingarna skriver i — en medveten ändring ska
    gå att skilja från en bugg, och ett namnbyte är precis en sådan ändring."""
    LOGGFIL.parent.mkdir(parents=True, exist_ok=True)
    delar = [datetime.now().isoformat(timespec="seconds"), "handelse=namnbyte"]
    delar += [f"{n}={v}" for n, v in rad.items()]
    with LOGGFIL.open("a", encoding="utf-8") as f:
        f.write("  ".join(delar) + "\n")


def main() -> int:
    args = sys.argv[1:]
    torr = "--dry-run" in args
    okanda = [a for a in args if a.startswith("--") and a != "--dry-run"]
    if okanda:
        print(f"FEL: okänd flagga {okanda[0]!r}.", file=sys.stderr)
        return 2
    fria = [a for a in args if not a.startswith("--")]
    if len(fria) != 2:
        print("Anrop:  byt-namn.py <gammal-stam> <ny-stam> [--dry-run]",
              file=sys.stderr)
        return 2

    cfg = k.load_config()
    root = Path(cfg["data"]["root"])
    gammal = k.normalize_stem(fria[0].removesuffix(".json"))
    ny = k.normalize_stem(fria[1].removesuffix(".json"))

    if not ny:
        print(f"FEL: {fria[1]!r} blir tomt efter normalisering.", file=sys.stderr)
        return 2
    if ny != fria[1].removesuffix(".json"):
        print(f"Normaliserat enligt File Naming Convention: "
              f"{fria[1]!r} -> {ny!r}")
    if ny == gammal:
        print(f"FEL: {ny!r} är redan namnet.", file=sys.stderr)
        return 2

    mapp, filer = k.hitta_memo(root, gammal)
    if mapp is None and not filer:
        print(f"FEL: hittar inget som heter {gammal!r} i arkivet.", file=sys.stderr)
        return 2
    if mapp is None:
        print(f"FEL: {gammal!r} finns i fler än en mapp — reda ut det först:",
              file=sys.stderr)
        for q in filer:
            print(f"  {q.relative_to(root).as_posix()}", file=sys.stderr)
        return 2

    # Två ljudfiler på samma stam är namnvaktens spärrfall: ljud_for() kan inte
    # avgöra vilken inspelning texten kommer ur, så pekarfälten går inte att
    # laga. Byt inte namn på något som ändå inte kan bli konsekvent.
    ljud = [q for q in filer if q.suffix.lower() in k.LJUDANDELSER]
    if len(ljud) > 1:
        print(f"FEL: {gammal!r} har {len(ljud)} ljudfiler "
              f"({', '.join(q.suffix for q in ljud)}). Namnvakten spärrar det, "
              f"och pekarfälten kan inte lagas förrän en av dem är borta.",
              file=sys.stderr)
        return 2

    # Kollisionen: den nya stammen måste vara ledig i HELA arkivet.
    krock_mapp, krock = k.hitta_memo(root, ny)
    if krock:
        var = (krock_mapp.relative_to(root).as_posix() if krock_mapp
               else "flera mappar")
        print(f"FEL: {ny!r} är upptaget i {var}:", file=sys.stderr)
        for q in krock[:8]:
            print(f"  {q.relative_to(root).as_posix()}", file=sys.stderr)
        print("     Stammar måste vara unika i hela arkivet — granska/state/ är "
              "platt, så sidecars skulle skriva över varandra.", file=sys.stderr)
        return 2

    state_filer = sorted(sn.STATE_DIR.glob(f"{gammal}-corrections*.json"))
    plan = sn.Namnbyte(gammal_mapp=mapp, gammal_stam=gammal, ny_mapp=mapp,
                        ny_stam=ny, filer=filer, bevis="utpekat för hand",
                        state_filer=state_filer)

    print(f"Döper om i {mapp.relative_to(root).as_posix()}/")
    print(f"  {gammal}  ->  {ny}")
    print()
    for q in filer:
        print(f"  {q.name}  ->  {sn.nytt_namn(q, gammal, ny)}")
    for q in state_filer:
        print(f"  granska/state/{q.name}  ->  {sn.nytt_namn(q, gammal, ny)}")

    try:
        sn.kontrollera(plan)
    except sn.SynkFel as e:
        print(f"\nFEL: {e}", file=sys.stderr)
        if e.atgard:
            print(f"     {e.atgard}", file=sys.stderr)
        return 1

    if torr:
        print()
        print(f"--dry-run: ingenting ändrat. Kör utan flaggan för att göra det.")
        return 0

    gjorda = sn.genomfor(plan)
    print()
    print(f"{len(gjorda)} filer namnändrade.")

    # Pekarfälten lagas EFTER namnbytet, med synka-namns egna regler: bara
    # stammen byts, suffixet härleds aldrig om. Svepet scopas till det här
    # memot — hela arkivet behöver inte läsas om för ett namnbyte.
    ny_json = mapp / f"{ny}.json"
    fixar = sn.planera_falt(root, [ny_json] if ny_json.is_file() else [])
    for fix in fixar:
        sn.tillampa_falt(fix)
        vem = fix.fil.name if fix.fil.parent == mapp else f"state/{fix.fil.name}"
        print(f"  fält  {vem}: {fix.falt}  {fix.gammalt!r} -> {fix.nytt!r}")

    gui = sn.synka_gui(root, [plan])
    for rad in gui:
        print(f"  GUI   {rad}")

    kvar = k.familj_i(mapp, gammal)
    if kvar:
        print("\nOFULLSTÄNDIGT — kvar under gamla namnet:", file=sys.stderr)
        for q in kvar:
            print(f"  {q.name}", file=sys.stderr)

    logga({"fran": gammal, "till": ny,
           "mapp": mapp.relative_to(root).as_posix(),
           "filer": len(gjorda), "falt": len(fixar), "gui": len(gui),
           "ofullstandig": len(kvar) or "-"})
    print(f"Loggat i {LOGGFIL.relative_to(k.PROJECT_ROOT).as_posix()}")
    return 1 if kvar else 0


if __name__ == "__main__":
    raise SystemExit(main())
