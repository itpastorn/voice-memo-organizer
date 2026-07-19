# voice-memo-organizer

Pipeline som gör Lars röstmemon sökbara: transkriberar dem, flaggar och rättar
felhörningar utan att förlora Lars röst, och (framöver) temataggar och indexerar
allt för sökning. Se **[CLAUDE.md](CLAUDE.md)** för hela bilden och besluten bakom.

Ljudet ligger utanför repot, i en datamapp som pekas ut av `data.root` i
[config.toml](config.toml). Originalljudet rörs aldrig — allt som produceras är
härledda filer bredvid ljudet.

## Pipeline och status

| Steg | Vad | Status |
| --- | --- | --- |
| **a** | Transkribering (KBLab Whisper) → `.json`/`.srt`/`.txt` | ✅ |
| **b** | Flaggning av felhörningar (LLM) → `-corrections.txt` | ✅ |
| — | Granska-GUI (webb): migrera → rätta → applicera | ✅ |
| **2** | Apply: skriv besluten till JSON, sätt `probability=1.0` | ✅ |
| **c** | Språklig förbättring → `-korrigerad.srt` + `.md` | ⬜ |
| **d** | QDA-taggning (kodbok, blocknivå) | ⬜ |
| **e** | SQLite-index (FTS5) för sökning | ⬜ |

## Modellen: KBLab kb-whisper via faster-whisper

`KBLab/kb-whisper-medium` distribuerar **CTranslate2-vikterna direkt i repo-roten
på Hugging Face** (`model.bin` + `config.json` + `vocabulary.json` +
`tokenizer.json`). faster-whisper laddar modellnamnet rakt av:

```python
WhisperModel("KBLab/kb-whisper-medium", ...)
```

**Ingen `ct2-transformers-converter` behövs.** Verifierat mot modellkortet
(2026-07). Samma gäller `kb-whisper-large` när VRAM finns; byt bara `model.name` i
`config.toml`. Revision väljs via `model.revision`: `""` (standard), `"subtitle"`
(kondenserad), `"strict"` (mer verbatim).

## Installation

Python 3.12 och ffmpeg i PATH.

```powershell
python -m venv venv
./venv/Scripts/python.exe -m pip install --upgrade pip
./venv/Scripts/python.exe -m pip install -r requirements.txt
```

Första körningen laddar ner vikterna (~1,5 GB för medium) till `models/`.

Granska-GUI:t (steg b) kräver **Docker** — ingen lokal PHP-installation. LLM-
flaggningen kräver en Anthropic-nyckel i en gitignorerad `.env` i projektroten:

```ini
ANTHROPIC_API_KEY=sk-ant-...
```

## Steg a — transkribering

Allt maskinberoende bor i [config.toml](config.toml). Producerar tre filer
**bredvid ljudfilen** (namn normaliseras enligt File Naming Convention i CLAUDE.md):
`<namn>.json` (sanningskällan, ord-nivå-tidsstämplar), `.srt`, `.txt`.

```powershell
# En enda fil (sökväg i data.test_file, byts för hand):
./venv/Scripts/python.exe transkribera.py

# Batch — modellen laddas en gång. Utan argument: de 5 nyaste utan .json:
./venv/Scripts/python.exe batch-transkribera.py [--antal=5] [--dry-run]
# ...eller explicita filer (relativt data.root eller absoluta):
./venv/Scripts/python.exe batch-transkribera.py fil1.m4a undermapp/fil2.mp3
```

Batchen är idempotent (hoppar över filer som redan har `.json`) och utesluter
`test/` och `sammanfatta/`. Varje körning loggar modell, device, compute_type,
ljudlängd och väggtid till stdout och `logs/transkribering.log` — för jämförelse
laptop/arbetsstation. (Mätpunkt: medium på CPU/int8 ≈ 0,56× realtid.)

## Steg b — flaggning av felhörningar

Ord-konfidens räcker inte (modellen är ofta tryggt fel på egennamn och engelska
låneord). Detektorn är därför en LLM som läser transkriptet semantiskt.

```powershell
# LLM-detektor (Claude). Ordlistan (ordlista.txt) ges som facit:
./venv/Scripts/python.exe flagga-llm.py
# Billig, nyckelfri jämförelse (flaggar lägsta X % ord-konfidens):
./venv/Scripts/python.exe generera-corrections.py
```

Skriver `<namn>-corrections.txt` bredvid JSON:en. Varje flaggat ord får sitt
**ankare** (ordets starttid, `@37.86`) och LLM:ns gissning som `#`-kommentar.
Delade byggstenar (config, ankare, kontextfönster, blockformat, `.srt`/`.txt`-
skrivare) bor i [korrigeringar.py](korrigeringar.py).

## Granska-GUI (webb, i Docker)

Ett webbgränssnitt för att rätta felhörningarna snabbt — ett ord i taget med
kontext, tangentbord först, och loopad ljuduppspelning runt varje problem.

```powershell
# 1. Migrera corrections-txt -> strukturerad sidecar + GUI-pekare + .env:
./venv/Scripts/python.exe migrera-corrections.py

# 2. Starta GUI:t (från granska/):
cd granska; docker compose up      # öppna http://localhost:8137
```

Källan (`/data`) monteras i **läsläge**; GUI:t skriver en arbetskopia av besluten i
`granska/state/`. Rätta i webbläsaren:

| Tangent | Åtgärd |
| --- | --- |
| `n` / `p` | nästa / förra flagga |
| `↵` | godta AI-förslaget |
| `c` | korrigera (eget ord) · `r` rätt som är · `d` radera · `s` osäker |
| Shift-klicka + `f` | ersätt en hel **fras** (markerat span) |
| `i` / `Shift+i` | **infoga** ord efter / före |
| `Space` | loopa ljudet ±5s runt ordet · `[` / `]` kortare / längre |

## Steg 2 — apply

När granskningen är klar skrivs besluten in i JSON:en:

```powershell
./venv/Scripts/python.exe applicera-corrections.py
```

Säkerhetskopierar `<namn>.json` → `<namn>.bak.json` (en gång), skriver den
korrigerade datan i `.json`, sätter `probability=1.0` på granskade ord, och
regenererar `.srt`/`.txt`. Läser alltid det orörda originalet (`.bak`) som källa —
**idempotent**: kör om utan skada. Osäkra och ogranskade ord lämnas orörda.

## Portabilitet (laptop → arbetsstation)

`model.device` och `model.compute_type` står på `"auto"`:

- CUDA hittas → `cuda` + `float16`
- annars → `cpu` + `int8`

CUDA-detektering via `ctranslate2.get_cuda_device_count()` — ingen torch behövs. På
arbetsstationen krävs **noll kodändringar**: byt vid behov `model.name` till
`kb-whisper-large` i `config.toml`, inget mer. Docker gör GUI:t lika portabelt.
