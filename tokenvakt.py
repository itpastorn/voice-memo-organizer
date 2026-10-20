"""Whisper-specialtoken som läckt in i transkripten.

    tokenvakt.py              vad som är drabbat, och hur farligt det är att laga
    tokenvakt.py --alla       varje träff med tidsstämpel
    tokenvakt.py <fil.json>   bara den filen

Exit-kod 1 när något hittas, annars 0.

Modellens styrtoken kan hamna i texten som om den vore tal: `<|nospeech|>` står
kvar i segmentet och följer med ut i `.json`, `.txt` och `.srt`. Den är svår att
se därför att Whisper delar den över flera ordtokens — `<`, `|nospeech`, `|`,
`>` — så varje ord för sig ser oskyldigt ut. Se `korrigeringar.specialtoken_traffar`.

Vakten **rensar inte** — det gör `rensa-token.py`, och bara i filer som
ingenting annat pekar på. Orden ingår i `global_index`, och att ta bort dem
förskjuter varje index efter träffen. I filer med sidecar, `-bak.json` eller
`.md` måste indexen skrivas om i samma svep (steg 2 av issue #12, ej byggt).

Se CLAUDE.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import korrigeringar as k


def tid(sekunder) -> str:
    if sekunder is None:
        return "  ?  "
    m, s = divmod(int(sekunder), 60)
    return f"{m:3d}:{s:02d}"


def granska_en(cfg: dict, p: Path) -> dict | None:
    """Träffar och riskbild för en fil, eller None när den är ren."""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    traffar = k.specialtoken_traffar(data.get("segments", []))
    if not traffar:
        return None

    side, _, _ = k.valj_sidecar(p)
    beslut = 0
    if side:
        try:
            s = json.loads(side.read_text(encoding="utf-8"))
            beslut = (sum(1 for f in s.get("flags", []) if f.get("reviewed"))
                      + len(s.get("phrase_edits", [])) + len(s.get("insertions", [])))
        except (OSError, json.JSONDecodeError):
            pass
    ord_totalt = sum(len(s.get("words") or []) for s in data.get("segments", []))
    tokenord = sum(len(t["ord"]) for t in traffar)
    return {
        "fil": p,
        "modell": (data.get("model") or "?").split("-")[-1],
        "traffar": traffar,
        "orena": sum(1 for t in traffar if not t["ren"]),
        "hinder": k.tokenrensning_hinder(p, data),
        "andel": tokenord / ord_totalt if ord_totalt else 0.0,
        "beslut": beslut,
        "applicerad": bool(data.get("corrections_applied_at")),
        "md": p.with_suffix(".md").is_file(),
    }


def main() -> int:
    cfg = k.load_config()
    root = Path(cfg["data"]["root"])

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

    rapporter = [r for r in (granska_en(cfg, p) for p in filer) if r]
    if not rapporter:
        print(f"Inga specialtoken i {len(filer)} transkript.")
        return 0

    tot = sum(len(r["traffar"]) for r in rapporter)
    orena = sum(r["orena"] for r in rapporter)
    riskabla = [r for r in rapporter if r["hinder"]]

    print(f"SPECIALTOKEN — {tot} träffar i {len(rapporter)} av {len(filer)} transkript")
    print()
    print(f"{'fil':66s} {'modell':6s} {'träff':>5s} {'oren':>5s} {'andel':>6s} "
          f"{'beslut':>6s} {'appl':>4s} {'md':>3s}")
    for r in sorted(rapporter, key=lambda x: -len(x["traffar"])):
        rel = r["fil"].relative_to(root).as_posix()
        risk = r["hinder"]
        kort = rel if len(rel) <= 66 else "…" + rel[-65:]
        print(f"{kort:66s} {r['modell']:6s} {len(r['traffar']):5d} "
              f"{r['orena']:5d} {r['andel']:6.1%} {r['beslut']:6d} "
              f"{'JA' if r['applicerad'] else '-':>4s} {'JA' if r['md'] else '-':>3s}"
              f"{'  <-- RÖR INTE' if risk else ''}")
        if visa_alla:
            for t in r["traffar"]:
                var = ("hela segmentet" if t["helt_segment"] else
                       "ren, mitt i tal" if t["ren"] else "OREN — ihop med tal")
                ord_ = f"ord {t['ord'][0]}–{t['ord'][-1]}" if t["ord"] else "inga ordindex"
                print(f"      {tid(t['start'])}  {t['token']!r:20s} {ord_:16s} {var}")

    print()
    print(f"**{orena} av {tot} träffar är orena** — tokenen sitter ihop med riktiga ord")
    print("('<|nospeech|>ologi,'), så en mekanisk strykning tar text med sig. De")
    print("lämnas åt en människa. Övriga går att stryka rakt av.")
    print()
    if riskabla:
        print(f"**{len(riskabla)} filer har sidecar, apply eller .md** (RÖR INTE). Orden ingår")
        print("i global_index — även ogranskade flaggor pekar på dem — så en rensning")
        print("förskjuter varje index efter träffen. De kräver steg 2 av issue #12.")
    if len(riskabla) < len(rapporter):
        print(f"{len(rapporter) - len(riskabla)} filer pekas inte på av något och kan rensas: "
              "rensa-token.py.")
    print()
    print("`andel` är hur stor del av filens ord som är tokenord. **Den duger inte")
    print("ensam som mått på en misslyckad transkription** — en kort fil får hög andel")
    print("av en enda träff. Använd den tillsammans med ord per minut.")
    print()
    print("Vakten rensar inte. Den rapporterar; rensa-token.py rensar.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
