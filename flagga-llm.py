"""Steg b, LLM-varianten: flaggning av oklarheter med Claude som detektor.

Ord-konfidens missar Lars stora felklass — egennamn och engelska låneord blir
tryggt fel (dik=0,91 för "geek"). I stället läser Claude transkriptet *semantiskt*
och flaggar misstänkta ord: nonsensord, förvanskade namn, engelska ord renderade
som svenska, och riktiga ord på fel plats. Ordlistan (ordlista.txt) ges som facit
så modellen känner igen korrekta termer och förstår vad förvanskningar borde vara.

Skriver samma '<namn>-corrections.txt' som percentil-varianten (format i
korrigeringar.py). Claudes gissade rättelse + skäl läggs som en '# ...'-kommentar
efter ankaret — synlig hjälp, men Lars beslut. Högerledet lämnas tomt åt dig.

Nyckeln läses ur .env (gitignorerad) via ANTHROPIC_API_KEY. Se CLAUDE.md.
"""

from __future__ import annotations

import json
import os
import sys

from pydantic import BaseModel, Field

import korrigeringar as k

k.load_dotenv()  # måste ske före Anthropic() så nyckeln finns i miljön


# --------------------------------------------------------------------------- #
# Structured output — Claude tvingas svara i det här schemat
# --------------------------------------------------------------------------- #

class Flaggning(BaseModel):
    index: int = Field(description="Ordets [index] i listan nedan.")
    hort: str = Field(description="Ordet så som det står transkriberat (för kontroll).")
    gissad_rattelse: str = Field(description="Vad det troligen borde vara. Tom sträng om osäker.")
    skal: str = Field(description="Kort skäl: nonsensord, förvanskat namn, engelskt låneord, fel ord i sammanhanget, ...")


class Resultat(BaseModel):
    flaggningar: list[Flaggning]


SYSTEM = """\
Du granskar en automatisk transkription (KBLabs svenska Whisper) av Lars Gunthers \
röstmemon — teologiska resonemang på svenska, ofta med engelska namn och begrepp.

Din uppgift: flagga ord som troligen är FELHÖRDA av transkriberaren. Leta efter
- nonsensord och ord som inte finns (t.ex. "gik", "dik"),
- förvanskade egennamn (t.ex. "Dunper" för "Gunther"),
- engelska namn/låneord renderade som svenska (t.ex. "geek" felstavat),
- riktiga svenska ord som är fel i sammanhanget (t.ex. "punkt" där det borde vara "uns"),
- meningar som verkar SAKNA ord — särskilt negationer (inte, aldrig, ingen) som
  vänder betydelsen, och namn i attributioner ("säger X", "enligt X" där namnet
  fallit bort). Resultatet är flytande svenska, så läs efter logiken. Flagga ordet
  närmast luckan, lämna gissad rättelse TOM och ange i skälet att ett ord troligen
  saknas, och vilket,
- avvikelser i bibelcitat: när texten citerar eller parafraserar Bibeln, jämför mot
  Bibel 2000:s ordalydelse och flagga ord som ligger nära citatets form men är fel
  (t.ex. "lydig inför döden" där Fil 2:8 har "ända till"). Flagga INTE talarens
  egna medvetna parafraser.

Flagga INTE ord som är korrekta, även om de är ovanliga. Ordlistan du får är facit
över namn och begrepp som förekommer — dessa är rättstavade och ska inte flaggas,
men de hjälper dig förstå vad en förvanskning borde vara.

Var hellre för snål än för generös: en enda missad "gik" är värre än att du låter
bli att flagga ett tveksamt men troligen korrekt ord. Returnera bara verkliga
misstankar."""


# Kontextord som skickas med på var sida om en bit, men inte flaggas. Ger Claude
# sammanhang över bitgränsen utan att samma ord flaggas i två bitar.
PAD = 20


def build_prompt(words: list[dict], ctx_start: int, ctx_end: int,
                 flag_start: int, flag_end: int, ordlista: list[str]) -> str:
    lines = [f"[{i}] {words[i]['word'].strip()}" for i in range(ctx_start, ctx_end)]
    ordlista_text = "\n".join(f"- {t}" for t in ordlista) if ordlista else "(tom)"
    return (
        "ORDLISTA (kända, rättstavade namn och begrepp):\n"
        f"{ordlista_text}\n\n"
        "TRANSKRIPT (ett ord per rad, [globalt index] framför varje). Flagga ENDAST "
        f"ord med index {flag_start}–{flag_end - 1}; raderna utanför det intervallet "
        "är bara kontext:\n"
        f"{chr(10).join(lines)}\n\n"
        "Returnera de ord i intervallet du misstänker är felhörda, med [index], vad "
        "du hörde, din gissade rättelse (tom om osäker) och ett kort skäl."
    )


def flag_chunk(client, words: list[dict], flag_start: int, flag_end: int,
               ordlista: list[str], model: str) -> tuple[list[Flaggning], object]:
    """Flagga en bit words[flag_start:flag_end]. Skickar PAD ord extra på var sida
    som ren kontext. Returnerar (flaggningar inom intervallet, usage)."""
    ctx_start = max(0, flag_start - PAD)
    ctx_end = min(len(words), flag_end + PAD)
    response = client.messages.parse(
        model=model,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        messages=[{"role": "user", "content": build_prompt(
            words, ctx_start, ctx_end, flag_start, flag_end, ordlista)}],
        output_format=Resultat,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return [], response.usage
    inom = [f for f in response.parsed_output.flaggningar
            if flag_start <= f.index < flag_end]
    return inom, response.usage


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

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("FEL: ANTHROPIC_API_KEY saknas.", file=sys.stderr)
        print("Lägg din nyckel i .env (gitignorerad) och kör igen.", file=sys.stderr)
        return 1

    ccfg = cfg["corrections"]
    max_ord = int(ccfg["llm_max_ord"])
    limit = len(words) if max_ord <= 0 else min(max_ord, len(words))
    chunk = int(ccfg["llm_chunk_ord"])
    model = ccfg["llm_modell"]
    ordlista = k.load_ordlista()

    import anthropic
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError as e:
        print(f"FEL: kunde inte skapa klient: {e}", file=sys.stderr)
        return 1

    starts = list(range(0, limit, chunk))
    annotations: dict[int, str] = {}  # global index -> kommentar (dedupar naturligt)
    in_tok = out_tok = 0

    for n, start in enumerate(starts, start=1):
        end = min(start + chunk, limit)
        try:
            flaggningar, usage = flag_chunk(client, words, start, end, ordlista, model)
        except anthropic.AuthenticationError:
            print("FEL: ogiltig ANTHROPIC_API_KEY.", file=sys.stderr)
            return 1
        except anthropic.APIError as e:
            print(f"  bit {n}/{len(starts)} misslyckades, hoppar över: {e}",
                  file=sys.stderr)
            continue
        in_tok += usage.input_tokens
        out_tok += usage.output_tokens
        for f in flaggningar:
            forslag = f"{f.gissad_rattelse}? " if f.gissad_rattelse.strip() else ""
            annotations[f.index] = f"{forslag}({f.skal})"
        print(f"  bit {n}/{len(starts)} (ord {start}–{end - 1}): "
              f"{len(flaggningar)} flaggade — totalt {len(annotations)}")

    if not annotations:
        print(f"Claude flaggade inga ord bland {limit} ord.")
        return 0

    clusters = k.cluster(list(annotations), int(ccfg["merge_gap"]))
    text = k.build_file(
        words, clusters,
        int(ccfg["context_min_words"]), int(ccfg["context_max_words"]),
        annotations=annotations,
    )
    out_path.write_text(text, encoding="utf-8")

    print(f"Läste:   {json_path}")
    print(f"Skickade {limit} ord till {model} i {len(starts)} bitar")
    print(f"Flaggade {len(annotations)} ord i {len(clusters)} block")
    print(f"Tokens:  in {in_tok}, ut {out_tok}")
    print(f"Skrev:   {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
