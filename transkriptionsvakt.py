"""Transkriptioner som inte duger över huvud taget.

    transkriptionsvakt.py            vad som är trasigt
    transkriptionsvakt.py --alla     visa måtten för varje fil, även de rena
    transkriptionsvakt.py <fil.json> bara den filen

Exit-kod 1 när något omärkt problem finns, annars 0.

Vakten bedömer **hela filen**, aldrig enskilda ord — det är detektorns jobb
(steg b). Frågan här är den motsatta: är det här över huvud taget en
transkription av det som sägs i ljudet?

Whisper kan fastna i en upprepningsloop och skriva flytande nonsens: *"Jag
tackar för mig. Jag tackar för mig själv. Jag tackar för mig."* i elva minuter.
Ingenting nedströms fångar det. Texten är välformad, så granskningen ser inget
konstigt, och detektorn letar efter felhörda ord — inte efter att filen saknar
innehåll.

Vakten **rättar inget**. Den pekar ut filer att lyssna på. Duger inspelningen
inte, märk den i GUI:t ("Märk: ny transkription behövs") — då hoppar
`batch-flagga.py` över den i stället för att betala för att flagga nonsens.

Trösklarna och varför de ligger där de ligger: se `korrigeringar`, avsnittet
"Är transkriptionen användbar över huvud taget?".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import korrigeringar as k


def granska_en(cfg: dict, p: Path, status: dict) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fil": p, "matt": None, "problem": ["går inte att läsa"], "markt": None}
    matt = k.transkriptionsmatt(data)
    st = k.status_for(cfg, p, status)
    return {
        "fil": p,
        "matt": matt,
        "problem": k.transkriptionsproblem(matt),
        "markt": (st or {}).get("status"),
        "modell": (data.get("model") or "?").split("-")[-1],
    }


def main() -> int:
    cfg = k.load_config()
    root = Path(cfg["data"]["root"])
    status = k.las_status(cfg)

    visa_alla = "--alla" in sys.argv
    explicit = [a for a in sys.argv[1:] if not a.startswith("--")]
    if explicit:
        filer = []
        for a in explicit:
            q = Path(a)
            q = q if q.is_absolute() else (root / a)
            if not q.is_file():
                print(f"Saknas: {q}", file=sys.stderr)
                return 2
            filer.append(q)
    else:
        filer = sorted(k.iter_transkript(root))

    print(f"Datamapp: {root}")
    print()

    rapporter = [granska_en(cfg, p, status) for p in filer]
    trasiga = [r for r in rapporter if r["problem"]]
    omarkta = [r for r in trasiga if not r["markt"]]
    for_korta = sum(1 for r in rapporter if r["matt"] is None and not r["problem"])

    if not trasiga:
        print(f"Inga problem i {len(rapporter)} transkript "
              f"({for_korta} för korta för att bedömas).")
        return 0

    print(f"TRASIGA TRANSKRIPTIONER — {len(trasiga)} av {len(rapporter)}, "
          f"varav {len(omarkta)} ännu omärkta")
    print()
    for r in sorted(trasiga, key=lambda x: x["matt"]["ord_per_minut"] if x["matt"] else 0):
        rel = r["fil"].relative_to(root).as_posix()
        m = r["matt"]
        markt = f"   [märkt: {r['markt']}]" if r["markt"] else ""
        print(f"  {rel}{markt}")
        if m:
            print(f"      {m['minuter']:.1f} min, {m['ord']} ord  |  "
                  f"{m['ord_per_minut']:.0f} ord/min  |  "
                  f"{m['unika_segment']:.0%} unika segment  |  "
                  f"täckning {m['tackning']:.0%}  |  {r['modell']}")
        for prob in r["problem"]:
            print(f"      - {prob}")
        print()

    if visa_alla:
        print("=== Alla filer ===")
        print(f"{'fil':60s} {'min':>5s} {'ord/min':>7s} {'unika':>6s} {'täckn':>6s}")
        for r in sorted(rapporter, key=lambda x: x["matt"]["ord_per_minut"] if x["matt"] else 9e9):
            m = r["matt"]
            rel = r["fil"].relative_to(root).as_posix()
            kort = rel if len(rel) <= 60 else "…" + rel[-59:]
            if not m:
                print(f"{kort:60s} {'':>5s} {'(för kort att bedöma)':>7s}")
            else:
                print(f"{kort:60s} {m['minuter']:5.1f} {m['ord_per_minut']:7.0f} "
                      f"{m['unika_segment']:6.0%} {m['tackning']:6.0%}")
        print()

    print("Lyssna på filerna innan något görs. En upprepningsloop är ett säkert")
    print("omdöme; lågt ordtempo utan loop kan vara en människa som tänker länge")
    print("mellan meningarna.")
    print()
    print("Duger inspelningen inte: märk den i GUI:t ('Märk: ny transkription")
    print("behövs'). Då hoppar batch-flagga.py över den i stället för att betala")
    print("för att flagga nonsens.")
    return 1 if omarkta else 0


if __name__ == "__main__":
    raise SystemExit(main())
