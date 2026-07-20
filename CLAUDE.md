# CLAUDE.md – voice-memo-organizer

## Projektbeskrivning

Lars Gunther spelar in röstmemon — teologiska resonemang, predikoutkast, idéer,
polemik — och laddar upp dem som ljudfiler. Detta projekt bygger en pipeline som
gör memona sökbara: transkriberar dem, städar språket utan att förlora Lars röst,
temataggar innehållet och indexerar allt i en databas.

Slutmålet är att kunna ställa frågor som *"berätta allt jag spelat in om Bill
Johnson"* eller *"vilka kopplingar har jag gjort mellan Trump och antikrists
ande?"* och få tillbaka rätt memon med tidsstämplar.

Detta är ett omtag. Tidigare försök finns kvar och ska läsas som erfarenhet,
inte som kod att återanvända rakt av. Se **Tidigare försök** nedan.

## Arbetssätt

**Stegvis.** Ett steg i taget, körbart och verifierat innan nästa påbörjas. Bygg
inte steg b–e i förväg. Låt inte "det behövs sedan" motivera kod som inte behövs nu.

**Utveckling sker i VSCode** med Claude-tillägget. Denna CLAUDE.md är den
gemensamma kontexten mellan sessioner och verktyg.

### Nuvarande steg: 1 av N — transkribering av en enda fil

Ett Python-skript som transkriberar **en hårdkodad testfil**:

```
transkribera/zego-teologisk-samling-i-grupper.m4a   (~3,4 MB, kort)
```

Skriptet ska producera `.json`, `.srt` och `.txt` bredvid ljudfilen. Inget
CLI-argument, ingen mappgenomsökning, ingen kö. Filsökvägen står i konfigurationen
och byts för hand.

Målet med steget är att se vad `kb-whisper-medium` faktiskt levererar på Lars
ljud och hur lång tid det tar. Kvaliteten på utdata avgör nästa steg.

## Portabilitet — laptop idag, arbetsstation imorgon

Lars kommer att byta från denna laptop (CPU, ingen CUDA) till en stationär dator
med rejält med VRAM. **Allt vi skriver ska flytta över utan omskrivning.**

Konkret:

- **Ingen hårdkodad `device` eller `compute_type`.** De läses ur konfiguration med
  autodetektering som standard: finns CUDA → `cuda` + `float16`, annars `cpu` +
  `int8`. En körning på arbetsstationen ska kräva noll kodändringar.
- **Modellstorleken är konfiguration, inte konstant.** `kb-whisper-medium` idag,
  `kb-whisper-large` när VRAM finns. Samma kodväg.
- **All maskinberoende inställning på ett ställe** — en `config.py` eller
  `config.toml` i projektroten. Trådantal, batchstorlek, `beam_size`, modellcache.
- **Inga absoluta sökvägar utanför konfigurationen.** Datamappens rot är en
  inställning, inte ett strängliteral spritt genom koden.
- **Mät och logga.** Varje körning loggar modell, device, compute_type, ljudlängd
  och väggklockstid. Då blir jämförelsen laptop/arbetsstation ett faktum och inte
  en gissning.
- Skriv inget som *bara* fungerar på CPU (t.ex. antaganden om att allt får plats i
  RAM, eller sekventiell bearbetning som förutsätter att GPU-parallellism aldrig
  blir aktuell).

## Datamapp

Ljudet ligger **inte** i detta projekt utan i:

```
C:\Users\gunther\Dropbox\arkiv\mediadev\transkribera\
```

- **Rotmappen är inkorgen.** Nya, osorterade `zego-*.m4a` hamnar här.
- **Undermapparna är temataxonomin.** `trump-politik/`, `NAR-profetrorelsen/`,
  `wimber-vineyard/`, `helande-dunamis/`, `bibelsyn-lib-fund-equmeniakyrkan/`,
  `israel-palestina-antisemitism/`, `skapelse-evolution-vetenskap-apologetik/`,
  `Kirk-TPUSA-kristen-nationalism/`, `god-karismatik-egna-boken/`,
  `ideer-predikoutkast-minnesanteckningar/`, `andra-ideer-teologi-substack-webb/`,
  `meta-admin-todo-fix/`.
- **Undantag — rör inte:** `test/` och `sammanfatta/` ingår inte i projektet.
  `sammanfatta/` tillhör YouTube-sammanfattningsskillen.

Ljudfilerna är mestadels `.m4a`, några `.mp3` och `.aac`. Prefixet `zego-` kommer
från inspelningsappen och behålls.

Befintliga ljudfilnamn följer *inte* namnkonventionen nedan (`zego-Trump-akrist-1.m4a`
har versaler). Ljudet döps inte om utan att Lars ber om det. Konventionen gäller
allt vi **producerar**.

## Pipeline

Fem steg. Varje steg är idempotent och kan köras om isolerat.

### a. Transkribering

KBLabs svenska Whisper (`KBLab/kb-whisper-*`) via faster-whisper / CTranslate2,
int8 på CPU.

- **Standardmodell: `kb-whisper-medium`.** `kb-whisper-large` körs på begäran
  för viktiga filer och skriver över medium-utdata.
- Producerar tre filer per ljudklipp: `.json` (fullt Whisper-utdata med
  ord-nivå-tidsstämplar), `.srt`, `.txt`.
- JSON:en är sanningskällan. Allt nedströms härleds ur den.

### b. Flaggning av oklarheter

Låg konfidens flaggas för manuell rättning — men **inte** ur ord-konfidens.
`kb-whisper-medium` ger genomgående låga ord-`probability` på Lars ljud (median
~0,69), och de flesta felen i Lars stora felklass — egennamn och engelska
låneord — får *hög* konfidens (modellen är tryggt fel: `dik`=0,91 för "geek").
Ord-konfidens korrelerar alltså bara löst med faktiska fel; en tröskel på den
missar systematiskt just det man bryr sig om. Detektorn är i stället en LLM.

**Detektor: `flagga-llm.py`.** Claude (`claude-opus-4-8`, structured output) läser
transkriptet bit för bit och flaggar misstänkta ord *semantiskt* — nonsensord,
förvanskade namn, engelska ord renderade som svenska, riktiga ord på fel plats.
`ordlista.txt` (se nedan) ges som facit. Filen chunkas med globala ordindex så
inga index behöver mappas om. Verifierat: fångar alla kända fel (Dunper→Gunther,
dik/gik→geek, punkt→uns) som percentil missade.

**Percentil-varianten `generera-corrections.py`** finns kvar som billig, offline,
nyckelfri jämförelse: flaggar de lägsta X % efter ord-konfidens (`flag_percentile`
i config). Inte primär — den bekräftar bara varför ord-konfidens inte räcker.

Utdata är en `<namn>-corrections.txt` i det format Lars redan använt, block för
block. Kontextraden är 7–10 ord runt de flaggade orden; varje flaggat ord får
ordets starttid som **ankare** (`@37.86`) ur JSON — ett entydigt fäste som
apply-steget använder för att hitta exakt rätt ord. Högerledet lämnas tomt åt
Lars; LLM:ns gissade rättelse + skäl läggs som `#`-kommentar efter ankaret:

```
"context": "Det är liksom fortfarande Lars Dunper som står där. Det"
Dunper=              @37.86   # Gunther? (förvanskat namn, ska vara Lars Gunther)
punkt=               @65.64   # uns? (fel ord i sammanhanget)
```

Lars redigerar filen för hand: `hört=rättat` (korrigering), `hört=hört`
(bekräftar rätt), `hört=DELETE` (tar bort ordet), eller tomt (ej granskat än —
JSON rörs inte). Granskningen sker numera i webb-GUI:t (`granska/`, PHP i
Docker): `migrera-corrections.py` gör .txt:en till en strukturerad sidecar,
GUI:t skriver besluten, och `applicera-corrections.py` skriver dem in i JSON:en
och sätter `probability = 1.0` på granskade ord.

**Runda 2 (frivillig): kontextgranskning med Claude Fable.** Subtila fel
överlever runda 1 — riktiga ord fel i sammanhanget ("få *råd* av Gud" → nåd,
"ditt eget *innehåll*" → inre), bortfallna ord (negationer, namnattributioner;
issue #1) och normaliserade bibelcitat som avviker från Bibel 2000 (issue #2).
`granska-igen.py` läser den **rättade** JSON:en (kräver `corrections_applied_at`),
skickar den till `claude-fable-5` (`corrections.runda2_modell`) med prompt riktat
mot de tre felklasserna, och skriver sidecaren `<namn>-corrections-2.json`
direkt — inget .txt-mellansteg. Granskning i samma GUI, apply med samma skript.

Versionskedjan gör varje runda idempotent:

```
<namn>-bak.json    orört Whisper-original (rörs aldrig)
<namn>-bak2.json   ögonblicksbild av runda 1-resultatet = runda 2:s bas
<namn>.json        alltid senaste sanningen (skrivs om av apply)
```

Sidecaren bär `"runda"` och `"base_json"`; apply läser basen därifrån —
`global_index` refererar alltid basens ordpositioner. Bortfallsflaggor får tom
`ai_guess` (GUI:ts förslag-knapp ersätter ord, vilket vore fel) och förslaget i
skälet; Lars infogar med `i`/`Shift+i`.

Egennamn är den stora felkällan: Wimber, Bolz-Weber, Talarico, Bickle, Feucht,
Branham, Halldorf, Hagman. **`ordlista.txt`** i projektroten samlar dem och föder
två bruk: LLM-detektorn (ingen längdgräns) och — senare — `initial_prompt` till
Whisper i steg a (max ~224 tokens, så hela listan får inte plats när den vuxit;
då krävs prioritering eller per-temamapp-prompt).

Delade byggstenar (config, ordlista, kontextfönster, ankare, blockformat) bor i
`korrigeringar.py`. API-nyckeln läses ur gitignorerad `.env` (`ANTHROPIC_API_KEY`).

### c. Språklig förbättring

En LLM (Claude API, anropad direkt från skriptet) städar transkriptionen: tar bort
utfyllnadsord och falska starter, sätter interpunktion, delar i stycken.

**Lars stil och formuleringar ska överleva.** Detta är inte en omskrivning. Är
meningen begriplig som den är, ska den stå kvar.

**Icke-innehåll tas bort — och det är inte omskrivning.** Skilj två saker: att
städa språket i memot (ovan), och att ta bort material som aldrig hörde till memot.
Det senare ska **föreslås för radering**, inte tyst kapas: avslutande skräp efter
att föredraget är slut (Lars spelar in med hörapparater; efter t.ex. *"Tack så
mycket, stort tack till dig Lars"* är resten publikinteraktion av usel kvalitet som
transkriberas till nonsens), och inskjutna avbrott mitt i — hundtilltal, eller
hälsningar till en bekant han möter. Detta krockar inte med regeln ovan: det
raderade är inte Lars innehåll. Instruktionen till LLM:n måste ta denna hänsyn, och
det borttagna ska gå att granska (skäl anges, samlas i en sidofil).

Producerar två nya filer med egna namn (originalen bevaras):

- **`*-korrigerad.srt`** — samma segmentindelning, städad text.
- **`*.md`** — markdown med underrubriker och listor där innehållet motiverar det.
  Tidsangivelser sätts ut per **block**, inte per mening, som `[00:04:12]` före
  varje avsnitt. Ett block ska gå att slå upp i ljudfilen.

**Negationsvakt (`negationsvakt.py`, issue #4).** Steg c:s risk är att städningen
gör tappade negationer osynliga — flytande text ser rätt ut, och en LLM som
granskar sitt eget utflöde ger falsk trygghet. Efter steg c körs därför en
deterministisk kontroll utan API-anrop: negationsorden (*inte, aldrig, ingen,
inget, inga, utan, icke*) räknas per block i `.md`:n respektive i JSON-segmenten
i samma tidsfönster; avvikelse ger en tidsstämplad rad och exit-kod 1. Falska
positiva är acceptabla — varje polaritetsändring ska kräva ett medvetet mänskligt
beslut.

### d. QDA-taggning

Markdownfilen temataggas enligt QDA-metodik (Qualitative Data Analysis — kodning
av textmaterial med teman). Koder sätts på blocknivå, inte bara dokumentnivå,
så att en sökning pekar på rätt ställe i memot.

- **Kodboken** ska definieras explicit och versioneras i projektet. Undermapparnas
  namn i `transkribera/` är utgångspunkten men räcker inte — de är för grova.
- Koder lagras i markdownfilens YAML-frontmatter (dokumentnivå) och som inline-
  annotation eller sidokartfil (blocknivå). **Beslut ej fattat.**
- Personnamn (Bill Johnson, Wimber, Trump) och begrepp (antikrists ande,
  dekret och deklarationer, meliorism) är båda koder men bör kunna skiljas åt.

### e. SQLite-index

En SQLite-databas indexerar alla transkriptioner så att Lars kan hitta allt som
berör en viss QDA-kod.

- FTS5 för fulltextsökning över blocken.
- Kopplar kod → block → tidsstämpel → ljudfil.
- Databasen är ett **derivat**. Den ska kunna byggas om från grunden ur
  `.json` + `.md` utan förlust.

Gränssnittet väntar. Det blir troligen en lokal webbserver i PHP.

## Framtid — utanför scope nu

RAG-databas ovanpå indexet, för att kunna ställa frågorna i naturligt språk i
stället för att söka på koder. Kräver ny hårdvara. Bygg inget för detta nu, men
låt inget beslut omöjliggöra det — särskilt inte blockindelningen i steg c, som
är de facto chunkning.

## Körning

**Batch, inte realtid.** Se hårdvaran nedan. Filbevakning som triggar
transkribering direkt vid uppladdning är avfärdat — en `kb-whisper-medium`-körning
äter datorn i minuter.

- Ett skript upptäcker ljudfiler som saknar utdata och köar dem.
- Körs manuellt eller via Windows Task Scheduler, lämpligen nattetid.
- Steg a är det dyra. Steg b–e är billiga och kan köras när som helst.

## Hårdvara

Nuvarande maskin (laptop). **Detta är ett tillfälligt tak, inte en förutsättning** —
se Portabilitet ovan.

| | |
|---|---|
| CPU | Intel i7-1355U, 10 kärnor / 12 trådar |
| RAM | 32 GB |
| GPU | Ingen dedikerad. Intel Iris Xe. |
| ffmpeg | Finns i PATH |
| Python | 3.12 |

Ingen CUDA. Whisper körs int8 på CPU. `kb-whisper-large` landar kring realtid
eller långsammare — en halvtimmes memo tar en halvtimme eller mer. Därför medium
som standard.

Planerad maskin: stationär med gott om VRAM. Då blir `kb-whisper-large` +
`float16` på `cuda` standardvalet, och batch nattetid blir onödigt.

## Tidigare försök

I `transkribera/` finns spår av två tidigare ansatser. Läs dem innan du bygger.

- **`texta-mig/`** — srt/txt/vtt från en körning. Visar utdataformat.
- **`andra-ideer-teologi-substack-webb/transcripts/json/`** — Whisper-JSON med
  ord-nivå-tidsstämplar, en `.bak.json`, och `zego-Adam-Abraham-corrections.txt`.
  Korrigeringsformatet därifrån ärvs (se steg b).
- **`helande-dunamis/*.json`** — fler Whisper-JSON:er.
- **`bygg-rapport.py`** och **`SKILL.md`** i roten tillhör
  sammanfatta-youtube-video-skillen, **inte** detta projekt. Rör dem inte.

## File Naming Convention

All output files must follow these normalization rules:

1. Lowercase everything
2. Convert spaces and underscores to hyphens
3. Transliterate non-ASCII characters to ASCII equivalents
    (e.g. å→a, ä→a, ö→o, accented letters → base letter)
4. Remove all characters that are not `a-z`, `0-9`, or `-`
5. Collapse consecutive hyphens into one
6. Strip leading and trailing hyphens

Never use underscores anywhere in filenames or project filenames.

## Struktur

Utdata läggs **bredvid ljudfilen, i samma temamapp**. Ett memo och dess
transkriptioner är grannar.

```
transkribera/NAR-profetrorelsen/
    zego-bill-johnson-fallen.m4a            ← original, orört
    zego-bill-johnson-fallen.json           ← a. Whisper, sanningskällan
    zego-bill-johnson-fallen.srt            ← a.
    zego-bill-johnson-fallen.txt            ← a.
    zego-bill-johnson-fallen-corrections.txt ← b. Lars redigerar för hand
    zego-bill-johnson-fallen-korrigerad.srt  ← c.
    zego-bill-johnson-fallen.md              ← c. + d. taggad
```

Skriptet självt, kodboken och databasen bor i detta projekt
(`voice-memo-organizer/`), inte i datamappen.

## Instruktioner för AI-assistenten

- **Svenska.** Lars arbetar på svenska. Kod och kommentarer likaså, om inget annat
  sägs.
- **Rör aldrig originalljudet.** Ingen omdöpning, ingen konvertering på plats,
  ingen radering. Allt annat är återskapbart; ljudet är det inte.
- **`test/` och `sammanfatta/` är utanför projektet.**
- Whisper-JSON:en är sanningskällan. Bygg allt annat som derivat, och se till att
  det går att bygga om.
- Steg c får förbättra läsbarheten. Den får inte skriva om Lars.
- Kör inte batch-transkribering utan att Lars vet om det. Det blockerar datorn.
- **Bygg bara det aktuella steget.** Föregrip inte kommande steg med abstraktioner
  som ingen ännu behöver.
- **Men anta aldrig CPU.** Se Portabilitet. Maskinberoende val hör hemma i
  konfigurationen, aldrig i kodens kropp.

## Öppna frågor

1. **Kodboken.** Vilka QDA-koder? Hierarkiska eller platta? Vem sätter dem —
   LLM:en fritt, eller LLM:en mot en fast lista som Lars godkänner?
2. **Blockkoder i markdown.** Frontmatter räcker för dokumentnivå. Hur märks
   enskilda block? HTML-kommentarer, en parallell `.codes.json`, eller något
   annat?
3. **Sortering.** Ska pipelinen föreslå vilken temamapp ett memo i inkorgen hör
   hemma i, eller gör Lars det för hand?
4. **De hundratal memon som redan finns.** Körs de igenom retroaktivt? Det är
   många timmar CPU.
