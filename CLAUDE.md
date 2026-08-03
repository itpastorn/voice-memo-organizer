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

### Nuvarande status (2026-08-01)

| Steg | Läge |
| --- | --- |
| **a** transkribering | ✅ enskild fil + batch |
| **b** flaggning (LLM) + granska-GUI + apply | ✅ |
| — runda 2 (Fable) | ✅ byggd, **har inte behövts** sedan prompten utökades |
| **c** språklig förbättring + negationsvakt | ✅ prototyp |
| **d** QDA-taggning | ⬜ kodboken obeslutad |
| **e** SQLite-index | ⬜ |
| **f** metadatataggar på ljudet | ⬜ planerad, ej byggd |

**Sex filer har gått hela vägen a → c**, i fem olika ämnesområden: NAR/politik,
skapelse/evolution, teologi (bokmaterial), AI/teknik, och en kort felfri fil.
Flaggfrekvensen ligger stabilt på **1,3–2 % av orden oberoende av ämne** — det är
en egenskap hos ljudet och modellen, inte hos domänen. Detektorn klarade tre
domäner där ordlistan var helt tom, så en ny temamapp kräver ingen
listinvestering innan pipelinen fungerar.

Testfilen står fortfarande i `data.test_file` och byts för hand. Batch finns för
steg a; steg b och c körs en fil i taget.

**Kvar av arkivet: ~348 av 355 ljudfiler.** Det är den stora återstående
kostnaden, och den blockeras av issue #9 (se Körning).

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

Fem steg (a–e) plus ett löpande (f). Varje steg är idempotent och kan köras om
isolerat.

### a. Transkribering

KBLabs svenska Whisper (`KBLab/kb-whisper-*`) via faster-whisper / CTranslate2,
int8 på CPU.

- **Standardmodell: `kb-whisper-medium`.** `kb-whisper-large` körs på begäran
  för viktiga filer och skriver över medium-utdata.
- Producerar tre filer per ljudklipp: `.json` (fullt Whisper-utdata med
  ord-nivå-tidsstämplar), `.srt`, `.txt`.
- JSON:en är sanningskällan. Allt nedströms härleds ur den.
- Direkt efter transkriberingen körs steg **f** en första gång och sätter
  preliminära metadatataggar på ljudfilen.

**Ordlisteprompt — kända ord matas in i förväg.** Whisper får aldrig memots eget
nyckelord rätt av sig själv: *ungjordskreationism* förvanskades fyra gånger i samma
fil, aldrig likadant; *cessationist* fem gånger i en annan. Prompten byggs per fil av
**`ordlista/gemensam.txt` + `ordlista/<temamapp>.txt`**, kommaseparerat (mätt: 484
tokens mot 541 för samma innehåll radbrutet).

**Skickas som `hotwords`, INTE `initial_prompt`.** Det är inte en detalj:
`initial_prompt` läggs i `all_tokens`, och eftersom `condition_on_previous_text =
false` (KBLabs rekommendation) nollställs den vid varje nytt 30-sekundersfönster —
den når alltså bara filens första halvminut. Uppmätt: en körning med `initial_prompt`
gav resultat **identiskt** med en utan, på varje målterm. `hotwords` injiceras i varje
fönsters prompt och trunkeras dessutom av faster-whisper till samma 223 tokens.

- **Taket är hårt: 223 tokens** (`448 // 2 - 1`). Räkningen sker med modellens *egen*
  tokenizer ur `models/` — teckenuppskattning slår fel med ~10 %, vilket är för mycket
  när gränsen är absolut.
- **Basen prioriteras.** Temamappens termer kapas bakifrån tills det ryms, och det
  som kapas **loggas namn för namn**. En tyst trunkering vore ett osynligt fel:
  termer skulle sluta verka utan att någon märkte det.
- **Inkorgen får bara basen** — temat är okänt tills filen sorterats.
- `transcription.prompt_bas` pekar ut basfilen. Det är utbyggnadspunkten för när
  materialet inte längre är Lars egna memon: en annan talare får en egen basfil,
  ingen kodändring.
- JSON:en bär `ordlista_prompt` och `ordlista_prompt_tema`, annars går två körningar
  av samma fil inte att skilja åt i efterhand.

**Avstängt som standard — tre prov visade ingen nytta.** `ordlista_prompt = false`.
Funktionen finns kvar, men slå inte på den utan att mäta om; nedan är underlaget.

| Prov | Fil | Utfall |
| --- | --- | --- |
| 1 | ungjordskreationism, 22 min | 3 termer rättade (Schweitzer, Krakatoa, Gish); nyckelordets förvanskningar 5 → 4; **inget läckage**; tid 0,8× → 1,5× |
| 2 | provbarhet-5-mos, 50 s, **felfri** | inga ordfel infördes, men interpunktion och segmentering försämrades (`18 .`, punkt inne i en uppräkning); 14 → 10 segment |
| 3 | ai-praktisk-nytta, 8 min, **12 kända fel** | **0 av 12 rättade.** Flera blev sämre |

Prov 3 är det avgörande, eftersom facit fanns på båda sidor. Sex av felen stod
ordagrant i prompten — och **inget** av dem landade:

| Whisper utan prompt | Med prompt | Rätt |
| --- | --- | --- |
| `Lars Dunder` | `Lars Dunther` | Lars Gunther |
| `Clode` | `Clode` (oförändrat) | Claude |
| `NoteGellum` | `Note Gellum` (delat) | NotebookLM |
| `Nordbehandlade` | `en obehandlad rätt` | ordbehandlaren |
| `Richard Reuter` | `Richard Royth` | Rikard Roitto |
| `schysst` | `flykt` | tydligt |

`Nordbehandlade` → *en obehandlad rätt* är det värsta utfallet: nonsens blev
**flytande nonsens**, alltså svårare för både detektorn och ögat att upptäcka.

Slutsats: `kb-whisper-medium` väger sitt akustiska intryck långt tyngre än
hotwords-listan, och 27 termer är sannolikt för trubbigt — hotwords är gjort för en
handfull ord. Ordlisteuppdelningen behålls ändå: den bär **steg b:s** mappscopning,
som redan löst att `Nadia Bolz-Weber` fick detektorn att missa `Shawn Bolz`.

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
`ordlista/` (se nedan) ges som facit — gemensam bas + ljudets temamapp.
Filen chunkas med globala ordindex så
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

**Batch:** `batch-flagga.py` kör flaggningen över många filer och **migrerar
direkt till sidecar**, så de blir granskningsklara i väljaren utan ett extra
skriptanrop per fil. Upptäcker det som saknar flaggning (nyaste först),
uppskattar kostnaden vid `--dry-run`, och är idempotent. Avbryter hela batchen om
ingen bit går igenom — annars lämnas kön halvflaggad vid slut på API-krediter,
vilket redan hänt en gång. Rör inte `current.json`.

**Filstatus "ny transkription behövs".** `zego-josh-hawley-jonathan-edwards`
talades in på **engelska**. KB-Whisper är tränad för svenska och **översatte** i
stället för att transkribera — resultatet är flytande svenska som inte är vad som
sägs i ljudet. Ingenting fångade det automatiskt: ord-konfidensen låg på 0,686 mot
normala 0,70–0,76, och `config.toml` sätter `language = "sv"`, så JSON:ens
`language_probability: 1` betyder ingenting. Det krävs ett mänskligt märke.

Knappen finns i granskningsvyn; märkningen bor i **`granska/status.json`**, som är
versionerad — `granska/state/` är gitignorerad och `/data` monteras read-only med
flit. Märkta filer syns i väljaren och hoppas över av `batch-flagga.py`.

**Omtranskribering är farligare än det ser ut.** Härledda filer från förra körningen
(`-corrections.*`, `-bak*.json`, kopian i `state/`) indexerar den GAMLA texten. Två
skydd finns, och de täcker olika saker:

- `applicera-corrections.py` jämför sidecarens `word_count` med källans ordantal och
  vägrar vid avvikelse — fångar sidecars som blivit ogiltiga.
- `transkribera.py` varnar när härledda filer redan finns — fångar det
  ordantalskontrollen *inte* ser: att `-bak.json` från förra körningen skulle få
  apply att läsa den gamla texten som källa.

**Filväljaren (`granska/valj.php`) är GUI:ts ingång.** Den listar alla
transkriptioner i datamappen, senaste först (`generated_at`, annars filens
ändringstid för äldre försök), med filter på temamapp och filnamn. Kom till när
batchtranskribering gjorde en fil per `current.json` ohållbart.

Två följdbeslut som är lätta att missa:

- **Filer utan sidecar listas och går att öppna.** GUI:t skapar en tom
  arbetskopia; typ 3 (klicka valfritt ord) räcker för att rätta. Alternativet —
  att bara visa förberedda filer — hade gjort nytranskriberat material osynligt
  tills två Python-steg körts.
- **Alla steg efter granskningen följer `granska/current.json`** via
  `korrigeringar.aktuell_json()`, med `config.toml` som fallback:
  `applicera-corrections.py`, `forbattra.py` och `negationsvakt.py`. Utan det blir
  väljaren en fälla — man granskar fil X, applicerar fil Y och producerar en `.md`
  för fil Z. Alla tre skriver ut vilken fil som träffades och varifrån valet kom.

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
Branham, Halldorf, Hagman. **`ordlista/`** samlar dem — `gemensam.txt` plus en fil
per temamapp, namngiven exakt som mappen. Två bruk, samma källa:

- **Steg b (här):** gemensam + temamappens fil, ingen längdgräns. Mappscopningen
  minskar också brus — `Nadia Bolz-Weber` i en global lista fick detektorn att
  missa `Shawn Bolz`. Inkorgen har okänt tema och får därför allt.
- **Steg a (ordlisteprompt):** samma filer, kapade till 223 tokens — men
  **avstängd**, se steg a. Steg b är alltså ordlistans enda aktiva bruk idag.

En term får finnas i flera mappfiler (Bill Johnson hör hemma i både NAR och
god-karismatik) — dubbletter är billigare än fel placering. Saknas en mappfil får
filen bara basen; det är inget fel, bara en mapp som ännu inte körts skarpt.

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

### f. Metadatataggning av ljudfilerna (löper parallellt)

Ljudfilen ska bära sitt eget innehåll. En memofil som hamnar i en mediaspelare,
på telefonen eller i en filhanterare ska visa vad den handlar om utan att någon
öppnar en `.md`. Taggarna är ett **derivat** — de ska kunna sättas om från
grunden ur `.json` + `.md` + kodboken.

**Inte ID3.** 351 av 354 ljudfiler är `.m4a`, som saknar ID3 och i stället bär
MP4/iTunes-atomer (`©nam`, `©ART`, `©alb`, `©cmt`, `©gen`, `©grp`). Två `.mp3`
och en `.aac` tar äkta ID3. `mutagen` hanterar båda bakom samma API, så koden
skiljer på formaten på ett ställe och inte i övrigt.

**Tre tidpunkter.** Samma skript, körs om idempotent; senare körningar skriver
över tidigare värden i de fält som fått nytt underlag.

| När | Underlag | Vad som sätts |
| --- | --- | --- |
| efter **a** | filnamn + JSON | preliminär titel, artist, album, datum, längd |
| efter **c** | `.md` | riktig titel ur rubriken, sammanfattning i kommentaren |
| efter **d** | kodboken | QDA-koder som genre/grupp |

**Fälten:**

- **Titel** — `.md`-rubriken när den finns, annars ljudfilens namn. Preliminära
  titlar ska gå att känna igen som preliminära.
- **Artist** — `Lars Gunther`.
- **Album** — temamappens namn (`NAR-profetrorelsen`, `trump-politik`, ...). Gör
  att mediaspelare grupperar memona per tema utan extra arbete. Memon i inkorgen
  får ingen album-tagg förrän de sorterats.
- **Datum** — inspelningsdatum ur den befintliga `creation_time` (Samsungs
  inspelningsapp sätter den). **Skriv aldrig över den med körningsdatum** — den
  är det enda spåret av när memot faktiskt spelades in.
- **Längd** — ur JSON:ens `duration`.
- **Kommentar** — kort sammanfattning ur steg c. Gör innehållet sökbart i
  filhanterare och mediabibliotek.
- **Genre/grupp** — steg d:s QDA-koder. Väntar på att kodboken beslutas; fältet
  lämnas tomt tills dess i stället för att fyllas med något provisoriskt.

**Originalljudet och taggarna.** Att skriva taggar innebär att containern
skrivs om — därför gäller följande, och inget mindre:

- Ljudströmmen kopieras **bit för bit**; ingen omkodning, aldrig.
- Skrivning sker till tempfil följt av atomiskt byte. Ett avbrott mitt i får
  aldrig lämna en trasig eller halvskriven ljudfil.
- Efter skrivning **verifieras** att ljudströmmen är oförändrad (hash av
  strömmen, inte av filen — containern har ju ändrats). Avviker den, återställs
  tempbytet och körningen avbryts med fel.
- Samsungs befintliga taggar (`creation_time`, `com.android.*`) bevaras.

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
- Steg a är det dyra. Steg b–e är billiga och kan köras när som helst
  (steg b ≈ $0,30/fil, steg c ≈ $0,20 — försumbart bredvid steg a:s CPU-tid).

**Nattkörning fungerar inte som det är nu (issue #9).** Windows *modernt
vänteläge* slog till 18 minuter efter start under en nattkörning och släppte
först åtta timmar senare. Jobbet dog inte — det ströps, vilket är värre: ingen
felutskrift, bara en körning som kröp. `batch-transkribera.py` behöver hålla
`ES_SYSTEM_REQUIRED` under körningen (`SetThreadExecutionState`). Maskinberoende
inställningar hör hemma i koden, inte i ett energischema någon ska minnas att
ändra. Detta blockerar i praktiken genomkörningen av arkivet.

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

**Hastigheten varierar oförklarat och kan inte planeras på (issue #8).** Uppmätt
på medium/CPU/int8, samma maskin och samma inställningar:

| Kvot vägg/ljud | Omständighet |
| --- | --- |
| 0,56× | den ursprungliga referensmätningen |
| 0,76×–0,82× | 22-minutersfil, dagtid |
| 0,99× | 50-sekundersfil (fast overhead väger tungt på korta filer) |
| 1,25×–1,31× | 8- och 15-minutersfiler |
| 1,52×–1,99× | med ordlisteprompt (hotwords) |

Spannet är **3,5×** mellan bästa och sämsta. **Räkna inte på 0,56× när arkivet
planeras** — skillnaden mellan 0,8× och 1,5× är ungefär hundra timmar CPU på de
~348 återstående filerna. Hypotes värd att pröva: `cpu_threads = 0` (auto) låter
CTranslate2 välja trådantal utifrån maskinens tillfälliga last. Ett explicit
värde skulle göra mätningarna jämförbara.

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
    zego-bill-johnson-fallen.m4a            ← original; ljudet orört, taggar ur f.
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
- **Rör aldrig originalljudet.** Regeln gäller ljud*innehållet*: ingen omdöpning,
  ingen omkodning, ingen radering. Allt annat är återskapbart; ljudet är det inte.
  **Enda undantaget är metadatataggar (steg f)** — ljudströmmen kopieras då bit
  för bit, skrivningen sker via tempfil + atomiskt byte, och strömmen verifieras
  oförändrad efteråt. Ingen annan skrivning i ljudfilen är tillåten.
- **`test/` och `sammanfatta/` är utanför projektet.**
- Whisper-JSON:en är sanningskällan. Bygg allt annat som derivat, och se till att
  det går att bygga om.
- Steg c får förbättra läsbarheten. Den får inte skriva om Lars.
- Kör inte batch-transkribering utan att Lars vet om det. Det blockerar datorn.
- **Bygg bara det aktuella steget.** Föregrip inte kommande steg med abstraktioner
  som ingen ännu behöver.
- **Men anta aldrig CPU.** Se Portabilitet. Maskinberoende val hör hemma i
  konfigurationen, aldrig i kodens kropp.
- **Mät i stället för att anta — och rapportera utfallet ärligt även när det är
  negativt.** Ordlisteprompten byggdes färdig, mättes i tre prov och stängdes av;
  `initial_prompt` såg ut att fungera tills en diff visade att den inte gjorde
  någonting alls. Ett par av granskningens viktigaste fynd kom ur att en mätning
  motsade förväntan. Skriv ner det underkända underlaget i CLAUDE.md, annars
  byggs samma sak om av nästa session.
- **Rör aldrig granskat material vid experiment.** Kör mot en kopia, eller säkra
  facit först och återställ efteråt. Sanningskällan är dyrast av allt i projektet:
  den kostar både CPU-tid och Lars ögon.

## Öppna frågor

1. **Kodboken.** Vilka QDA-koder? Hierarkiska eller platta? Vem sätter dem —
   LLM:en fritt, eller LLM:en mot en fast lista som Lars godkänner? Blockerar
   steg d, och därmed genre/grupp-fältet i steg f.
2. **Blockkoder i markdown.** Frontmatter räcker för dokumentnivå. Hur märks
   enskilda block? HTML-kommentarer, en parallell `.codes.json`, eller något
   annat?
3. **Sortering.** Ska pipelinen föreslå vilken temamapp ett memo i inkorgen hör
   hemma i, eller gör Lars det för hand? Har fått vikt: temamappen styr numera
   både ordlisteurvalet i steg b och album-taggen i steg f, så en osorterad fil
   får sämre stöd.
4. **De hundratal memon som redan finns.** ~348 av 355 återstår. Blockeras av
   issue #9 (vänteläget) och försvåras av issue #8 (oförutsägbar hastighet).
5. **Hur hjälper man Whisper med ovanliga ord?** Ordlisteprompt via hotwords är
   prövad och underkänd (se steg a). Kvar att pröva: `kb-whisper-large` på
   arbetsstationen, revision-diff som flaggkälla (issue #5), eller att helt
   acceptera att felen fångas i steg b.

## Öppna GitHub-issues

Läs dem innan planering — `gh issue list`. De bär beslut och prioritering som
inte står här.

| # | Vad | Läge |
| --- | --- | --- |
| 5 | Revision-diff (standard vs strict) som extra flaggkälla | idé, väntar |
| 7 | Konsistensvakt: samma namn förvanskat olika, bara ett flaggat | verklig lucka |
| 8 | Väggklockemätningen räknar in sömn; hastigheten oförutsägbar | mätproblem |
| 9 | Modernt vänteläge stryper nattbatch | **blockerar arkivet** |
| 10 | Ordlistan per temamapp | ✅ genomförd i denna omgång |
