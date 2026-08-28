"""Namnvakt över hela arkivet: vad pipelinen vägrar bearbeta, och varför.

Skripten kör vakten själva och stoppar den enskilda filen. Det här är
översikten — vad som är spärrat, vad som bara avviker, och vad som ska döpas om.

    namnvakt.py              spärrar och en sammanfattning av avvikelserna
    namnvakt.py --alla       listar också varje avvikande ljudfilnamn
    namnvakt.py --ljud       bara ljudfilerna (steg a)
    namnvakt.py --transkript bara transkripten (steg b och framåt)

Exit-kod 1 när något är spärrat, annars 0.

Konventionen (File Naming Convention i CLAUDE.md) gäller allt vi PRODUCERAR.
Ljudet är undantaget — det döps inte om utan att Lars ber om det, och
transkribera.py normaliserar stammen på väg ut. Därför är ett avvikande
ljudfilnamn en upplysning, medan en KOLLISION är ett fel: två ljudfiler med
samma normaliserade stam skriver samma .json, och den ena inspelningen kommer
aldrig in i pipelinen.

Se CLAUDE.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import korrigeringar as k


def ljudfiler(root: Path):
    """Arkivets ljudfiler, utom test/ och sammanfatta/."""
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in k.LJUDANDELSER or not p.is_file():
            continue
        if {d.lower() for d in p.relative_to(root).parts[:-1]} & k.EXCLUDE_DIRS:
            continue
        yield p


def main() -> int:
    cfg = k.load_config()
    root = Path(cfg["data"]["root"])

    visa_alla = "--alla" in sys.argv
    bara_ljud = "--ljud" in sys.argv
    bara_transkript = "--transkript" in sys.argv
    kor_ljud = not bara_transkript
    kor_transkript = not bara_ljud

    print(f"Datamapp: {root}")
    print()

    sparrar: list[tuple[str, k.NamnFel]] = []
    avvikande: list[tuple[Path, list[str]]] = []
    n_ljud = n_transkript = 0
    varningar: list[tuple[str, str]] = []

    if kor_ljud:
        # Kollisionen rapporteras en gång per grupp, inte en gång per fil: båda
        # filerna i ett par kastar samma fel med samma text.
        sedda: set[str] = set()
        for p in ljudfiler(root):
            n_ljud += 1
            rel = p.relative_to(root).as_posix()
            try:
                k.vakta_ljud(cfg, p)
            except k.NamnFel as e:
                nyckel = k.normalize_stem(p.stem)
                if nyckel not in sedda:
                    sedda.add(nyckel)
                    sparrar.append((rel, e))
                continue
            brott = k.namnbrott(p.stem)
            if brott:
                avvikande.append((p, brott))

    if kor_transkript:
        for p in k.iter_transkript(root):
            n_transkript += 1
            rel = p.relative_to(root).as_posix()
            try:
                for v in k.vakta_transkript(cfg, p):
                    varningar.append((rel, v))
            except k.NamnFel as e:
                sparrar.append((rel, e))

    if sparrar:
        print(f"SPÄRRAT — {len(sparrar)} problem som pipelinen vägrar bearbeta")
        print()
        for rel, e in sparrar:
            print(f"  {rel}")
            print(f"      {e}")
            if e.atgard:
                print(f"      Åtgärd: {e.atgard}")
            print()
    else:
        print("SPÄRRAT: inget. Alla filer går att bearbeta.")
        print()

    if varningar:
        print(f"ATT SE ÖVER — {len(varningar)} transkript")
        print()
        for rel, v in varningar:
            if "följer inte konventionen" in v:
                continue        # väntat för gamla ljudfilnamn, räknas nedan
            print(f"  {rel}")
            print(f"      {v}")
        print()

    if avvikande:
        print(f"AVVIKANDE LJUDFILNAMN — {len(avvikande)} av {n_ljud}")
        print("  Ofarligt: transkribera.py normaliserar stammen på väg ut, så")
        print("  utdata blir rätt namngivet ändå. Ljudet döps inte om av oss.")
        print()
        raknare: dict[str, int] = {}
        for _p, brott in avvikande:
            for b in brott:
                raknare[b.split(":")[0]] = raknare.get(b.split(":")[0], 0) + 1
        for orsak, antal in sorted(raknare.items(), key=lambda x: -x[1]):
            print(f"    {antal:4d}  {orsak}")
        print()
        if visa_alla:
            for p, brott in avvikande:
                print(f"  {p.relative_to(root).as_posix()}")
                print(f"      -> {k.normalize_stem(p.stem)}{p.suffix.lower()}"
                      f"   ({', '.join(brott)})")
            print()
        else:
            print("  Kör med --alla för att se dem en och en.")
            print()

    delar = []
    if kor_ljud:
        delar.append(f"{n_ljud} ljudfiler")
    if kor_transkript:
        delar.append(f"{n_transkript} transkript")
    print(f"Genomsökt: {', '.join(delar)}. "
          f"{len(sparrar)} spärrade, {len(avvikande)} avvikande namn.")
    return 1 if sparrar else 0


if __name__ == "__main__":
    raise SystemExit(main())
