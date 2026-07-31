"""Delade byggstenar för steg b (flaggning av oklarheter).

Både percentil-generatorn (generera-corrections.py) och LLM-detektorn
(flagga-llm.py) läser samma Whisper-JSON och skriver samma corrections-format.
Det som skiljer är *hur* orden flaggas — vilka index som väljs. Allt runtomkring
(config, ordlista, kontextfönster, ankare, blockformat) bor här.

Se CLAUDE.md.
"""

from __future__ import annotations

import json
import tomllib
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Konfiguration och sökvägar
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    with open(PROJECT_ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def normalize_stem(stem: str) -> str:
    """Gemener, bindestreck, ASCII, aldrig understreck (File Naming Convention
    i CLAUDE.md). Speglar transkribera.py så att JSON-namnet kan härledas."""
    decomposed = unicodedata.normalize("NFKD", stem)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    s = ascii_only.lower()
    out = []
    for ch in s:
        if ch in " _":
            out.append("-")
        elif ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "-":
            out.append(ch)
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def json_path_for(cfg: dict) -> Path:
    """JSON-filens sökväg härledd ur samma data.test_file som steg a."""
    audio_root = Path(cfg["data"]["root"])
    test_file = Path(cfg["data"]["test_file"])
    stem = normalize_stem(test_file.stem)
    return audio_root / test_file.parent / f"{stem}.json"


def rel_to_root(cfg: dict, path: Path) -> str:
    """Sökväg relativt datamappens rot, med snedstreck — formatet granska/current.json
    använder. Ljudet ligger i temamappar (NAR-profetrorelsen/...), och GUI:t monterar
    roten som /data; bara filnamnet räcker alltså inte. Filer direkt i roten ger
    enbart filnamnet."""
    rel = path.resolve().relative_to(Path(cfg["data"]["root"]).resolve())
    return rel.as_posix()


def load_ordlista() -> list[str]:
    """Egennamn och begrepp ur ordlista.txt. Rader som börjar med # hoppas över,
    och en '#'-kommentar sist på en rad skalas bort — listan dokumenterar ofta
    observerade förvanskningar ('Shawn Bolz  # (Bolts, Boltz)'), men LLM:n ska
    bara få den rättstavade termen som facit.
    Tom lista om filen saknas — ordlistan är hjälp, inte krav."""
    path = PROJECT_ROOT / "ordlista.txt"
    if not path.is_file():
        return []
    termer = []
    for rad in path.read_text(encoding="utf-8").splitlines():
        rad = rad.split("#", 1)[0].strip()
        if rad:
            termer.append(rad)
    return termer


def load_dotenv() -> None:
    """Minimal .env-läsare: KEY=VALUE-rader läggs i miljön om de inte redan finns.
    Undviker ett beroende till python-dotenv för en handfull rader."""
    import os
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for rad in path.read_text(encoding="utf-8").splitlines():
        rad = rad.strip()
        if not rad or rad.startswith("#") or "=" not in rad:
            continue
        key, _, val = rad.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


# --------------------------------------------------------------------------- #
# Ordlista ur JSON
# --------------------------------------------------------------------------- #

def flatten_words(segments: list[dict]) -> list[dict]:
    """Platta ut alla ord till en enda lista i ordning. Varje ord får med sig
    en säker starttid (faller tillbaka på segmentets start om ordets saknas)."""
    words: list[dict] = []
    for seg in segments:
        seg_start = seg.get("start")
        for w in seg.get("words") or []:
            start = w.get("start")
            words.append({
                "word": w.get("word", ""),
                "start": start if start is not None else seg_start,
                "probability": w.get("probability"),
            })
    return words


# --------------------------------------------------------------------------- #
# Kluster och kontextfönster
# --------------------------------------------------------------------------- #

def cluster(flagged: list[int], merge_gap: int) -> list[list[int]]:
    """Slå ihop flaggade ord som ligger nära varandra till gemensamma block."""
    clusters: list[list[int]] = []
    for i in sorted(flagged):
        if clusters and i - clusters[-1][-1] <= merge_gap:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    return clusters


def context_window(words: list[dict], cluster_idx: list[int],
                   min_words: int, max_words: int) -> tuple[int, int]:
    """Ett fönster på min–max ord som alltid rymmer hela klustret, centrerat runt
    det. Returnerar (start, slut) som index i words (slut exklusivt)."""
    first, last = cluster_idx[0], cluster_idx[-1]
    center = (first + last) // 2
    half = max_words // 2
    start = max(0, center - half)
    end = min(len(words), start + max_words)
    start = max(0, end - max_words)
    start = min(start, first)
    end = max(end, last + 1)
    if end - start < min_words:
        end = min(len(words), start + min_words)
        start = max(0, end - min_words)
    return start, end


def context_text(words: list[dict], start: int, end: int) -> str:
    """Kontextsträngen: orden råa (Whisper-tokens bär redan sina mellanslag)."""
    return "".join(w["word"] for w in words[start:end]).strip()


# --------------------------------------------------------------------------- #
# Utskrift av corrections-filen
# --------------------------------------------------------------------------- #

def format_anchor(start) -> str:
    return f"@{start:.2f}" if start is not None else "@?"


def build_file(words: list[dict], clusters: list[list[int]],
               min_words: int, max_words: int,
               annotations: dict[int, str] | None = None) -> str:
    """Bygg hela corrections-filens text i Lars format.

    annotations: valfri karta global ordindex -> kommentar (t.ex. LLM:ns
    föreslagna rättelse och skäl). Läggs efter ankaret som '# ...' så att den
    syns men inte tas för Lars beslut; apply-steget ignorerar allt efter '#'.
    """
    annotations = annotations or {}
    all_flagged = [i for c in clusters for i in c]
    left_width = max(
        (len(words[i]["word"].strip()) + 1 for i in all_flagged),
        default=0,
    )

    blocks: list[str] = []
    for c in clusters:
        start, end = context_window(words, c, min_words, max_words)
        lines = [f'"context": "{context_text(words, start, end)}"']
        for i in c:
            w = words[i]
            left = f'{w["word"].strip()}='
            row = f"{left.ljust(left_width)}  {format_anchor(w['start'])}"
            if i in annotations:
                row += f"   # {annotations[i]}"
            lines.append(row)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


# --------------------------------------------------------------------------- #
# Härledda format (.srt, .txt) — delade av transkribera.py (steg a) och
# applicera-corrections.py (steg 2). JSON är sanningskällan; dessa härleds ur den.
# --------------------------------------------------------------------------- #

def format_timestamp(seconds: float) -> str:
    """SRT-tid: HH:MM:SS,mmm."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[dict], path) -> None:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_txt(segments: list[dict], path) -> None:
    # En rad per segment. JSON är sanningskällan; detta är läsbar bekvämlighet.
    lines = [seg["text"].strip() for seg in segments]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
