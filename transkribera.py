"""Steg 1 av pipelinen: transkribera EN hårdkodad testfil med KBLabs svenska
Whisper (KBLab/kb-whisper-*) via faster-whisper / CTranslate2.

Producerar tre filer bredvid ljudfilen:
    <namn>.json  — full Whisper-utdata med ord-nivå-tidsstämplar. Sanningskällan.
    <namn>.srt   — undertexter, härlett ur JSON.
    <namn>.txt   — ren text, härlett ur JSON.

Ingen CLI, ingen mappgenomsökning, ingen kö. Filen och all maskinberoende
inställning kommer ur config.toml. Se CLAUDE.md.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import tomllib
import unicodedata
from datetime import datetime
from pathlib import Path

import ctranslate2
from faster_whisper import WhisperModel

import korrigeringar as k

PROJECT_ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    with open(PROJECT_ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def resolve_under_project(value: str) -> Path:
    """Relativa sökvägar tolkas mot projektroten; absoluta lämnas orörda."""
    p = Path(value)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def autodetect_device(configured: str) -> str:
    """'auto' -> cuda om CTranslate2 ser ett CUDA-kort, annars cpu.
    Ett explicit värde i konfigurationen tvingar valet (portabilitet: byt i
    config, aldrig i koden)."""
    if configured != "auto":
        return configured
    try:
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def autodetect_compute_type(configured: str, device: str) -> str:
    """'auto' -> float16 på cuda, int8 på cpu. Explicit värde tvingar."""
    if configured != "auto":
        return configured
    return "float16" if device == "cuda" else "int8"


# --------------------------------------------------------------------------- #
# Filnamn (File Naming Convention i CLAUDE.md)
# --------------------------------------------------------------------------- #

def normalize_stem(stem: str) -> str:
    """Gemener, bindestreck, ASCII, aldrig understreck."""
    # 3. Translitterera icke-ASCII (å->a, ä->a, ö->o, accenter -> basbokstav).
    decomposed = unicodedata.normalize("NFKD", stem)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    # 1. Gemener.
    s = ascii_only.lower()
    # 2. Mellanslag och understreck -> bindestreck.
    out = []
    for ch in s:
        if ch in " _":
            out.append("-")
        elif ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "-":
            out.append(ch)
        # 4. Allt annat tas bort.
    s = "".join(out)
    # 5. Kollapsa flera bindestreck.
    while "--" in s:
        s = s.replace("--", "-")
    # 6. Strippa ledande/avslutande bindestreck.
    return s.strip("-")


# --------------------------------------------------------------------------- #
# Loggning
# --------------------------------------------------------------------------- #

def setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("transkribera")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    fileh = logging.FileHandler(log_path, encoding="utf-8")
    fileh.setFormatter(fmt)
    logger.addHandler(fileh)
    return logger


# --------------------------------------------------------------------------- #
# Huvudflöde
# --------------------------------------------------------------------------- #

def main() -> int:
    cfg = load_config()

    logger = setup_logging(resolve_under_project(cfg["logging"]["file"]))

    audio_root = Path(cfg["data"]["root"])
    audio_path = audio_root / cfg["data"]["test_file"]
    if not audio_path.is_file():
        logger.error("Ljudfilen saknas: %s", audio_path)
        return 1

    model_name = cfg["model"]["name"]
    device = autodetect_device(cfg["model"]["device"])
    compute_type = autodetect_compute_type(cfg["model"]["compute_type"], device)
    download_root = resolve_under_project(cfg["model"]["download_root"])
    download_root.mkdir(parents=True, exist_ok=True)
    revision = cfg["model"]["revision"] or None

    tcfg = cfg["transcription"]
    cpu_threads = int(tcfg["cpu_threads"])

    logger.info("=== Ny körning ===")
    logger.info("Ljud:         %s", audio_path)
    logger.info("Modell:       %s (revision=%s)", model_name, revision or "standard")
    logger.info("Device:       %s", device)
    logger.info("compute_type: %s", compute_type)
    logger.info("beam_size:    %s", tcfg["beam_size"])
    logger.info("cpu_threads:  %s", cpu_threads if cpu_threads else "auto")

    # Ladda modell. CT2-vikterna ligger i repo-roten på Hugging Face och laddas
    # direkt av faster-whisper — ingen konvertering (se README).
    model = WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        download_root=str(download_root),
        cpu_threads=cpu_threads,
        revision=revision,
    )

    wall_start = time.monotonic()
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=tcfg["language"],
        beam_size=tcfg["beam_size"],
        word_timestamps=True,
        condition_on_previous_text=tcfg["condition_on_previous_text"],
    )

    # Segmenten är en generator — transkriberingen sker medan vi itererar.
    segments: list[dict] = []
    for seg in segments_iter:
        words = []
        if seg.words:
            for w in seg.words:
                words.append({
                    "start": w.start,
                    "end": w.end,
                    "word": w.word,
                    "probability": w.probability,
                })
        segments.append({
            "id": seg.id,
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
            "avg_logprob": seg.avg_logprob,
            "no_speech_prob": seg.no_speech_prob,
            "compression_ratio": seg.compression_ratio,
            "temperature": seg.temperature,
            "words": words,
        })

    wall_seconds = time.monotonic() - wall_start
    audio_seconds = info.duration
    ratio = wall_seconds / audio_seconds if audio_seconds else 0.0

    # Sanningskällan: full utdata med ord-nivå-tidsstämplar.
    output = {
        "audio_file": audio_path.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": model_name,
        "model_revision": revision or "standard",
        "device": device,
        "compute_type": compute_type,
        "beam_size": tcfg["beam_size"],
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "duration_after_vad": getattr(info, "duration_after_vad", None),
        "segments": segments,
    }

    stem = normalize_stem(audio_path.stem)
    out_dir = audio_path.parent
    json_path = out_dir / f"{stem}.json"
    srt_path = out_dir / f"{stem}.srt"
    txt_path = out_dir / f"{stem}.txt"

    json_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    k.write_srt(segments, srt_path)
    k.write_txt(segments, txt_path)

    logger.info("Segment:      %d", len(segments))
    logger.info("Ljudlängd:    %.1f s (%.2f min)", audio_seconds, audio_seconds / 60)
    logger.info("Väggklocka:   %.1f s (%.2f min)", wall_seconds, wall_seconds / 60)
    logger.info("Kvot vägg/ljud: %.2fx (realtid = 1,0)", ratio)
    logger.info("Skrev: %s", json_path)
    logger.info("Skrev: %s", srt_path)
    logger.info("Skrev: %s", txt_path)
    logger.info("=== Klar ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
