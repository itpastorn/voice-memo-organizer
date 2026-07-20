"""Runda 2 (frivillig): kontextgranskning av redan rättad transkription med Claude Fable.

Efter runda 1 (flagga-llm -> GUI -> apply) passerar ändå subtila fel — riktiga
svenska ord som är fel i sammanhanget ("få råd av Gud" för "nåd"), bortfallna ord
(negationer, namnattributioner; issue #1) och normaliserade bibelcitat (issue #2).
Det här skriptet läser den RÄTTADE JSON:en och låter Claude Fable 5 (starkare
semantisk läsning) leta just den felklassen.

Versionskedja:
    <stem>.bak.json    orört Whisper-original (rörs aldrig)
    <stem>.bak2.json   skapas HÄR: ögonblicksbild av runda 1-resultatet = runda 2:s bas
    <stem>.json        alltid senaste sanningen (skrivs om av apply)

Utdata är en sidecar direkt (ingen -corrections.txt, inget migrera-steg):
<stem>-corrections-2.json bredvid ljudet, med "runda": 2 och "base_json" så att
applicera-corrections.py vet vilken bas ordindexen refererar till. current.json
pekas om så granska-GUI:t visar exakt bak2-texten. Se CLAUDE.md.
"""

from __future__ import annotations

import json
import shutil
import sys
from typing import Literal

from pydantic import BaseModel, Field

import korrigeringar as k

PROJECT_ROOT = k.PROJECT_ROOT

k.load_dotenv()  # måste ske före Anthropic() så nyckeln finns i miljön


# --------------------------------------------------------------------------- #
# Structured output — Claude tvingas svara i det här schemat
# --------------------------------------------------------------------------- #

class Flaggning(BaseModel):
    index: int = Field(description="Ordets [index] i listan nedan.")
    hort: str = Field(description="Ordet så som det står i texten (för kontroll).")
    typ: Literal["ersattning", "bortfall"] = Field(
        description="'ersattning' = ordet är troligen fel och ska bytas. "
                    "'bortfall' = ett ord saknas troligen intill detta ord.")
    gissad_rattelse: str = Field(
        description="Vid 'ersattning': vad ordet troligen borde vara (tom om osäker). "
                    "Vid 'bortfall': ALLTID tom — förslaget läggs i skälet.")
    skal: str = Field(
        description="Kort skäl. Vid 'bortfall': vilket ord som troligen saknas och var, "
                    "t.ex. \"'inte' saknas troligen efter 'är'\".")


class Resultat(BaseModel):
    flaggningar: list[Flaggning]


SYSTEM = """\
Du granskar en transkription (KBLabs svenska Whisper) av Lars Gunthers röstmemon — \
teologiska föredrag och resonemang på svenska. Texten har REDAN genomgått en \
granskningsrunda där uppenbara fel rättats. Kvarvarande fel är subtila. Var restriktiv: \
flagga bara verkliga misstankar.

Leta efter tre felklasser:

1. RIKTIGA ORD FEL I SAMMANHANGET — särskilt närhomofoner från tal ("råd" där det \
borde vara "nåd") och betydelseglidningar ("innehåll" där det borde vara "inre"), \
ofta i teologiskt register. Ordet är korrekt svenska men passar inte kontexten.

2. BORTFALL — meningar som verkar sakna ord. Särskilt negationer (inte, aldrig, \
ingen) som vänder meningens betydelse, och namn i attributioner ("säger X", "enligt \
X" där namnet fallit bort). Resultatet är flytande svenska, så läs efter LOGIKEN: \
motsäger meningen sitt sammanhang, eller pekar en attribution på ingen? Flagga ordet \
närmast luckan med typ "bortfall" och ange i skälet vilket ord som troligen saknas.

3. BIBELCITAT — när texten citerar eller parafraserar Bibeln: jämför mot Bibel 2000:s \
ordalydelse och flagga avvikelser som ändrar orden ("lydig inför döden" där Fil 2:8 \
har "lydig ända till döden"). Flagga INTE talarens egna medvetna parafraser — bara \
ord som ligger nära citatets form men är fel.

Ordlistan du får är facit över namn och begrepp som förekommer — dessa är rättstavade \
och ska inte flaggas.

Ord markerade [granskad] är redan bekräftade av en människa i runda 1. Flagga dem bara \
vid mycket stark misstanke, och nämn i skälet att ordet var granskat.

Flagga INTE stil, dialekt, talspråk eller Lars egna formuleringar. Hellre för snål än \
för generös."""


# Kontextord som skickas med på var sida om en bit, men inte flaggas. Ger Claude
# sammanhang över bitgränsen utan att samma ord flaggas i två bitar.
PAD = 20

# Skiljetecken ignoreras när modellens citerade ord jämförs med transkriptets —
# Whisper-tokens bär punkt/citattecken ('innehåll.'), modellen citerar ordet bart.
SKILJETECKEN = '.,!?;:"\'”“’‘…—–()[]'


def karna(s: str) -> str:
    return s.strip().strip(SKILJETECKEN)


def build_prompt(words: list[dict], ctx_start: int, ctx_end: int,
                 flag_start: int, flag_end: int, ordlista: list[str]) -> str:
    lines = []
    for i in range(ctx_start, ctx_end):
        mark = " [granskad]" if words[i].get("probability") == 1.0 else ""
        lines.append(f"[{i}] {words[i]['word'].strip()}{mark}")
    ordlista_text = "\n".join(f"- {t}" for t in ordlista) if ordlista else "(tom)"
    return (
        "ORDLISTA (kända, rättstavade namn och begrepp):\n"
        f"{ordlista_text}\n\n"
        "TRANSKRIPT (ett ord per rad, [globalt index] framför varje; [granskad] = "
        "människobekräftat i runda 1). Flagga ENDAST ord med index "
        f"{flag_start}–{flag_end - 1}; raderna utanför det intervallet är bara kontext:\n"
        f"{chr(10).join(lines)}\n\n"
        "Returnera de ord i intervallet du misstänker enligt de tre felklasserna, med "
        "[index], typ, gissad rättelse (tom vid bortfall/osäkerhet) och ett kort skäl."
    )


def flag_chunk(client, words: list[dict], flag_start: int, flag_end: int,
               ordlista: list[str], model: str) -> tuple[list[Flaggning], object]:
    """Flagga en bit words[flag_start:flag_end]. Skickar PAD ord extra på var sida
    som ren kontext. thinking utelämnas — Fable har alltid tänkande på och
    avvisar annan konfiguration. Returnerar (flaggningar inom intervallet, usage)."""
    ctx_start = max(0, flag_start - PAD)
    ctx_end = min(len(words), flag_end + PAD)
    response = client.messages.parse(
        model=model,
        max_tokens=8000,
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
        return 1

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not data.get("corrections_applied_at"):
        print("FEL: JSON:en saknar corrections_applied_at.", file=sys.stderr)
        print("Runda 2 granskar den RÄTTADE texten — kör runda 1 (flagga-llm, GUI, "
              "applicera-corrections) först.", file=sys.stderr)
        return 1

    # --fortsatt: återuppta en ofullständig körning (t.ex. slut på API-krediter
    # mitt i). Bara de bitar som misslyckades körs om; befintliga flaggor och
    # eventuella GUI-beslut behålls.
    fortsatt = "--fortsatt" in sys.argv[1:]
    gammal: dict | None = None

    sidecar_path = json_path.with_name(f"{json_path.stem}-corrections-2.json")
    if sidecar_path.exists():
        gammal = json.loads(sidecar_path.read_text(encoding="utf-8"))
        rester = gammal.get("misslyckade_bitar") or []
        if not fortsatt:
            print(f"FEL: {sidecar_path.name} finns redan.", file=sys.stderr)
            print("Vägrar skriva över — den kan innehålla granskningsbeslut.", file=sys.stderr)
            if rester:
                print(f"Den är OFULLSTÄNDIG: {len(rester)} bitar misslyckades. Kör med "
                      "--fortsatt för att granska resten utan att förlora något.",
                      file=sys.stderr)
            else:
                print("Ta bort eller döp om filen (och ev. kopian i granska/state/) "
                      "och kör igen.", file=sys.stderr)
            return 1
        if not rester:
            print(f"{sidecar_path.name} är redan komplett — inget att fortsätta.")
            return 0
    elif fortsatt:
        print(f"FEL: --fortsatt utan befintlig {sidecar_path.name}.", file=sys.stderr)
        return 1

    words = k.flatten_words(data.get("segments", []))
    if not words:
        print("FEL: JSON saknar ord-nivå-tidsstämplar.", file=sys.stderr)
        return 1

    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("FEL: ANTHROPIC_API_KEY saknas.", file=sys.stderr)
        print("Lägg din nyckel i .env (gitignorerad) och kör igen.", file=sys.stderr)
        return 1

    ccfg = cfg["corrections"]
    chunk = int(ccfg["llm_chunk_ord"])
    model = ccfg["runda2_modell"]
    ordlista = k.load_ordlista()

    # CLI-override för billig testning (som forbattra.py): --max=N ord ur början.
    limit = len(words)
    for a in sys.argv[1:]:
        if a.startswith("--max="):
            limit = min(int(a.split("=", 1)[1]), len(words))

    # Basen för runda 2: ögonblicksbild av runda 1-resultatet. EN gång — rörs
    # aldrig om den finns, så ordindexen förblir giltiga över omkörningar.
    bak2 = json_path.with_name(f"{json_path.stem}.bak2.json")
    if not bak2.exists():
        shutil.copy2(json_path, bak2)
        bak2_msg = f"skapade {bak2.name}"
    else:
        bak2_msg = f"{bak2.name} fanns (bas för runda 2)"

    import anthropic
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError as e:
        print(f"FEL: kunde inte skapa klient: {e}", file=sys.stderr)
        return 1

    if gammal is not None:
        # --fortsatt: bara bitarna som misslyckades förra gången.
        bitar = [(int(s), int(e)) for s, e in gammal["misslyckade_bitar"]]
    else:
        bitar = [(s, min(s + chunk, limit)) for s in range(0, limit, chunk)]

    flaggor: dict[int, Flaggning] = {}  # global index -> flaggning (dedupar naturligt)
    misslyckade: list[list[int]] = []   # [start, end)-intervall som inte granskades
    in_tok = out_tok = 0

    for n, (start, end) in enumerate(bitar, start=1):
        try:
            resultat, usage = flag_chunk(client, words, start, end, ordlista, model)
        except anthropic.AuthenticationError:
            print("FEL: ogiltig ANTHROPIC_API_KEY.", file=sys.stderr)
            return 1
        except anthropic.APIError as e:
            misslyckade.append([start, end])
            print(f"  bit {n}/{len(bitar)} misslyckades, hoppar över: {e}",
                  file=sys.stderr)
            continue
        in_tok += usage.input_tokens
        out_tok += usage.output_tokens
        for f in resultat:
            flaggor[f.index] = f
        print(f"  bit {n}/{len(bitar)} (ord {start}–{end - 1}): "
              f"{len(resultat)} flaggade — totalt {len(flaggor)}")

    # --- Bygg sidecaren (samma struktur som runda 1 + runda/base_json) ---
    # Vid --fortsatt behålls befintliga flaggor (inklusive ev. GUI-beslut);
    # nya läggs bara till för index som saknas.
    flags_by_index: dict[int, dict] = (
        {f["global_index"]: f for f in gammal.get("flags", [])} if gammal else {}
    )
    for i in sorted(flaggor):
        if i in flags_by_index:
            continue
        f = flaggor[i]
        w = words[i]
        # Vid bortfall lämnas ai_guess tom: GUI:ts "Förslag"-knapp ERSÄTTER ordet,
        # vilket vore fel — förslaget står i skälet och Lars infogar med i/Shift+i.
        guess = "" if f.typ == "bortfall" else f.gissad_rattelse.strip()
        reason = f.skal.strip()
        if f.typ == "bortfall":
            reason = f"BORTFALL: {reason}"
        if w.get("probability") == 1.0 and "granskad" not in reason:
            reason += " (redan granskad i runda 1)"
        flags_by_index[i] = {
            "anchor": round(w["start"], 2) if w["start"] is not None else None,
            "global_index": i,
            "heard": w["word"].strip(),
            "verify_ok": karna(w["word"]) == karna(f.hort),
            "ai_guess": guess,
            "ai_reason": reason,
            "decision": "pending",
            "replacement": "",
            "note": "runda 2 (Fable)",
            "line": None,
        }
    flags = [flags_by_index[i] for i in sorted(flags_by_index)]

    sidecar = {
        "audio_file": data.get("audio_file") or f"{json_path.stem}.m4a",
        "transcript_json": bak2.name,
        "base_json": bak2.name,
        "runda": 2,
        "source": "granska-igen.py",
        "model": model,
        "word_count": len(words),
        "misslyckade_bitar": misslyckade,
        "flags": flags,
        "phrase_edits": (gammal or {}).get("phrase_edits", []),
        "insertions": (gammal or {}).get("insertions", []),
        "review": (gammal or {}).get("review", []),
    }
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # Pekare åt granska/-GUI:t: transcript = bak2 (exakt den text indexen refererar
    # till, stabil även efter att runda 2 applicerats), sidecar = runda 2-filen.
    granska = PROJECT_ROOT / "granska"
    granska.mkdir(exist_ok=True)
    (granska / "current.json").write_text(json.dumps({
        "stem": json_path.stem,
        "transcript_json": bak2.name,
        "sidecar_json": sidecar_path.name,
        "audio_file": sidecar["audio_file"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    env_path = granska / ".env"
    if not env_path.is_file():
        env_path.write_text(f"DATA_ROOT={cfg['data']['root']}\n", encoding="utf-8")

    # Finns redan en arbetskopia i granska/state/ (GUI:t har varit igång) fylls
    # den på med de nya flaggorna — beslut som redan fattats där rörs inte.
    state_copy = granska / "state" / sidecar_path.name
    state_msg = ""
    if state_copy.is_file():
        st = json.loads(state_copy.read_text(encoding="utf-8"))
        har = {f["global_index"] for f in st.get("flags", [])}
        nya = [f for f in flags if f["global_index"] not in har]
        st["flags"] = sorted(st.get("flags", []) + nya, key=lambda f: f["global_index"])
        st["misslyckade_bitar"] = misslyckade
        state_copy.write_text(json.dumps(st, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        state_msg = f"arbetskopian i granska/state/ kompletterad (+{len(nya)} flaggor)"

    bortfall = sum(1 for f in flags if f["ai_reason"].startswith("BORTFALL"))
    print()
    print(f"Läste:    {json_path.name} (rättad {data['corrections_applied_at']})")
    print(f"Bas:      {bak2_msg}")
    print(f"Granskade {len(bitar) - len(misslyckade)} av {len(bitar)} bitar med {model}")
    print(f"Flaggade  {len(flags)} ord ({bortfall} bortfall, {len(flags) - bortfall} ersättningar)")
    print(f"Tokens:   in {in_tok}, ut {out_tok}")
    print(f"Skrev:    {sidecar_path}")
    if state_msg:
        print(f"          {state_msg}")
    print("GUI:      current.json pekar nu på runda 2 — docker compose up i granska/")
    if misslyckade:
        oord = sum(e - s for s, e in misslyckade)
        print()
        print(f"VARNING: {len(misslyckade)} bitar ({oord} ord) blev ALDRIG granskade.")
        print("Åtgärda orsaken (t.ex. fyll på API-krediter) och kör:")
        print("    python granska-igen.py --fortsatt")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
