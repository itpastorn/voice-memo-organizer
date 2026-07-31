"""Migrering: befintlig handredigerad '-corrections.txt' -> strukturerad sidecar.

Textformatet (se korrigeringar.py) klarar bara en sorts korrigering rent —
ord-ersättning med ankare. Lars har sträckt det till fyra sorter (infogning,
hel fras, eget tillägg, namn utan förslag) och en rad har gått sönder i syntaxen.
Det här skriptet räddar de två timmars handgranskning som redan gjorts i .txt:en
in i en '-corrections.json' som webb-GUI:t (granska/) läser och skriver beslut till.

Best-effort + ärlig rapport: allt som inte kan tolkas entydigt (fraser, infogningar,
ordrader utan ankare, trasiga rader) listas med radnummer — inget slängs tyst.

Ankaret är ordets starttid formaterad som i korrigeringar.format_anchor (@{start:.2f}).
Samma sträng används som nyckel tillbaka till det globala ordindexet i JSON:en, så
apply-steget och GUI:t pekar garanterat på samma ord.

Skriver också granska/current.json (filnamn GUI:t ska öppna) och granska/.env
(DATA_ROOT ur config.toml) så 'docker compose up' i granska/ bara funkar.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import korrigeringar as k

PROJECT_ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Tolkning av kommentaren '# gissning? (skäl)'
# --------------------------------------------------------------------------- #

def parse_comment(comment: str) -> tuple[str, str]:
    """'Gunther? (förvanskat namn)' -> ('Gunther', 'förvanskat namn').
    Saknad gissning eller skäl ger tom sträng."""
    c = comment.strip()
    reason = ""
    m = re.search(r"\(([^()]*)\)\s*$", c)
    if m:
        reason = m.group(1).strip()
        c = c[: m.start()].strip()
    guess = c[:-1].strip() if c.endswith("?") else (c.split("?", 1)[0].strip() if "?" in c else c.strip())
    return guess, reason


def strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


# --------------------------------------------------------------------------- #
# Tolkning av .txt:en, block för block
# --------------------------------------------------------------------------- #

ANCHOR_RE = re.compile(r"@(\d+(?:\.\d+)?)")
INSERT_RE = re.compile(r"\[([^\]]+)\]")


def build_anchor_map(words: list[dict]) -> dict[str, list[int]]:
    """Ankarsträng (samma format som .txt:en skrevs med) -> globala ordindex."""
    m: dict[str, list[int]] = {}
    for i, w in enumerate(words):
        if w["start"] is None:
            continue
        m.setdefault(f"{w['start']:.2f}", []).append(i)
    return m


def iter_blocks(lines: list[str]):
    """Gruppera rader i block avgränsade av tomrader. Ger listor av (radnr, text)."""
    block: list[tuple[int, str]] = []
    for n, raw in enumerate(lines, start=1):
        if raw.strip() == "":
            if block:
                yield block
                block = []
        else:
            block.append((n, raw))
    if block:
        yield block


def migrate(words: list[dict], txt: str) -> dict:
    anchor_map = build_anchor_map(words)
    flags: list[dict] = []
    phrase_edits: list[dict] = []
    insertions: list[dict] = []
    review: list[dict] = []  # rader som inte kunde tolkas entydigt

    for block in iter_blocks(txt.splitlines()):
        context_lines = [(n, t) for n, t in block if t.lstrip().startswith('"context":')]
        block_context = ""
        if context_lines:
            m = re.search(r'"context":\s*"(.*)"\s*$', context_lines[0][1])
            block_context = m.group(1) if m else context_lines[0][1]
            # Fler än en kontextrad = Lars omskrivna kontext (en frasrättelse).
            for n, t in context_lines[1:]:
                mm = re.search(r'"context":\s*"(.*)"\s*$', t)
                phrase_edits.append({"line": n, "kind": "omskriven-kontext",
                                     "text": mm.group(1) if mm else t.strip(),
                                     "block_context": block_context})
            # Infogningar: ord inom [ ] i kontextraden.
            for ins in INSERT_RE.findall(block_context):
                insertions.append({"line": context_lines[0][0], "word": ins,
                                   "block_context": block_context})

        for n, t in block:
            s = t.strip()
            if s.startswith('"context":'):
                continue
            if s.lower().startswith("frasen"):
                rhs = re.split(r"[=:]", s, maxsplit=1)
                phrase_edits.append({"line": n, "kind": "fras",
                                     "text": strip_quotes(rhs[1]) if len(rhs) > 1 else s,
                                     "block_context": block_context})
                continue
            if "=" not in s:
                review.append({"line": n, "raw": s, "why": "ingen '=' — okänt radformat"})
                continue

            # Ordrad: HEARD=RHS  [@ankare]  [# kommentar]
            comment = ""
            cm = re.search(r"\s+#\s?(.*)$", s)
            body = s
            if cm:
                comment = cm.group(1)
                body = s[: cm.start()]
            am = ANCHOR_RE.search(body)
            anchor = am.group(1) if am else None
            assign = (body[: am.start()] if am else body).rstrip()
            left, _, rhs = assign.partition("=")
            heard = left.strip()
            rhs = rhs.strip()
            guess, reason = parse_comment(comment)

            if anchor is None:
                review.append({"line": n, "raw": s, "heard": heard,
                               "rhs": rhs, "why": "ordrad utan ankare"})
                continue

            idxs = anchor_map.get(anchor, [])
            gi = None
            note = ""
            if not idxs:
                note = "ankaret finns inte i JSON"
            elif len(idxs) == 1:
                gi = idxs[0]
            else:
                match = [i for i in idxs if words[i]["word"].strip() == strip_quotes(heard)]
                gi = match[0] if match else idxs[0]
                note = "flera ord delar ankare" + ("" if match else " (ingen ordmatch)")

            verify_ok = gi is not None and words[gi]["word"].strip() == strip_quotes(heard)

            rhs_clean = strip_quotes(rhs)
            if rhs == "":
                decision, replacement = "pending", ""
            elif rhs == "DELETE":
                decision, replacement = "delete", ""
            elif rhs_clean == strip_quotes(heard):
                decision, replacement = "accept", ""
            else:
                decision, replacement = "replace", rhs_clean

            flags.append({
                "anchor": float(anchor),
                "global_index": gi,
                "heard": heard,
                "verify_ok": verify_ok,
                "ai_guess": guess,
                "ai_reason": reason,
                "decision": decision,
                "replacement": replacement,
                "note": note,
                "line": n,
            })

    return {"flags": flags, "phrase_edits": phrase_edits,
            "insertions": insertions, "review": review}


# --------------------------------------------------------------------------- #
# Utskrift
# --------------------------------------------------------------------------- #

def main() -> int:
    cfg = k.load_config()
    json_path = k.json_path_for(cfg)
    txt_path = json_path.with_name(f"{json_path.stem}-corrections.txt")
    sidecar_path = json_path.with_name(f"{json_path.stem}-corrections.json")

    if not json_path.is_file():
        print(f"FEL: JSON saknas: {json_path}", file=sys.stderr)
        return 1
    if not txt_path.is_file():
        print(f"FEL: corrections-txt saknas: {txt_path}", file=sys.stderr)
        return 1

    data = json.loads(json_path.read_text(encoding="utf-8"))
    words = k.flatten_words(data.get("segments", []))
    if not words:
        print("FEL: JSON saknar ord-nivå-tidsstämplar.", file=sys.stderr)
        return 1

    result = migrate(words, txt_path.read_text(encoding="utf-8"))

    sidecar = {
        "audio_file": data.get("audio_file") or f"{json_path.stem}.m4a",
        "transcript_json": json_path.name,
        "source": "migrera-corrections.py",
        "word_count": len(words),
        **result,
    }
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # Pekare + Docker-miljö åt granska/-GUI:t.
    granska = PROJECT_ROOT / "granska"
    granska.mkdir(exist_ok=True)
    # Sökvägarna är relativa datamappens rot — ljudet ligger i temamappar och
    # GUI:t monterar roten som /data. Arbetskopian i state/ hålls däremot platt
    # (PHP tar basename), precis som applicera-corrections.py förutsätter.
    audio_path = json_path.with_name(sidecar["audio_file"])
    (granska / "current.json").write_text(json.dumps({
        "stem": json_path.stem,
        "transcript_json": k.rel_to_root(cfg, json_path),
        "sidecar_json": k.rel_to_root(cfg, sidecar_path),
        "audio_file": k.rel_to_root(cfg, audio_path),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (granska / ".env").write_text(f"DATA_ROOT={cfg['data']['root']}\n", encoding="utf-8")

    # --- Rapport ---
    flags = result["flags"]
    from collections import Counter
    dec = Counter(f["decision"] for f in flags)
    unresolved = [f for f in flags if f["global_index"] is None]
    misverify = [f for f in flags if f["global_index"] is not None and not f["verify_ok"]]

    print(f"Läste:   {txt_path.name}")
    print(f"Skrev:   {sidecar_path}")
    print()
    print(f"Ordflaggor: {len(flags)}")
    print(f"  beslut:  {dict(dec)}")
    print(f"  ankare löst mot JSON: {len(flags) - len(unresolved)}/{len(flags)}"
          f"  (verifierade mot rätt ord: {sum(f['verify_ok'] for f in flags)})")
    print(f"Fraser (needs-review):      {len(result['phrase_edits'])}")
    print(f"Infogningar (needs-review): {len(result['insertions'])}")
    print(f"Otolkade rader:             {len(result['review'])}")

    if unresolved:
        print("\nOLÖSTA ANKARE:")
        for f in unresolved:
            print(f"  rad {f['line']}: {f['heard']} @{f['anchor']}  ({f['note']})")
    if misverify:
        print("\nANKARE PEKAR PÅ FEL ORD (kontrollera):")
        for f in misverify:
            gi = f["global_index"]
            print(f"  rad {f['line']}: .txt säger '{f['heard']}' men JSON[{gi}]="
                  f"'{words[gi]['word'].strip()}'  ({f['note']})")
    if result["review"]:
        print("\nOTOLKADE RADER (bevarade, tas i GUI:t):")
        for r in result["review"]:
            print(f"  rad {r['line']}: {r['raw']}   <- {r['why']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
