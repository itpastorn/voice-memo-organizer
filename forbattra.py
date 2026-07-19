"""Steg c: språklig förbättring. En LLM städar transkriptionen till läsbar markdown
UTAN att skriva om Lars, och föreslår radering av material som inte hör till memot.

Läser den korrigerade <namn>.json (sanningskällan efter apply) och producerar:
    <namn>.md              — markdown med block, korta underrubriker och en
                             tidsstämpel [hh:mm:ss] per block (går att slå upp i ljudet).
    <namn>-borttaget.txt   — det LLM:n föreslog radera, med skäl, för granskning.

Två saker hålls isär (se CLAUDE.md steg c):
  1. Städa språket i memot — utfyllnadsord, falska starter, interpunktion, stycken.
     INTE omskrivning: Lars ordval och formuleringar överlever.
  2. Föreslå radering av icke-innehåll — avslutande skräp efter föredragets slut
     (publikinteraktion via hörapparater), och inskjutna avbrott (hundtilltal,
     hälsningar). Raderat samlas i sidofilen, tas inte tyst bort.

Chunkar segmenten med globala index så tidsstämplarna hämtas exakt ur JSON.
Nyckeln läses ur .env (ANTHROPIC_API_KEY). Se CLAUDE.md.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from pydantic import BaseModel, Field

import korrigeringar as k

k.load_dotenv()


# --------------------------------------------------------------------------- #
# Structured output
# --------------------------------------------------------------------------- #

class Block(BaseModel):
    first_seg: int = Field(description="Första segmentets [index] i blocket.")
    last_seg: int = Field(description="Sista segmentets [index] i blocket.")
    heading: str = Field(default="", description="Kort underrubrik om ett nytt tema börjar, annars tom sträng.")
    markdown: str = Field(default="", description="Städad text i markdown (stycken, ev. lista). Tom om remove=true.")
    remove: bool = Field(default=False, description="True om blocket inte hör till memot och ska tas bort.")
    remove_reason: str = Field(default="", description="Kort skäl om remove=true, t.ex. 'efter föredragets slut', 'hundtilltal'.")


class Resultat(BaseModel):
    blocks: list[Block]


SYSTEM = """\
Du bearbetar en automatisk transkription av Lars Gunthers röstmemon — teologiska \
föredrag och resonemang på svenska, ofta med engelska namn och begrepp. Du får \
segment i ordning, vart och ett med [index] och tid.

Din uppgift är TVÅ saker, håll isär dem:

1. STÄDA SPRÅKET i det som är memot — utan att skriva om Lars.
   Tillåtet: ta bort utfyllnadsord ("liksom", "ehm", tvekljud), falska starter och
   ordupprepningar, sätt interpunktion, dela i stycken, och ge blocket en kort
   underrubrik när ett nytt tema börjar. Gruppera segmenten i block efter naturliga
   ämnesgränser.
   INTE tillåtet: parafrasera, byta Lars ordval, förkorta eller sammanfatta hans
   resonemang, lägga till något. Är en mening begriplig som den är ska den stå kvar
   i hans formulering. Behåll hans röst.

2. FÖRESLÅ RADERING av material som inte hör till memot (remove=true + kort skäl).
   Detta är inte omskrivning — det är att skala bort det som aldrig var innehåll:
   - Avslutande skräp efter att föredraget/resonemanget är slut. Lars spelar in med
     hörapparater; efter avtackningen (t.ex. "Tack så mycket") är resten ofta
     publikinteraktion av usel ljudkvalitet som blivit nonsens.
   - Inskjutna avbrott mitt i: Lars tilltalar sin hund, eller hälsar på en bekant
     han möter. Orelaterat till innehållet.
   Var försiktig: radera bara det du är trygg med inte hör till memot. Vid minsta
   tvekan, behåll och städa i stället.

Returnera en lista block som täcker ALLA segment du fått (hoppa inte över något).
Varje block anger first_seg och last_seg (de [index] du fått)."""


def hms(sec) -> str:
    s = int(round(sec or 0))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def build_prompt(segments: list[dict], start: int, end: int) -> str:
    lines = [f"[{i}] ({hms(segments[i].get('start'))}) {segments[i].get('text', '').strip()}"
             for i in range(start, end)]
    return (
        f"Segment att bearbeta (index {start}–{end - 1}):\n"
        + "\n".join(lines)
        + "\n\nBearbeta HELA intervallet. Gruppera i block, städa språket utan att "
        "skriva om Lars, och flagga icke-innehåll för radering."
    )


def process_chunk(client, segments, start, end, model):
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        messages=[{"role": "user", "content": build_prompt(segments, start, end)}],
        output_format=Resultat,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return [], response.usage
    return response.parsed_output.blocks, response.usage


def main() -> int:
    cfg = k.load_config()
    json_path = k.json_path_for(cfg)
    if not json_path.is_file():
        print(f"FEL: JSON saknas: {json_path}", file=sys.stderr)
        return 1
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("FEL: ANTHROPIC_API_KEY saknas (lägg i .env).", file=sys.stderr)
        return 1

    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    if not segments:
        print("FEL: JSON saknar segment.", file=sys.stderr)
        return 1

    fcfg = cfg.get("forbattring", {})
    model = cfg["corrections"]["llm_modell"]
    per = int(fcfg.get("segment_per_anrop", 90))
    max_seg = int(fcfg.get("max_segment", 0))

    # CLI-override för testning: --from=N (startsegment), --max=N (antal segment).
    from_seg = 0
    for a in sys.argv[1:]:
        if a.startswith("--from="):
            from_seg = int(a.split("=", 1)[1])
        elif a.startswith("--max="):
            max_seg = int(a.split("=", 1)[1])
    count = (len(segments) - from_seg) if max_seg <= 0 else max_seg
    end_all = min(from_seg + count, len(segments))
    limit = end_all - from_seg

    import anthropic
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError as e:
        print(f"FEL: kunde inte skapa klient: {e}", file=sys.stderr)
        return 1

    all_blocks: list[Block] = []
    in_tok = out_tok = 0
    starts = list(range(from_seg, end_all, per))
    for n, start in enumerate(starts, 1):
        end = min(start + per, end_all)
        try:
            blocks, usage = process_chunk(client, segments, start, end, model)
        except anthropic.AuthenticationError:
            print("FEL: ogiltig ANTHROPIC_API_KEY.", file=sys.stderr)
            return 1
        except anthropic.APIError as e:
            print(f"  bit {n}/{len(starts)} misslyckades, hoppar över: {e}", file=sys.stderr)
            continue
        in_tok += usage.input_tokens
        out_tok += usage.output_tokens
        all_blocks.extend(blocks)
        removed = sum(1 for b in blocks if b.remove)
        print(f"  bit {n}/{len(starts)} (seg {start}–{end - 1}): {len(blocks)} block, {removed} för radering")

    # --- Bygg markdown + borttaget-fil ---
    stem = json_path.stem
    md_lines = [
        "---",
        f"titel: {stem}",
        f"ljudfil: {data.get('audio_file', '')}",
        f"kalla_json: {json_path.name}",
        f"langd_sek: {round(data.get('duration', 0) or 0, 1)}",
        f"genererad: {datetime.now().isoformat(timespec='seconds')}",
        "steg: c",
        "---",
        "",
    ]
    removed_lines: list[str] = []
    kept = 0
    for b in all_blocks:
        fs = max(0, min(b.first_seg, len(segments) - 1))
        ts = hms(segments[fs].get("start"))
        if b.remove:
            text = b.markdown.strip() or " ".join(
                segments[i].get("text", "").strip()
                for i in range(fs, min(b.last_seg + 1, len(segments))))
            removed_lines.append(f"[{ts}]  ({b.remove_reason or 'icke-innehåll'})\n{text}\n")
            continue
        kept += 1
        if b.heading.strip():
            md_lines.append(f"## {b.heading.strip()}")
            md_lines.append("")
        md_lines.append(f"**[{ts}]**")
        md_lines.append("")
        md_lines.append(b.markdown.strip())
        md_lines.append("")

    md_path = json_path.with_suffix(".md")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    removed_path = json_path.with_name(f"{stem}-borttaget.txt")
    if removed_lines:
        header = ("Förslag på borttaget material (steg c). Granska — inget är borta ur\n"
                  f"{json_path.name}, bara utelämnat ur {md_path.name}.\n\n")
        removed_path.write_text(header + "\n".join(removed_lines), encoding="utf-8")

    print()
    print(f"Läste:   {json_path.name} (segment {from_seg}–{end_all - 1}, {limit} st)")
    print(f"Block:   {len(all_blocks)} totalt — {kept} behållna, {len(removed_lines)} för radering")
    print(f"Tokens:  in {in_tok}, ut {out_tok}")
    print(f"Skrev:   {md_path.name}" + (f" + {removed_path.name}" if removed_lines else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
