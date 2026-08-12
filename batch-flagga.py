"""Batch för steg b: flagga flera transkriptioner och gör dem granskningsklara.

Anrop:
    venv/Scripts/python.exe batch-flagga.py
        upptäcker alla .json som saknar flaggning (--antal=N begränsar).
    venv/Scripts/python.exe batch-flagga.py NAR-profetrorelsen/zego-x.json ...
        kör de angivna filerna (relativt data.root eller absoluta).
    ... --dry-run
        listar vad som skulle köras, uppskattar kostnaden, och avslutar.

Gör två saker per fil — flaggning (flagga-llm) OCH migrering till sidecar
(migrera-corrections) — så att filen syns som granskningsklar i webbväljaren
direkt efteråt. Utan migreringen hade man fått köra ett skript till per fil.

Rör INTE granska/current.json: vilket memo som granskas är ditt val i GUI:t,
inte en biverkning av en batchkörning.

Idempotent: filer som redan har -corrections.txt eller -corrections.json hoppas
över, så en avbruten körning fortsätter där den slutade. Se CLAUDE.md steg b.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import korrigeringar as k

PROJECT_ROOT = Path(__file__).resolve().parent

# Priser per miljon tokens för corrections.llm_modell (claude-opus-4-8).
PRIS_IN, PRIS_UT = 5.0, 25.0


def ladda(namn: str):
    """Ladda en modul vars filnamn innehåller bindestreck. `import flagga-llm`
    går inte; filnamnen följer projektets namnkonvention och byts inte för
    Pythons skull."""
    spec = importlib.util.spec_from_file_location(
        namn.replace("-", "_"), PROJECT_ROOT / f"{namn}.py")
    mod = importlib.util.module_from_spec(spec)
    # Måste ligga i sys.modules FÖRE exec_module: Pydantic slår upp modulen för
    # att lösa typerna i Resultat/Flaggning, och kastar annars
    # "is not fully defined" när schemat ska användas.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fl = ladda("flagga-llm")
mig = ladda("migrera-corrections")


def redan_flaggad(p: Path) -> bool:
    """Flaggad = har .txt eller sidecar. Räcker för idempotens och skyddar
    handredigeringar från att skrivas över."""
    return (p.with_name(f"{p.stem}-corrections.txt").exists()
            or p.with_name(f"{p.stem}-corrections.json").exists())


def upptack(cfg: dict, root: Path, n: int | None) -> list[Path]:
    """Filer som saknar flaggning. Märkta filer ('ny transkription behövs' —
    t.ex. memon på annat språk än modellen, där resultatet blir en översättning)
    hoppas över: att flagga ord i en sådan text är bortkastade pengar."""
    status = k.las_status(cfg)
    filer = []
    for p in k.iter_transkript(root):
        if redan_flaggad(p):
            continue
        st = k.status_for(cfg, p, status)
        if st and st.get("status") == "ny-transkription":
            print(f"  hoppar över (ny transkription behövs): "
                  f"{p.relative_to(root).as_posix()}", file=sys.stderr)
            continue
        filer.append(p)
    filer.sort(key=lambda p: p.stat().st_mtime, reverse=True)   # nyaste först
    return filer if n is None else filer[:n]


def flagga_en(client, cfg: dict, json_path: Path, model: str,
              chunk: int) -> tuple[int, int, int, int]:
    """Flagga EN fil och skriv både .txt och sidecar.
    Returnerar (antal flaggor, tokens in, tokens ut, misslyckade bitar)."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    words = k.flatten_words(data.get("segments", []))
    if not words:
        raise ValueError("saknar ord-nivå-tidsstämplar")

    tema = k.temamapp_for(cfg, json_path)
    ordlista = k.load_ordlista(tema, allt=tema is None)

    import anthropic
    annotations: dict[int, str] = {}
    in_tok = out_tok = misslyckade = 0
    for start in range(0, len(words), chunk):
        end = min(start + chunk, len(words))
        try:
            flaggningar, usage = fl.flag_chunk(client, words, start, end, ordlista, model)
        except anthropic.APIError as e:
            misslyckade += 1
            print(f"      bit {start}–{end - 1} misslyckades: {e}", file=sys.stderr)
            continue
        in_tok += usage.input_tokens
        out_tok += usage.output_tokens
        for f in flaggningar:
            forslag = f"{f.gissad_rattelse}? " if f.gissad_rattelse.strip() else ""
            annotations[f.index] = f"{forslag}({f.skal})"

    ccfg = cfg["corrections"]
    txt_path = json_path.with_name(f"{json_path.stem}-corrections.txt")
    if annotations:
        clusters = k.cluster(list(annotations), int(ccfg["merge_gap"]))
        txt_path.write_text(
            k.build_file(words, clusters, int(ccfg["context_min_words"]),
                         int(ccfg["context_max_words"]), annotations=annotations),
            encoding="utf-8")
        result = mig.migrate(words, txt_path.read_text(encoding="utf-8"))
    else:
        # Inga flaggor: skriv ingen .txt (det finns inget att handredigera), men
        # ändå en tom sidecar så filen syns som klar i väljaren i stället för
        # att se ut som att steg b aldrig körts.
        result = {"flags": [], "phrase_edits": [], "insertions": [], "review": []}

    sidecar = {
        "audio_file": data.get("audio_file") or f"{json_path.stem}.m4a",
        "transcript_json": json_path.name,
        "source": "batch-flagga.py",
        "model": model,
        "tema": tema,
        "word_count": len(words),
        **result,
    }
    json_path.with_name(f"{json_path.stem}-corrections.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")

    return len(annotations), in_tok, out_tok, misslyckade


def main() -> int:
    cfg = k.load_config()
    root = Path(cfg["data"]["root"])

    dry_run = "--dry-run" in sys.argv
    antal = None
    explicit: list[str] = []
    for a in sys.argv[1:]:
        if a.startswith("--antal="):
            antal = int(a.split("=", 1)[1])
        elif a.startswith("--"):
            continue
        else:
            explicit.append(a)

    if explicit:
        mal = []
        for a in explicit:
            p = Path(a)
            p = p if p.is_absolute() else (root / a)
            if not p.is_file():
                print(f"Saknas, hoppar över: {p}", file=sys.stderr)
                continue
            mal.append(p)
    else:
        mal = upptack(cfg, root, antal)

    if not mal:
        print("Inget att flagga — alla transkriptioner har redan .txt eller sidecar.")
        return 0

    # Uppskattning ur uppmätta körningar: ~15 tokens in och ~3 ut per ord
    # (28k/6k för 1919 ord, 34k/7k för 2207). Grov, men rätt storleksordning.
    ord_tot = 0
    for p in mal:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            ord_tot += sum(len(s.get("words") or []) for s in d.get("segments", []))
        except Exception:
            pass
    kostnad = (ord_tot * 15 / 1e6) * PRIS_IN + (ord_tot * 3 / 1e6) * PRIS_UT

    print(f"=== Batch steg b: {len(mal)} fil(er), ca {ord_tot} ord ===")
    for i, p in enumerate(mal, 1):
        print(f"  {i}. {p.relative_to(root).as_posix()}")
    print(f"Uppskattad kostnad: ~${kostnad:.2f}")
    if dry_run:
        print("--dry-run: inget flaggat.")
        return 0

    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("FEL: ANTHROPIC_API_KEY saknas (lägg i .env).", file=sys.stderr)
        return 1

    import anthropic
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError as e:
        print(f"FEL: kunde inte skapa klient: {e}", file=sys.stderr)
        return 1

    ccfg = cfg["corrections"]
    model, chunk = ccfg["llm_modell"], int(ccfg["llm_chunk_ord"])
    klara = flaggor_tot = in_tot = ut_tot = 0

    for i, p in enumerate(mal, 1):
        print(f"[{i}/{len(mal)}] {p.name}")
        try:
            nf, itok, otok, fel = flagga_en(client, cfg, p, model, chunk)
        except anthropic.AuthenticationError:
            print("FEL: ogiltig ANTHROPIC_API_KEY. Avbryter.", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"      MISSLYCKADES, hoppar över: {e}", file=sys.stderr)
            continue

        # Gick INGEN bit igenom är felet systemiskt (slut på krediter, nere API)
        # — då är det bättre att stanna än att bränna igenom resten av kön och
        # lämna 39 filer halvflaggade.
        if fel and itok == 0:
            print("FEL: ingen bit gick igenom — avbryter batchen i stället för "
                  "att lämna resten halvflaggad.", file=sys.stderr)
            return 1

        klara += 1
        flaggor_tot += nf
        in_tot += itok
        ut_tot += otok
        varning = f"  ({fel} bit(ar) misslyckades)" if fel else ""
        print(f"      {nf} flaggor, {itok}+{otok} tokens{varning}")

    faktisk = in_tot / 1e6 * PRIS_IN + ut_tot / 1e6 * PRIS_UT
    print()
    print(f"=== Klart: {klara}/{len(mal)} filer, {flaggor_tot} flaggor ===")
    print(f"Tokens: in {in_tot}, ut {ut_tot}  (~${faktisk:.2f})")
    if klara:
        print("Öppna granska/valj.php för att granska — filerna står som klara där.")
    return 0 if klara == len(mal) else 1


if __name__ == "__main__":
    raise SystemExit(main())
