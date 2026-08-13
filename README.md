# voice-memo-organizer

Pipeline som gör Lars röstmemon sökbara: transkriberar dem, flaggar och rättar
felhörningar utan att förlora Lars röst, och (framöver) temataggar och indexerar
allt för sökning. Se **[CLAUDE.md](CLAUDE.md)** för hela bilden och besluten bakom.

Ljudet ligger utanför repot, i en datamapp som pekas ut av `data.root` i
[config.toml](config.toml). Originalljudet rörs aldrig — allt som produceras är
härledda filer bredvid ljudet.

---

## Snabbmanual — så kör du kedjan

Exemplen är Git Bash. Sätt genvägarna en gång per terminalfönster:

```bash
VMO=/c/Users/gunther/Dropbox/arkiv/workspace/ai-agents-projects/voice-memo-organizer
PY="$VMO/venv/Scripts/python.exe"
alias batch="$PY $VMO/batch-transkribera.py"
```

### 1. Transkribera (steg a) — dyrt, allt annat är billigt

Ställ dig i temamappen med ljudet och peka ut filerna. **`"$PWD"/` behövs** —
utan den letar skriptet i inkorgen.

```bash
cd /c/Users/gunther/Dropbox/arkiv/mediadev/transkribera/NAR-profetrorelsen
batch "$PWD"/*.m4a --dry-run     # se vad som skulle köras
batch "$PWD"/*.m4a               # kör allt (idempotent — hoppar över klara)
files=("$PWD"/*.m4a); batch "${files[@]:0:3}"   # eller bara några
```

Räkna med **0,8–1,5× realtid**; 39 filer ≈ 9 timmar. **Datorn somnar och stryper
jobbet** efter ~20 minuter utan tillsyn (issue #9) — starta långa körningar från
PowerShell med vaken-låsning, eller stanna vid datorn.

### 2. Flagga felhörningar (steg b) — ~$0,25/fil

```bash
cd "$VMO"
"$PY" batch-flagga.py --dry-run   # lista + kostnadsuppskattning
"$PY" batch-flagga.py             # flaggar OCH gör granskningsklart
```

### 3. Granska i webbläsaren

```bash
cd "$VMO/granska" && docker compose up      # http://localhost:8137
```

Väljaren listar alla filer, senaste överst. Klicka en → rätta med `n` (nästa),
`↵` (godta förslag), `c` (eget ord), `r` (rätt som är), `Space` (lyssna).
Duger transkriptionen inte alls — t.ex. memo på engelska — tryck **Märk: ny
transkription behövs** i sidopanelen.

### 3b. Låt rättelserna hitta sina syskon (frivilligt)

```bash
"$PY" propagera-namn.py --dry-run     # visa förslagen
"$PY" propagera-namn.py               # lägg dem som nya flaggor
```

Detektorn ser ord, inte dokument: samma namn förvanskas olika många gånger och
bara någon variant flaggas. När du rättat `Vertobius` → `Posobiec` letar det här
skriptet upp de **orättade** varianterna längre fram i samma fil. Deterministiskt,
inga API-anrop, och det applicerar aldrig — allt blir nya `pending`-flaggor som du
avgör i GUI:t. Kör om GUI:t efteråt.

Räkna med **ungefär ett förslag per fil**, ofta noll. Ungefär hälften är riktiga
fynd; resten kostar ett tangenttryck. Justera med `--troskel=` (lägre = fler).

### 4. Skriv in besluten

```bash
"$PY" aktuell.py                  # vilken fil är vald? (skriver ingenting)
"$PY" applicera-corrections.py    # följer filen du valt i GUI:t

"$PY" batch-applicera.py --dry-run   # alla färdiggranskade på en gång
"$PY" batch-applicera.py
```

`aktuell.py` svarar på frågan som annars kräver att GUI:t är igång: vilken fil
står på tur, hur många flaggor är ogranskade, är den redan applicerad.

Har du granskat en hel omgång är `batch-applicera.py` vägen: den tar alla filer
som är färdiggranskade och ännu inte applicerade. `--dry-run` räknar igenom allt
och skriver ingenting — siffrorna är riktiga, inte uppskattade, så du ser exakt
vad som skulle ändras innan något rörs.

### 5. Läsbar text (steg c)

```bash
"$PY" forbattra.py         # -> <namn>.md
"$PY" negationsvakt.py     # larmar om en negation tappats

"$PY" batch-forbattra.py --dry-run   # alla applicerade filer + kostnad
"$PY" batch-forbattra.py             # kör steg c OCH negationsvakten per fil
```

Steg c kostar pengar (~**$0,01 per ljudminut**, uppmätt), så batchen visar alltid
kostnaden först och `--dry-run` gör inget annat. Negationsvakten körs automatiskt
efter varje fil — den är gratis och är hela skyddet mot att städningen gör en
tappad negation osynlig.

**Kör om steg c** efter varje ny apply — `.md` är ett derivat av JSON:en.

Steg 4 och 5 arbetar alla på **den fil du valt i GUI:t** (`granska/current.json`),
och skriver ut vilken det blev. Har du inte valt någon används `data.test_file`
i config.toml. `aktuell.py` visar valet i förväg.

| Vill du... | Gör så |
| --- | --- |
| veta vilken fil som är vald | `aktuell.py` — fil, mapp, flaggor kvar, applicerad eller ej |
| applicera många filer på en gång | `batch-applicera.py` — hoppar över ogranskade, märkta och redan applicerade |
| köra en enda fil genom steg a | sätt `data.test_file` i config.toml, kör `transkribera.py` |
| granska en fil som saknar flaggning | öppna den ändå i väljaren, klicka valfritt ord |
| leta subtila fel en gång till | `granska-igen.py` (Claude Fable, ~$4/fil — sällan behövt) |
| transkribera om en fil | ta bort dess `-corrections.*`, `-bak*.json` och kopian i `granska/state/` **först** |

---

## Pipeline och status

| Steg | Vad | Status |
| --- | --- | --- |
| **a** | Transkribering (KBLab Whisper) → `.json`/`.srt`/`.txt` · enskild + batch | ✅ |
| **b** | Flaggning av felhörningar (LLM) → sidecar · enskild + batch | ✅ |
| — | Granska-GUI (webb): filväljare → rätta → applicera | ✅ |
| **2** | Apply: skriv besluten till JSON, sätt `probability=1.0` | ✅ |
| — | Runda 2 (frivillig): kontextgranskning med Claude Fable | ✅ sällan behövd |
| **c** | Språklig förbättring → `.md` + `-borttaget.txt` | ✅ prototyp |
| — | Negationsvakt: deterministisk kontroll av steg c-utdatan | ✅ |
| **d** | QDA-taggning (kodbok, blocknivå) | ⬜ kodboken obeslutad |
| **e** | SQLite-index (FTS5) för sökning | ⬜ |
| **f** | Metadatataggar på ljudfilerna (efter a, c och d) | ⬜ |

Sex filer har gått hela vägen a → c i fem ämnesområden. **Flaggfrekvensen ligger på
1,3–2 % av orden oberoende av ämne** — det är en egenskap hos ljudet och modellen,
inte hos domänen, och detektorn klarade tre domäner där ordlistan var tom.

### Kända hinder

| # | Vad | Följd |
| --- | --- | --- |
| [#9](../../issues/9) | Modernt vänteläge stryper nattbatch | **blockerar arkivgenomkörningen** |
| [#8](../../issues/8) | Hastigheten spänner 0,56×–1,99× oförklarat | går inte att planera på |
| [#11](../../issues/11) | Språkdetektering avstängd av config | engelska memon översätts tyst |
| [#7](../../issues/7) | Samma namn förvanskat olika — bara ett flaggas | detektorn ser ord, inte dokument |

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

# Batch — modellen laddas en gång. Utan argument: de 5 nyaste utan .json
# i HELA datamappen, inte i mappen du står i:
./venv/Scripts/python.exe batch-transkribera.py [--antal=5] [--dry-run]
# ...eller explicita filer (relativt data.root eller absoluta):
./venv/Scripts/python.exe batch-transkribera.py fil1.m4a undermapp/fil2.mp3
```

Vill du köra **en temamapp** måste filerna pekas ut — se snabbmanualen överst.

Batchen är idempotent (hoppar över filer som redan har `.json`) och utesluter
`test/` och `sammanfatta/`. Varje körning loggar modell, device, compute_type,
ljudlängd och väggtid till stdout och `logs/transkribering.log` — för jämförelse
laptop/arbetsstation.

**Hastigheten går inte att planera på.** Uppmätt på samma maskin och inställningar:
0,56× (referensen), 0,76–0,82×, 0,99×, 1,25–1,31×, och 1,52–1,99× med
ordlisteprompt. Spannet är 3,5× och orsaken är okänd — se issue #8. Räkna inte på
bästafallet: skillnaden mellan 0,8× och 1,5× är ~100 timmar CPU på arkivets
återstående filer.

**Transkriberar du om en fil** varnar skriptet om det redan finns härledda filer
(`-corrections.*`, `-bak*.json`). De indexerar den gamla texten och måste bort
innan du flaggar om — annars pekar ordindexen fel.

### Ordlisteprompt — kända namn matas in i förväg

Whisper får inte memots eget nyckelord rätt av sig själv: *ungjordskreationism*
förvanskades fyra gånger i samma fil, *cessationist* fem gånger i en annan.
Prompten byggs per fil av **`ordlista/gemensam.txt` + `ordlista/<temamapp>.txt`**:

| | |
| --- | --- |
| Skickas som | faster-whispers **`hotwords`** — `initial_prompt` når bara första 30-sekundersfönstret när `condition_on_previous_text=false` |
| Tak | **223 tokens** (`448//2-1`), exakt räknat med modellens egen tokenizer |
| Prioritet | basen överlever alltid; mappens termer kapas bakifrån och **loggas** |
| Inkorgen | bara basen — temat är okänt tills filen sorterats |
| Av/på | `transcription.ordlista_prompt` i config.toml |

`transcription.prompt_bas` byter basfil när materialet inte är Lars egna memon.
JSON:en bär `ordlista_prompt` och `ordlista_prompt_tema`, så två körningar av samma
fil går att skilja åt i efterhand.

**Avstängd som standard.** Tre prov visade ingen nytta: på en fil med **12 kända
fel** rättades **0**, och flera blev sämre (`Nordbehandlade` → *en obehandlad rätt*
— nonsens blev flytande nonsens). På en felfri fil försämrades interpunktionen.
Slå inte på den utan att mäta om — underlaget står i CLAUDE.md steg a.

Uppdelningen i `ordlista/` behålls ändå: den bär **steg b:s** mappscopning.

## Steg b — flaggning av felhörningar

Ord-konfidens räcker inte (modellen är ofta tryggt fel på egennamn och engelska
låneord). Detektorn är därför en LLM som läser transkriptet semantiskt.

```powershell
# LLM-detektor (Claude). Ordlistan (gemensam + temamappens) ges som facit:
./venv/Scripts/python.exe flagga-llm.py

# Batch — flaggar OCH migrerar till sidecar, så filerna blir granskningsklara:
./venv/Scripts/python.exe batch-flagga.py [--antal=5] [--dry-run]
./venv/Scripts/python.exe batch-flagga.py NAR-profetrorelsen/zego-x.json ...

# Billig, nyckelfri jämförelse (flaggar lägsta X % ord-konfidens):
./venv/Scripts/python.exe generera-corrections.py
```

`batch-flagga.py` upptäcker alla transkriptioner utan flaggning, nyaste först.
`--dry-run` listar dem och **uppskattar kostnaden** innan något körs. Idempotent:
filer med `.txt` eller sidecar hoppas över, så en avbruten körning fortsätter där
den slutade. Går ingen bit igenom (slut på API-krediter, nere API) avbryts hela
batchen i stället för att lämna kön halvflaggad. `granska/current.json` rörs inte
— vilket memo som granskas är ditt val i GUI:t.

Skriver `<namn>-corrections.txt` bredvid JSON:en. Varje flaggat ord får sitt
**ankare** (ordets starttid, `@37.86`) och LLM:ns gissning som `#`-kommentar.
Delade byggstenar (config, ankare, kontextfönster, blockformat, `.srt`/`.txt`-
skrivare) bor i [korrigeringar.py](korrigeringar.py).

## Granska-GUI (webb, i Docker)

Ett webbgränssnitt för att rätta felhörningarna snabbt — ett ord i taget med
kontext, tangentbord först, och loopad ljuduppspelning runt varje problem.

```powershell
cd granska; docker compose up      # öppna http://localhost:8137
```

Använder du `batch-flagga.py` är sidecarerna redan skrivna. Kör du enstaka filer med
`flagga-llm.py` behövs `migrera-corrections.py` däremellan (den skriver också
`granska/.env` första gången, vilket Docker-uppstarten kräver).

**Filväljaren är ingången.** `valj.php` listar **alla** transkriptioner i datamappen,
senaste överst, med filter på temamapp och filnamn. Statuskolumnen visar var varje
fil står: `ej flaggad` · `inga fel funna` · `X flaggor, Y kvar` · `applicerad` ·
`runda 2` · `⚠ ny transkription behövs`.

**Filer utan sidecar går att öppna ändå** — de får en tom arbetskopia, och du rättar
genom att klicka valfritt ord i texten. Saknas ljudfilen (gäller äldre transkript)
märks ljudpanelen ut och resten fungerar som vanligt.

Valet skrivs till `granska/current.json`, och `applicera-corrections.py` följer det —
annars vore väljaren en fälla: granska fil X, applicera fil Y.

**Märk "ny transkription behövs"** när transkriptionen inte duger — t.ex. ett memo
på engelska, där KB-Whisper *översätter* i stället för att transkribera. Knappen
finns i granskningsvyn; märkningen sparas i `granska/status.json` (versionerad),
syns i väljaren och gör att `batch-flagga.py` hoppar över filen. Ingen mening att
flagga ord i en text som ska göras om.

Transkriberar du om en fil: **ta bort dess `-corrections.*`, `-bak*.json` och kopian
i `granska/state/` först.** De indexerar den gamla texten. `transkribera.py` varnar
om de finns, och `applicera-corrections.py` vägrar när sidecarens ordantal inte
stämmer med källan.

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

**Vilken fil?** `granska/current.json` — alltså den du valt i GUI:t — annars
`data.test_file` i config.toml. Rapporten skriver ut vilken fil som träffades och
varifrån valet kom, så ett felaktigt val syns direkt.

Säkerhetskopierar `<namn>.json` → `<namn>-bak.json` (en gång), skriver den
korrigerade datan i `.json`, sätter `probability=1.0` på granskade ord, och
regenererar `.srt`/`.txt`. Läser alltid rundans orörda bas som källa (runda 1:
`-bak.json`; runda 2: sidecarens `base_json` → `-bak2.json`) — **idempotent**:
kör om utan skada. Osäkra och ogranskade ord lämnas orörda. Finns en runda
2-sidecar (`-corrections-2.json`) appliceras den; annars runda 1.

**Vägrar** om sidecarens `word_count` inte stämmer med källans ordantal — då har
transkriptionen bytts under sidecaren och besluten skulle skrivas på fel ord.
**Varnar** om filen är märkt "ny transkription behövs".

Glöm inte att köra om steg c efteråt: `.md` är ett derivat av JSON:en.

## Runda 2 — kontextgranskning med Claude Fable (frivillig)

Subtila fel överlever runda 1: riktiga ord fel i sammanhanget ("få *råd* av
Gud" → nåd), bortfallna negationer/namn, normaliserade bibelcitat. Efter apply
kan den rättade JSON:en granskas en gång till av Claude Fable 5 (dyrare,
starkare semantisk läsning — modell i `corrections.runda2_modell`):

```powershell
./venv/Scripts/python.exe granska-igen.py
```

Skapar `<namn>-bak2.json` (runda 2:s bas, en gång) och skriver sidecaren
`<namn>-corrections-2.json` direkt — inget migrera-steg. `granska/current.json`
pekas om, så samma GUI används för granskningen; därefter samma
`applicera-corrections.py`. Bortfallsflaggor (märkta BORTFALL i skälet) rättas
med GUI:ts infoga-tangenter `i`/`Shift+i`.

Misslyckas bitar (t.ex. slut på API-krediter mitt i) noteras de i sidecaren
(`misslyckade_bitar`) och skriptet avslutar med varning + exit-kod 1. Åtgärda
orsaken och kör `granska-igen.py --fortsatt` — bara resterna granskas, och
befintliga flaggor och GUI-beslut behålls. `--max=N` finns för billig testning.

## Steg c — språklig förbättring + negationsvakt

```powershell
./venv/Scripts/python.exe forbattra.py       # -> <namn>.md + <namn>-borttaget.txt
./venv/Scripts/python.exe negationsvakt.py   # deterministisk vakt, inga API-anrop
```

Båda arbetar på samma fil som apply: `granska/current.json` om den finns, annars
`data.test_file`. Källan skrivs ut i rapporten.

`forbattra.py` städar språket utan att skriva om Lars och föreslår radering av
icke-innehåll (samlas i `-borttaget.txt`). `negationsvakt.py` räknar
negationsord (*inte, aldrig, ingen, inget, inga, utan, icke*) per block i
`.md`:n mot JSON-segmenten i samma tidsfönster — en tappad negation är den mest
extrema omskrivning som finns, och flytande text döljer den. Avvikelse ⇒
tidsstämplad rad + exit-kod 1. Falska positiva är OK; varje polaritetsändring
ska vara ett medvetet beslut.

## Portabilitet (laptop → arbetsstation)

`model.device` och `model.compute_type` står på `"auto"`:

- CUDA hittas → `cuda` + `float16`
- annars → `cpu` + `int8`

CUDA-detektering via `ctranslate2.get_cuda_device_count()` — ingen torch behövs. På
arbetsstationen krävs **noll kodändringar**: byt vid behov `model.name` till
`kb-whisper-large` i `config.toml`, inget mer. Docker gör GUI:t lika portabelt.
