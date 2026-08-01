"""Negationsvakt (issue #4): deterministisk kontroll av steg c-utdatan.

Steg c:s risk är att städningen gör tappade negationer OSYNLIGA — flytande text
ser rätt ut. En LLM som granskar sitt eget utflöde ger falsk trygghet; detta är
i stället en billig, deterministisk jämförelse utan API-anrop:

- Läser den rättade <stem>.json (sanningskällan) och <stem>.md (steg c).
- Delar .md:n i block via tidsstämpelraderna **[hh:mm:ss]**; varje block täcker
  [blockstart, nästa blockstart).
- Räknar negationsorden per block i .md respektive i JSON-segmenten i samma
  tidsfönster. Avvikelse => rad med tidsstämpel och båda räkningarna.

Falska positiva är acceptabla (städning får slå ihop meningar, och block som
raderats som icke-innehåll ger avvikelser) — poängen är att varje
polaritetsändring ska kräva ett medvetet mänskligt beslut. Exit-kod 1 vid
avvikelser (CI-vänligt), 0 om allt stämmer. Idempotent. Se CLAUDE.md steg c.
"""

from __future__ import annotations

import json
import re
import sys

import korrigeringar as k

NEGATIONER = {"inte", "aldrig", "ingen", "inget", "inga", "utan", "icke"}

TS_RE = re.compile(r"^\*\*\[(\d{2}):(\d{2}):(\d{2})\]\*\*\s*$")
ORD_RE = re.compile(r"[a-zåäöé]+", re.IGNORECASE)
RUBRIK_RE = re.compile(r"^\s*#{1,6}\s")


def rakna(text: str) -> int:
    """Negationsord i texten. Rubrikrader (## ...) räknas INTE: de är steg c:s
    egna formuleringar, inte Lars ord, så de har ingen motsvarighet i källan.
    Dessutom skriver forbattra.py rubriken FÖRE nästa blocks tidsstämpel, så en
    rubrik hamnar i föregående block och skulle ge falskt utslag där."""
    rader = [r for r in text.splitlines() if not RUBRIK_RE.match(r)]
    ord_ = ORD_RE.findall("\n".join(rader).lower())
    return sum(1 for w in ord_ if w in NEGATIONER)


def md_block(md_text: str) -> list[tuple[float, str]]:
    """(starttid i sekunder, blocktext) per **[hh:mm:ss]**-markör i .md-filen."""
    block: list[tuple[float, str]] = []
    tid = None
    rader: list[str] = []
    for rad in md_text.splitlines():
        m = TS_RE.match(rad.strip())
        if m:
            if tid is not None:
                block.append((tid, "\n".join(rader)))
            h, mi, s = (int(g) for g in m.groups())
            tid = h * 3600 + mi * 60 + s
            rader = []
        elif tid is not None:
            rader.append(rad)
    if tid is not None:
        block.append((tid, "\n".join(rader)))
    return block


def main() -> int:
    cfg = k.load_config()
    json_path = k.json_path_for(cfg)
    md_path = json_path.with_suffix(".md")

    if not json_path.is_file():
        print(f"FEL: JSON saknas: {json_path}", file=sys.stderr)
        return 2
    if not md_path.is_file():
        print(f"FEL: markdown saknas: {md_path} — kör forbattra.py (steg c) först.",
              file=sys.stderr)
        return 2

    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    if not segments:
        print("FEL: JSON saknar segment.", file=sys.stderr)
        return 2

    block = md_block(md_path.read_text(encoding="utf-8"))
    if not block:
        print("FEL: inga **[hh:mm:ss]**-markörer i markdown-filen.", file=sys.stderr)
        return 2

    avvikelser = 0
    for i, (start, text) in enumerate(block):
        # Blockets tidsfönster: [blockstart, nästa blockstart). Sista blocket
        # sträcker sig till ljudets slut. Markörens tid är avrundad till hel
        # sekund, så fönstret vidgas 0,5 s åt vänster för att inte tappa ordet
        # i skarven.
        t0 = start - 0.5
        t1 = (block[i + 1][0] - 0.5) if i + 1 < len(block) else float("inf")
        kalla = " ".join(s.get("text", "") for s in segments
                         if s.get("start") is not None and t0 <= s["start"] < t1)
        n_kalla = rakna(kalla)
        n_md = rakna(text)
        if n_kalla != n_md:
            avvikelser += 1
            h, rest = divmod(int(start), 3600)
            mi, s_ = divmod(rest, 60)
            print(f"[{h:02d}:{mi:02d}:{s_:02d}]  källa {n_kalla} negation(er), "
                  f"md {n_md} — kontrollera blocket")

    print()
    print(f"Jämförde {len(block)} block i {md_path.name} mot {json_path.name}")
    if avvikelser:
        print(f"{avvikelser} block avviker — varje polaritetsändring ska vara ett "
              "medvetet beslut. Falska positiva förekommer (ihopslagna meningar, "
              "raderat icke-innehåll).")
        return 1
    print("Inga avvikelser i negationsord.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
