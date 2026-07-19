"""Steg b, percentil-varianten: flaggning av oklarheter utifrån ord-konfidens.

Läser Whisper-JSON:en (sanningskällan från steg a) och skriver en
'<namn>-corrections.txt' bredvid den. Flaggar de 'flag_percentile' % av orden som
har lägst probability. Kräver ingen API-nyckel — billig, offline, deterministisk.

OBS: på kb-whisper-medium korrelerar ord-konfidens bara löst med faktiska fel
(egennamn/låneord blir tryggt fel). LLM-detektorn (flagga-llm.py) träffar den
felklassen bättre. Den här varianten är kvar som snabb offline-jämförelse.

Format och kontextfönster bor i korrigeringar.py. Se CLAUDE.md.
"""

from __future__ import annotations

import json
import sys

import korrigeringar as k


def flagged_indices(words: list[dict], percentile: float) -> tuple[list[int], float]:
    """Flagga de 'percentile' % av orden som har lägst probability. Returnerar
    (index, den faktiska sannolikhetsgränsen) — gränsen loggas så att man ser
    var skalan hamnade för just den här filen och modellen."""
    probs = sorted(
        w["probability"] for w in words if w["probability"] is not None
    )
    if not probs:
        return [], 0.0
    n = max(1, round(len(probs) * percentile / 100))
    cutoff = probs[n - 1]
    idx = [
        i for i, w in enumerate(words)
        if w["probability"] is not None and w["probability"] <= cutoff
    ]
    return idx, cutoff


def main() -> int:
    cfg = k.load_config()
    json_path = k.json_path_for(cfg)

    if not json_path.is_file():
        print(f"FEL: JSON saknas: {json_path}", file=sys.stderr)
        print("Kör transkribera.py först (steg a).", file=sys.stderr)
        return 1

    out_path = json_path.with_name(f"{json_path.stem}-corrections.txt")
    if out_path.exists():
        print(f"FEL: {out_path.name} finns redan.", file=sys.stderr)
        print("Vägrar skriva över — den kan innehålla dina handredigeringar.",
              file=sys.stderr)
        print("Ta bort eller döp om filen och kör igen.", file=sys.stderr)
        return 1

    data = json.loads(json_path.read_text(encoding="utf-8"))
    words = k.flatten_words(data.get("segments", []))
    if not words:
        print("FEL: JSON saknar ord-nivå-tidsstämplar.", file=sys.stderr)
        return 1

    ccfg = cfg["corrections"]
    percentile = float(ccfg["flag_percentile"])
    flagged, cutoff = flagged_indices(words, percentile)

    if not flagged:
        print(f"Inga ord att flagga i {json_path.name}.")
        print(f"Totalt {len(words)} ord.")
        return 0

    clusters = k.cluster(flagged, int(ccfg["merge_gap"]))
    text = k.build_file(
        words, clusters,
        int(ccfg["context_min_words"]), int(ccfg["context_max_words"]),
    )
    out_path.write_text(text, encoding="utf-8")

    print(f"Läste:   {json_path}")
    print(f"Ord:     {len(words)} totalt, {len(flagged)} flaggade "
          f"({100 * len(flagged) / len(words):.1f}%, lägsta {percentile:.0f}%)")
    print(f"Gräns:   probability <= {cutoff:.3f}")
    print(f"Block:   {len(clusters)}")
    print(f"Skrev:   {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
