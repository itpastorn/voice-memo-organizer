"""Batch för steg a: transkribera flera filer med modellen laddad EN gång.

Anrop:
    venv/Scripts/python.exe batch-transkribera.py fil1.m4a undermapp/fil2.mp3 ...
        transkriberar de angivna filerna (relativt data.root eller absoluta).
    venv/Scripts/python.exe batch-transkribera.py
        upptäcker de N nyaste ljudfilerna som saknar .json (N via --antal=5),
        exklusive test/ och sammanfatta/.
    ... --dry-run
        listar bara vilka filer som skulle köras och avslutar (ingen modell laddas).

Skriver json/srt/txt bredvid varje fil, i samma format som transkribera.py.
Hoppar över filer som redan har .json (idempotent). Loggar per fil till samma
logg som steg a. Batch, inte realtid — blockerar datorn. Se CLAUDE.md (Körning).

Rör inte transkribera.py: återanvänder dess hjälpfunktioner (autodetektering,
normalize_stem, loggning) och korrigeringar-skrivarna. Ingen kodväg för steg a
ändras.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import transkribera as t
import korrigeringar as k
from faster_whisper import WhisperModel

AUDIO_EXTS = {".m4a", ".mp3", ".aac"}
EXCLUDE_DIRS = {"test", "sammanfatta"}


def json_path_for(audio: Path) -> Path:
    """Utdata-JSON:ens sökväg — normaliserat filnamn bredvid ljudet."""
    return audio.parent / f"{t.normalize_stem(audio.stem)}.json"


def discover(root: Path, n: int) -> list[Path]:
    """De N nyaste ljudfilerna utan .json, exkl. test/ och sammanfatta/."""
    files = []
    for p in root.rglob("*"):
        if p.suffix.lower() not in AUDIO_EXTS or not p.is_file():
            continue
        if {x.lower() for x in p.relative_to(root).parts} & EXCLUDE_DIRS:
            continue
        if json_path_for(p).exists():
            continue
        files.append(p)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:n]


def transcribe_one(model, cfg: dict, meta: dict, audio: Path,
                   logger=None) -> tuple[float, float, int, int]:
    """Transkribera EN fil, skriv json/srt/txt.
    Returnerar (ljudlängd, väggtid, segment, specialtoken i texten).

    Ordlisteprompten härleds per fil ur ljudets temamapp — det är den enda
    inställning som skiljer filerna åt i en batch."""
    tcfg = cfg["transcription"]
    prompt = k.ordlista_prompt_for(cfg, audio, logger)
    wall = time.monotonic()
    seg_iter, info = model.transcribe(
        str(audio),
        language=tcfg["language"],
        beam_size=tcfg["beam_size"],
        word_timestamps=True,
        condition_on_previous_text=tcfg["condition_on_previous_text"],
        hotwords=prompt,          # inte initial_prompt — se korrigeringar.ordlista_prompt_for
    )
    segments: list[dict] = []
    for seg in seg_iter:
        words = [{"start": w.start, "end": w.end, "word": w.word, "probability": w.probability}
                 for w in (seg.words or [])]
        segments.append({
            "id": seg.id, "start": seg.start, "end": seg.end, "text": seg.text,
            "avg_logprob": seg.avg_logprob, "no_speech_prob": seg.no_speech_prob,
            "compression_ratio": seg.compression_ratio, "temperature": seg.temperature,
            "words": words,
        })
    wall = time.monotonic() - wall

    output = {
        "audio_file": audio.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": meta["name"], "model_revision": meta["revision"] or "standard",
        "device": meta["device"], "compute_type": meta["compute_type"],
        "beam_size": tcfg["beam_size"], "language": info.language,
        "language_probability": info.language_probability, "duration": info.duration,
        "duration_after_vad": getattr(info, "duration_after_vad", None),
        "ordlista_prompt": prompt,
        "ordlista_prompt_tema": k.temamapp_for(cfg, audio),
        "segments": segments,
    }
    stem = t.normalize_stem(audio.stem)
    d = audio.parent
    (d / f"{stem}.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    k.write_srt(segments, d / f"{stem}.srt")
    k.write_txt(segments, d / f"{stem}.txt")
    # Duger transkriptionen över huvud taget? En upprepningsloop ger flytande
    # nonsens som varken granskningen eller detektorn upptäcker — se
    # transkriptionsvakt.py.
    if logger:
        for problem in k.transkriptionsproblem(k.transkriptionsmatt(output)):
            logger.warning("    VARNING: %s", problem)

    # Specialtoken som läckt ut som text blir ord i sanningskällan. Rapporteras
    # per fil av anroparen — se tokenvakt.py.
    return info.duration, wall, len(segments), len(k.specialtoken_traffar(segments))


def main() -> int:
    cfg = t.load_config()
    logger = t.setup_logging(t.resolve_under_project(cfg["logging"]["file"]))
    root = Path(cfg["data"]["root"])

    dry_run = "--dry-run" in sys.argv
    n = 5
    explicit = []
    for a in sys.argv[1:]:
        if a.startswith("--antal="):
            n = int(a.split("=", 1)[1])
        elif a.startswith("--"):
            continue
        else:
            explicit.append(a)

    if explicit:
        targets = []
        for a in explicit:
            p = Path(a)
            p = p if p.is_absolute() else (root / a)
            if not p.is_file():
                logger.error("Saknas, hoppar över: %s", p)
                continue
            targets.append(p)
    else:
        targets = discover(root, n)

    if not targets:
        logger.info("Inga filer att transkribera.")
        return 0

    logger.info("=== Batch: %d fil(er) att köra ===", len(targets))
    sparrade = 0
    for i, p in enumerate(targets, 1):
        done = json_path_for(p).exists()
        try:
            k.vakta_ljud(cfg, p)
            not_ = "   (json finns — hoppas över)" if done else ""
        except k.NamnFel:
            sparrade += 1
            not_ = "   (SPÄRRAD — namnkollision)"
        logger.info("  %d. %s%s", i, p.relative_to(root).as_posix(), not_)
    if sparrade:
        logger.warning("%d fil(er) spärrade av namnvakten — kör namnvakt.py för "
                       "att se varför och vad som ska döpas om.", sparrade)
    if dry_run:
        logger.info("--dry-run: ingen transkribering körd.")
        return 0

    device = t.autodetect_device(cfg["model"]["device"])
    compute_type = t.autodetect_compute_type(cfg["model"]["compute_type"], device)
    download_root = t.resolve_under_project(cfg["model"]["download_root"])
    download_root.mkdir(parents=True, exist_ok=True)
    revision = cfg["model"]["revision"] or None
    meta = {"name": cfg["model"]["name"], "device": device,
            "compute_type": compute_type, "revision": revision}

    logger.info("Modell: %s | device: %s | compute_type: %s | beam_size: %s",
                meta["name"], device, compute_type, cfg["transcription"]["beam_size"])
    model = WhisperModel(
        meta["name"], device=device, compute_type=compute_type,
        download_root=str(download_root),
        cpu_threads=int(cfg["transcription"]["cpu_threads"]), revision=revision,
    )

    total_wall = 0.0
    done_count = 0
    token_tot = 0
    # Issue #9: modernt vänteläge stryper jobbet i stället för att stoppa det,
    # så en nattkörning kryper i timmar utan att något syns i loggen. Begäran
    # hålls bara runt själva körningen — modellinläsningen ovan tar sekunder,
    # och --dry-run har redan returnerat.
    with k.vaken(logger, skal="batchtranskriberingen"):
        for i, audio in enumerate(targets, 1):
            if json_path_for(audio).exists():
                logger.info("[%d/%d] hoppar över (json finns): %s", i, len(targets), audio.name)
                continue
            # Namnvakten före arbetet: en kollision skulle låta den här körningen
            # skriva över ett transkript som redan finns. Hoppa över, avbryt inte
            # batchen — de övriga filerna är oskyldiga.
            try:
                for v in k.vakta_ljud(cfg, audio):
                    logger.warning("[%d/%d] VARNING: %s", i, len(targets), v)
            except k.NamnFel as e:
                logger.error("[%d/%d] SPÄRRAD %s: %s", i, len(targets), audio.name, e)
                if e.atgard:
                    logger.error("       %s", e.atgard)
                continue

            logger.info("[%d/%d] transkriberar: %s", i, len(targets), audio.name)
            try:
                dur, wall, nseg, tokentraffar = transcribe_one(model, cfg, meta, audio, logger)
            except Exception as e:
                logger.error("[%d/%d] MISSLYCKADES %s: %s", i, len(targets), audio.name, e)
                continue
            total_wall += wall
            done_count += 1
            token_tot += tokentraffar
            logger.info("[%d/%d] klar: %.1f min ljud, %.1f min väggtid (%.2fx realtid), %d segment -> %s",
                        i, len(targets), dur / 60, wall / 60, (wall / dur if dur else 0), nseg,
                        json_path_for(audio).name)
            if tokentraffar:
                logger.warning("[%d/%d] VARNING: %d specialtoken i texten — de blir ord i "
                               "JSON:en. Kör tokenvakt.py.", i, len(targets), tokentraffar)

    logger.info("=== Batch klar: %d transkriberade, total väggtid %.1f min ===",
                done_count, total_wall / 60)
    if token_tot:
        logger.warning("%d specialtoken hamnade i texten under körningen. Kör "
                       "tokenvakt.py för att se vilka filer.", token_tot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
