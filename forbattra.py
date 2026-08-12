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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

import korrigeringar as k

k.load_dotenv()

# Priser per miljon tokens för corrections.llm_modell (claude-opus-4-8).
PRIS_IN, PRIS_UT = 5.0, 25.0


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


class ForbattraFel(Exception):
    """Vägran: .md:n skulle bli ofullständig eller filen går inte att bearbeta."""


@dataclass(slots=True)
class Utfall:
    json_path: Path
    md_path: Path
    borttaget_path: Path | None
    block: int
    behallna: int
    borttagna: int
    segment: int
    anrop: int
    in_tok: int
    ut_tok: int

    @property
    def kostnad(self) -> float:
        return self.in_tok / 1e6 * PRIS_IN + self.ut_tok / 1e6 * PRIS_UT


def forbattra_en(client, cfg: dict, json_path: Path, *,
                 from_seg: int = 0, max_seg: int = 0, tyst: bool = False) -> Utfall:
    """Steg c för EN fil. Skriver <namn>.md och ev. <namn>-borttaget.txt.

    Kastar ForbattraFel om någon bit misslyckas. En .md med en tappad bit ser
    komplett ut — 90 segment saknas mitt i utan att något syns — och
    negationsvakten skulle larma på hela luckan utan att förklara varför. Hellre
    ingen .md än en med hål."""
    import anthropic

    if not json_path.is_file():
        raise ForbattraFel(f"JSON saknas: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    if not segments:
        raise ForbattraFel(f"{json_path.name} saknar segment.")

    per = int(cfg.get("forbattring", {}).get("segment_per_anrop", 90))
    model = cfg["corrections"]["llm_modell"]

    count = (len(segments) - from_seg) if max_seg <= 0 else max_seg
    end_all = min(from_seg + count, len(segments))

    all_blocks: list[Block] = []
    in_tok = out_tok = 0
    starts = list(range(from_seg, end_all, per))
    for n, start in enumerate(starts, 1):
        end = min(start + per, end_all)
        try:
            blocks, usage = process_chunk(client, segments, start, end, model)
        except anthropic.APIError as e:
            raise ForbattraFel(f"bit {n}/{len(starts)} (seg {start}–{end - 1}) "
                               f"misslyckades: {e}") from e
        in_tok += usage.input_tokens
        out_tok += usage.output_tokens
        all_blocks.extend(blocks)
        if not tyst:
            removed = sum(1 for b in blocks if b.remove)
            print(f"  bit {n}/{len(starts)} (seg {start}–{end - 1}): "
                  f"{len(blocks)} block, {removed} för radering")

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

    return Utfall(json_path=json_path, md_path=md_path,
                  borttaget_path=removed_path if removed_lines else None,
                  block=len(all_blocks), behallna=kept, borttagna=len(removed_lines),
                  segment=end_all - from_seg, anrop=len(starts),
                  in_tok=in_tok, ut_tok=out_tok)


def main() -> int:
    cfg = k.load_config()
    json_path, vald_via = k.aktuell_json(cfg)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("FEL: ANTHROPIC_API_KEY saknas (lägg i .env).", file=sys.stderr)
        return 1

    # CLI-override för testning: --from=N (startsegment), --max=N (antal segment).
    from_seg = 0
    max_seg = int(cfg.get("forbattring", {}).get("max_segment", 0))
    for a in sys.argv[1:]:
        if a.startswith("--from="):
            from_seg = int(a.split("=", 1)[1])
        elif a.startswith("--max="):
            max_seg = int(a.split("=", 1)[1])

    import anthropic
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError as e:
        print(f"FEL: kunde inte skapa klient: {e}", file=sys.stderr)
        return 1

    try:
        u = forbattra_en(client, cfg, json_path, from_seg=from_seg, max_seg=max_seg)
    except ForbattraFel as e:
        print(f"FEL: {e}", file=sys.stderr)
        return 1

    print()
    print(f"Fil:     {u.json_path.name}   <- {vald_via}")
    print(f"Läste:   {u.json_path.name} ({u.segment} segment i {u.anrop} anrop)")
    print(f"Block:   {u.block} totalt — {u.behallna} behållna, {u.borttagna} för radering")
    print(f"Tokens:  in {u.in_tok}, ut {u.ut_tok}  (~${u.kostnad:.2f})")
    print(f"Skrev:   {u.md_path.name}"
          + (f" + {u.borttaget_path.name}" if u.borttaget_path else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
