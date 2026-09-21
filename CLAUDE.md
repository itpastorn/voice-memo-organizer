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

**Tio filer till har gått a → c** (Kirk-TPUSA-mappen, 2026-08-13). Totalt alltså
16 filer hela vägen. Negationsvakten gav sitt **första verkliga utslag** i den
omgången: steg c tappade *och inte* i `zego-trump-forlatelse-2` och vände därmed
en sats. Av fem flaggade block var ett verkligt tapp, ett en korrekt städad
stamning (*"inte... inte längre"* → *"inte längre"*), och ett en rekonstruktion av
obegriplig text. Kvoten motiverar vakten: den är gratis och fångar det ingen
LLM-självgranskning skulle se.

Batch finns för **alla** körbara steg: a, b, apply och c. `data.test_file` byts
för hand men läses numera bara av steg a — allt efter granskningen följer GUI:ts
filval (`aktuell.py` visar vilken det är).

**Namnvakten spärrar sex filer** (`namnvakt.py`) — se File Naming Convention.
Ett av fallen dolde 47 minuter ljud som aldrig kunnat transkriberas.

**Arkivet är till största delen transkriberat (2026-09-16).** 286 transkript, varav
201 med `small` (körda 2026-09-05–06) och 82 med `medium`. **Kvar: 80 ljudfiler,
17,8 timmar** — mätt med `ffprobe`. Fyra av dem ligger i `incoming/`.

**Helhetsflödet ändrades 2026-09-16** — nya inspelningar går via `incoming/` och
sorteras sist. Se Pipeline.

## Portabilitet — laptop idag, arbetsstation imorgon

Lars kommer att byta från denna laptop (CPU, ingen CUDA) till en stationär dator
med rejält med VRAM. **Allt vi skriver ska flytta över utan omskrivning.**

Konkret:

- **Ingen hårdkodad `device` eller `compute_type`.** De läses ur konfiguration med
  autodetektering som standard: finns CUDA → `cuda` + `float16`, annars `cpu` +
  `int8`. En körning på arbetsstationen ska kräva noll kodändringar.
- **Modellstorleken är konfiguration, inte konstant.** `kb-whisper-small` idag,
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

- **`incoming/` är inkorgen** (`data.incoming` i config.toml). Nya inspelningar
  laddas upp här och ligger kvar genom hela kedjan tills de sorteras. Roten
  innehåller inget ljud längre, bara skillfiler som inte hör till projektet.
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

Varje steg är idempotent och kan köras om isolerat.

### Helhetsflödet (sedan 2026-09-16)

```text
incoming/  →  zego-prepare  →  normalisering  →  a transkribering
           →  b flaggning + granskning  →  c förbättring
           →  f metadatataggar + d QDA-taggning
           →  sortering till temamapp (förslag, Lars godkänner)
           →  e databas, RAG ...
```

Den stora förändringen mot tidigare är att **sorteringen sker sist**. Filerna
ligger i `incoming/` genom hela kedjan och flyttas en gång, när allt är klart.

| Beslut | |
| --- | --- |
| Normalisering mot "rör aldrig originalljudet" | normaliserad **kopia**; originalet orört |
| Vilken fil är ljudfilen nedströms | **originalet**; den normaliserade kastas efter steg a |
| Var filerna ligger under bearbetning | kvar i `incoming/` tills sorteringen |
| Sorteringen | **föreslår, Lars godkänner** |

**Byggt:** `incoming/` som inkorg, och sorteringen (`sortera.py`, se nedan).
**Väntar:** normaliseringen (källan finns, men nyttan är omätt — se nedan).

### Sorteringen (`sortera.py` + `sortering.toml`)

**Ett träd, inte en kedja.** Lars beskrev det själv i ett memo: grenarna följer
hans huvudintressen, och det som kommer först får företräde när ett memo rör
flera ämnen. Ordningen är 1 Meta, 2 Skumt och dumt (NAR / Kirk-TPUSA /
trump-politik), 3 Israel, 4 Skapelse, 5 God karismatik (wimber / helande /
god-karismatik), 6 Bibelsyn, 7a Egna texter, 7b Idéer.

Meta ligger först av ett skäl som bara Lars kunde veta: ett metamemo nämner
andra grenars kodord i förbigående — memot som gav upphov till hela den här
funktionen säger "finns det något i wimber-vineyard så ska den till den mappen".

**Skriptet flyttar aldrig något självt.** Det föreslår; Lars godkänner en fil i
taget med `--flytta <stam>`. Reglerna bor i `sortering.toml` och är gjorda för
att ändras — `sortera.py --mat` mäter om mot hela arkivet efter varje ändring.

**Uppmätt (286 sorterade transkript).** Förslag ges för 89 % av filerna, och
55 % av dem är rätt. Spridningen är poängen:

| Mapp | Rätt av förslagen |
| --- | ---: |
| trump-politik, skapelse-evolution | 100 % |
| NAR-profetrorelsen | 92 % |
| helande-dunamis | 82 % |
| israel-palestina | 75 % |
| meta-admin | 67 % |
| Kirk-TPUSA | 53 % |
| bibelsyn | 22 % |
| god-karismatik | 11 % |
| andra-ideer | 8 % |

**Vägen dit, så att ingen gör om försöken:**

- *Första träffen vinner*, som memot beskrev det, gav **36 %**. Den som ligger
  först stjäl: `god-karismatik` fick 0 av 30.
- *Räkna träffar i stället* gav **55 %** på grennivå 64 %.
- *Täthetskrav* ("nämns minst N gånger") gav ingen vinst alls — det flyttar bara
  felet mellan grenarna. Vid krav på 8 omnämnanden går slasken från 35 % till
  91 % medan gren 2 faller från 89 % till 36 %.
- *Normalisering* för olika många nyckelord per mapp gav **43 %** — sämre.

**Lövvalet inom en gren är en inställning, för grenarna är olika.** Prioritet
(första lövet med träff) är rätt i gren 2: ett Kirk-memo kan handla mycket om
Trump utan att vara ett Trump-memo, och det lyfte Kirk från 39 % till 53 %.
Poäng är rätt i gren 5: `wimber` finns i 22 filer medan mappen har två, så
prioritet lät det lövet äta grenen och `helande-dunamis` föll från 82 % till
59 %. `lovval = "poang"` i TOML-filen styr det.

**Det som inte går att lösa med nyckelord.** `god-karismatik` överlappar NAR
oåterkalleligt — CLAUDE.md konstaterade redan att Bill Johnson hör hemma i båda.
`andra-ideer` saknar eget ordförråd: publiceringsorden Lars föreslog (`substack`,
`artikel`, `del två`) täcker 22 % av mappen med 24 % precision, och `del
två/tre/fyra` förekommer aldrig — Whisper skriver siffror.

**Det som ska göra sorteringen bra över tid är inte algoritmen utan vanan.**
Säger Lars i memots första mening *"den här inspelningen handlar om NAR"* avgörs
saken direkt, oavsett allt annat. `[uttalat]` i TOML-filen innehåller fraserna.
Ingen fil i arkivet har det ännu; regeln finns på plats för att den ska kunna
börja användas.

Flytten tar hela familjen — ljud, transkript och allt härlett — i ett svep, och
vägrar när ett målnamn redan finns. Arbetskopian i `granska/state/` är platt och
behöver inte flyttas.

**`zego-prepare`** är ett Git Bash-alias för
`workspace/adminscripts/zego-prepare.sh` — ett annat repo, inte en del av det här
projektet. Det döper **bara om** `.m4a`-filerna i en mapp, i tre steg: stryker
inledande "Lars Gunther", normaliserar namnet via `normalize-filenames.sh`, och
lägger till `zego-`. Ljudinnehållet rörs inte, och "normalisera" i skriptet
betyder filnamn, inte ljud.

Allt skriptet producerar är en fixpunkt för projektets `normalize_stem()` —
uppmätt på sex knepiga titlar — så pipelinen och skriptet är överens om namnet
**efteråt**. På råa titlar skiljer reglerna sig däremot: adminskriptet ersätter
otillåtna tecken med bindestreck, projektets regel stryker dem (`don't panic` →
`zego-don-t-panic` respektive `zego-dont-panic`). **Ordningen är därför ett krav:
förbered först, transkribera sedan.** Transkriberas en fil innan den förberetts
får transkriptet en annan stam än ljudet får efteråt, och de härledda filerna blir
föräldralösa (det `synka-namn.py` finns för att laga).

**Två fel hittade 2026-09-16, provade på testfiler. De bor i adminscripts:**

- **Namnkrock raderade en inspelning — åtgärdat 2026-09-17**, sammanslaget i
  adminscripts `main` 2026-09-19. Steg 1 och 3 gjorde `mv` utan att kontrollera om
  målet fanns. Prov: fem testfiler in, tre ut, exit 0 och "Klar." —
  `generation.m4a` skrev över en *annan* `zego-generation.m4a`, och
  `Lars Gunther predikan.m4a` skrev över en *annan* `predikan.m4a`. Det var det
  enda i hela flödet som kunde förstöra originalljud.

  Nu räknar `zego-prepare` ut varje fils **slutnamn innan något flyttas**, och
  vägrar hela körningen (exit 1, ingenting omdöpt) om två filer skulle hamna på
  samma namn. Rapporten säger om de krockande filerna är byte-identiska
  (`cmp -s`) — en dubblett kan raderas, olika innehåll är en inspelning som hade
  gått förlorad. Regeln för steg 2 hämtas från `normalize-filenames.sh --namn`,
  ett nytt läge, så att den bara finns på ett ställe. Dessutom `--dry-run` och ett
  skyddsnät i själva flytten. Verifierat: omstruktureringen av
  `normalize-filenames.sh` gav bit för bit identisk utskrift på 28 knepiga namn,
  och krockfallet lämnar alla fem filer orörda.
- **Undantagslistan ger versaler.** `normalize-filenames.sh` behåller etablerade
  versalnamn (README, TODO, **MEMORY**, SECURITY, …). Ett memo med titeln
  "Memory" blev `zego-MEMORY.m4a`. Ofarligt för pipelinen — utdata normaliseras
  ändå, och namnvakten varnar — men listan är gjord för projektfiler, inte memon.

Molnvarningen i skriptet (namnbyte i synkad mapp kan ge 0 B-filer) känner igen
Google Drive, OneDrive och iCloud men **inte Dropbox**, där `incoming/` ligger.
Om Dropbox har samma problem är inte prövat.

**Felet som flödet utlöste.** `temamapp_for()` returnerade första sökvägsdelen,
så en fil i `incoming/` fick temat `"incoming"`. `load_ordlista("incoming")`
letade då efter `ordlista/incoming.txt`, hittade den inte och gav **bara basen —
8 termer i stället för 132**. Tyst, i alla fyra skript som flaggar
(`flagga-llm`, `batch-flagga`, `granska-igen`, `propagera-namn`), och det hade
slagit till på de fyra första filerna i `incoming/`. Rättat: inkorgen ger `None`,
och `None` ger hela listan — det dokumenterade inkorgsbeteendet. Verifierat att
ingen av de 286 sorterade filerna fick ändrat tema.

**Designkrav för normaliseringen**, låsta innan steget kommer:

- En **intern detalj i steg a**: ffmpeg till tempfil → transkribera → radera.
  `audio_file` pekar på originalet.
- **Längden måste bevaras.** Ändrar filterkedjan ljudets längd stämmer ordens
  tidsstämplar inte längre mot originalet: GUI:ts ljudloop spelar fel ställe, och
  `synka-namn.py`:s längdbevis (JSON:ens `duration` mot `ffprobe` på originalet)
  slutar fungera. `loudnorm` bevarar längden, `silenceremove` gör det inte. Mät
  med `ffprobe` efteråt och vägra vid avvikelse över ~0,1 s.
- JSON:en bär filterkedjan (`normalisering`), annars går en normaliserad och en
  onormaliserad körning inte att skilja åt.

**Normaliseringen finns att låna, men mätningen talar emot att den behövs här
(2026-09-16).** Källan är `ljud.py` i hestra-projektet, med en lånesinstruktion i
dess `docs/lan-ljudsteget-till-voice-memo-organizer.md`: tvåpass `loudnorm`
(−16 LUFS, −1,5 dBTP, LRA 11) efter förfiltret `highpass=f=80` + lätt kompressor,
självrättande offset, mätning av resultatet, och markering av trasiga
inspelningar (under −50 LUFS eller under 1 LU). Instruktionen säger själv att
målvärdena är satta för predikningar i kyrksal och ska mätas på memon först.

Pass 1 på 20 memon (de fyra i `incoming/` plus 16 slumpvalda):

| | Röstmemon | Hestra-predikningar |
| --- | --- | --- |
| Loudness | median **−24,1 LUFS**, spann −27,4 … −21,6 | ~−35 LUFS, ner mot −40 |
| Dynamiskt omfång | median **3,5 LU**, spann 2,1 … 8,1 | ~8 LU |
| "Trasiga" | 0 av 20 | förekommer |

Memona är redan jämna, starka och hårt komprimerade — sannolikt telefonens egen
nivåreglering plus närmikrofon. Problemet normaliseringen löser i hestra, mycket
tysta källor med varierande avstånd till mikrofonen, finns knappt här. Kvar blir
~8 dB ren förstärkning, som Whispers log-mel-steg är i stort sett okänsligt för,
och mer kompression på redan komprimerat ljud.

**Normaliseringens effekt på transkriberingen är inte mätt någonstans** — inte
heller i hestra, där den normaliserade filen också är en lyssningsprodukt. Här
kastas den efter steg a, så dess *enda* syfte vore bättre transkribering. Samma
läge som ordlisteprompten: bygg inte in den förrän den visat nytta.

**Beslut 2026-09-17: normaliseringssteget byggs inte.** Flödesskissen behåller det
som en plats, men kedjan går `incoming/` → `zego-prepare` → steg a tills något
talar för motsatsen.

Längdkravet håller: kedjan gav **+31 ms** på en fil om 33 min, lika i mp3 och i
wav 16 kHz mono — avvikelsen kommer alltså från filterkedjan, inte från
kodningen. Blir steget av är wav 16 kHz mono rätt tempformat: förlustfritt, och
det Whisper ändå räknar om till.

**Temagissning före flaggningen — prövad och underkänd.** Planen var att en fil i
`incoming/` skulle få en preliminär temagissning som valde ordlista, eftersom
scopningen är uppmätt att spela roll (se steg b, Bolz). Deterministisk
träffräkning: hur många termer ur varje `ordlista/<mapp>.txt` som förekommer i
transkriptet. Mätt mot de 286 sorterade transkripten, med två facitregler — en fil
i en mapp med lista ska få den listan; en fil i en av de fem mappar som saknar
lista ska få `None` (hela listan), eftersom en annan mapps lista vore ett fel.

Felen är inte symmetriska, och det avgör: `None` ger hela listan (brusigare, men
inget missas), medan **fel lista tar bort mappens egna namn** — precis den skada
scopningen skulle förebygga, fast värre.

| Regel | Rätt lista | Föll tillbaka | **Fel lista** | Precision | Täckning |
| --- | --- | --- | --- | --- | --- |
| ≥ 1 träff, ingen marginal | 96 | 111 | **79** | 55 % | 41 % |
| ≥ 1 träff, vinnaren 1,5× tvåan | 91 | 140 | **55** | 62 % | 41 % |
| ≥ 2 träffar, 2× | 47 | 222 | **17** | 73 % | 24 % |
| ≥ 3 träffar, 3× | 19 | 265 | **2** | 91 % | 10 % |

Ingen tröskel fungerar. Med användbar täckning väljs fel lista för 55–79 filer; för
att få ner felen måste den vara så sträng att den gissar på en av tio och annars
faller tillbaka — vilket är detsamma som att inte gissa.

**Skälet är att signalen saknas.** Median egna träffar per fil är 1 i
`NAR-profetrorelsen` och `Kirk-TPUSA`, och **0** i `god-karismatik`,
`meta-admin` och `bibelsyn`. Mellan 28 och 65 % av filerna nämner inte en enda
term ur sin egen mapps lista. Egna listan slår alla andra i bara 47 % av filerna;
38 % blir oavgjort. Gissningen vilar på namn, och **namn är den felklass Whisper
är sämst på** — en term som förvanskats träffar inte.

Inom samma mapp har `medium`-transkripten 1,5–3× högre täthet av egna termer än
`small` (NAR: 2,54 mot 0,86 per 1000 ord). Riktningen är konsekvent, men
medium-urvalen är 4–8 filer per mapp och sannolikt inte slumpmässiga — det var de
filer som valdes ut först. **Antydan, inte fynd.** Värt att veta för modellvalet
nedan, som redan bär förbehållet att medium-mätningen kan ha mätt bortfall.

**Läckage var inget problem:** ordlistorna ändrades senast 2026-08-01, och det
äldsta av de 286 transkripten är från 2026-08-12.

Gissningen kopplades därför **inte** in. Filer i `incoming/` flaggas med hela
listan. Nästa kandidat, om scopningen visar sig sakna i praktiken, är en
LLM-klassificering på råtranskriptet (~$0,01/fil) — den läser innehåll och inte
bara namn. Bygg den inte utan att först se att hela listan faktiskt ger sämre
flaggning på inkorgsfiler; Bolz-fallet är ett enda uppmätt exempel.

**Första utfallet (2026-09-19): sju filer i `incoming/`, flaggade med hela
listan.** Alla sju bär `tema: null` i sidecaren — rättelsen av `temamapp_for()`
höll. Gissningarna hämtade termer ur **åtta olika listor**, även ur mappar memot
knappast hör till: `Skimpar`→Wimber, `Wingard`→Vineyard, `Klod`→Claude,
`Kentham`→Ken Ham, `ischgalopp`→Gish gallop. Med felet kvar (bara basen, 8 termer)
hade ingen av dem stått i listan. Förenligt med att hela listan gör nytta, men
inget bevis — Claude kan känna till namnen ändå.

**Flaggtätheten fördubblades:** median **4,05 %** (2,05–8,33 %) mot **1,64 %** på
67 sorterade medium-filer. Tre tänkbara orsaker, som den här datan inte kan skilja
åt: small-modellen (fler verkliga fel), hela listan (mer brus), och innehållet
(namntunga NAR-memon). De fem sorterade small-filerna som flaggats med scopad
lista ligger på median 1,82 % — antyder att modellen ensam inte fördubblar, men
n = 5 och spridningen är 1,5–4,4 %.

**Måttet som avgör det finns redan:** av 716 granskade detektorflaggor i 47 filer
var **87 % verkliga fel** (80 % replace, 7,5 % delete) och **12 % accept** —
detektorn hade fel. Håller andelen accept sig kring 12 % när inkorgsfilerna
granskas är de extra flaggorna verkliga fel, och texten har fler fel. Stiger den
tydligt är de extra flaggorna brus. Räkna om när filerna granskats.

### a. Transkribering

KBLabs svenska Whisper (`KBLab/kb-whisper-*`) via faster-whisper / CTranslate2,
int8 på CPU.

- **Standardmodell: `kb-whisper-small`** sedan 2026-09-04. `kb-whisper-large`
  körs på begäran för viktiga filer och skriver över utdatan. Underlaget står
  nedan — det vänder på det förväntade, och därför är det värt att läsa noga.
- **Arkivet är blandat: 82 transkript med `medium`, 201 med `small`.** Ingenting
  behöver göras åt det: JSON:en bär `model`, så varje fil är självförklarande, och
  en omtranskribering skulle radera granskningsbesluten (sidecarens `global_index`
  refererar den gamla textens ordpositioner). Blanda alltså med flit.
- **Mätningarna längre ner i det här dokumentet gjordes på `medium`** —
  ordlisteprovens tre tabeller, ord-konfidensens median ~0,69, hastighetsspannet
  i Hårdvara. De är inte omprövade på small och ska inte läsas som om de vore.
- Producerar tre filer per ljudklipp: `.json` (fullt Whisper-utdata med
  ord-nivå-tidsstämplar), `.srt`, `.txt`.
- JSON:en är sanningskällan. Allt nedströms härleds ur den.
- **Specialtoken kan läcka ut som text** — se tokenvakten nedan. `transkribera.py`
  och `batch-transkribera.py` varnar direkt när det sker.
- Metadatataggarna (steg **f**) sätts **efter steg c**, inte direkt efter
  transkriberingen — se Helhetsflödet. Album-taggen väntar dessutom på sorteringen.

**Modellvalet är mätt (2026-09-04), och utfallet vänder på det förväntade.**
Mätningen gjordes i ett annat, liknande KB-Whisper-projekt: alla tre modellerna
på samma 3-minutersutdrag, med `large` som facit.

| Modell | Ord | Överensstämmelse med large | Kvot vägg/ljud |
| --- | --- | --- | --- |
| **small** | 398 | **93,6 %** | ~0,49× |
| medium | 355 | 80,1 % | ~0,85× |
| large | 414 | (facit) | ~1,67× |

Small är alltså både snabbare **och** närmare large än vad medium är. Det är inte
vad modellstorlek normalt ger, och därför bytte projektet standardmodell.

**Applicerat på det här arkivet.** Uppmätt med `ffprobe` 2026-09-04: **277
ljudfiler utan transkript, 60,5 timmar** (82 filer / 18,8 h är redan gjorda).
Ursprungsprognosen räknade på 24,6 h — det här arkivet är alltså 2,5 gånger
större, och skillnaden mellan modellerna växer i samma takt:

| Modell | CPU-tid för de 60,5 h som återstår |
| --- | --- |
| small | ~30 h |
| medium | ~52 h |
| large | ~101 h |

Valet av small mot medium är alltså värt **drygt 20 timmars CPU** på den här
maskinen — och mot large drygt 70.

**Två förbehåll, som hör till protokollet.**

*Medium tappade ord.* 355 ord mot larges 414 är 14 % färre. Redan det sätter ett
tak kring 86 % på överensstämmelsen, och 80,1 % ligger nära taket — siffran mäter
alltså sannolikt ett **bortfall** hos medium mer än sämre ordträffsäkerhet. Det
gör inte medium bättre: bortfall är den farligaste felklassen i det här projektet,
eftersom flytande text döljer det (jfr issue #1 och negationsvakten, som finns just
för att en tappad negation vänder en sats utan att synas). Men slutsatsen "small
är 13 procentenheter noggrannare än medium" är inte det mätningen visar.

*Ett utdrag, en fil.* n = 1, tre minuter. Samma försiktighet gäller som för
propageringströskeln: överanpassning mot en enda fil är en verklig risk. Hade
utdraget råkat vara det där medium tappade en bit, är rangordningen mellan small
och medium svagare än tabellen antyder. Hastighetskolumnen är däremot robust —
den följer modellstorleken och stämmer med projektets egna mätningar.

Sammantaget: **beslutet står på hastigheten, som är säker, och stöds av
kvalitetssiffran, som är suggestiv.** Ingenting i mätningen talar för medium.

**OBS — mätningen ovan underkändes i sitt eget projekt (2026-09-06, upptäckt här
2026-09-16).** Hestra-projektet (`mediadev/predikningar-hestra-2017-2020/`) mätte
om på en **hel** predikan, 25,4 min, ~3 200 ord, med large som facit:

| Modell | Ord | Bortfall | Tillägg | Substitutioner | Fart |
| --- | ---: | ---: | ---: | ---: | ---: |
| small | 3 092 | 184 | 64 | 83 | 2,09× |
| medium | 2 860 | 399 | 47 | 128 | — |
| large | 3 216 | — | — | — | 0,79× |

Medium var sämst på båda axlarna, vilket stöder att vi lämnade medium. Men **small
tappar också** — 184 bortfall i 25 minuter — och large hittar inte på: tio av
larges bortfallsluckor kontrollyssnades, och alla tio fanns i ljudet. Utdraget om
398 ord hade råkat innehålla mediums bästa ställen. Projektet bytte till **large**,
med lärdomen *mät på hela inspelningar, inte utdrag* — samma förbehåll som stod
här, fast nu belagt.

Tre skäl att inte föra över siffrorna rakt av: predikningar i kyrksal är annat
ljud än mobilmemon med närmikrofon (se loudness-mätningen under Helhetsflödet);
hestra transkriberar **normaliserat** ljud; och hestra kör **`vad_filter=True`**,
vilket vi inte gör. Bortfall är ändå den farligaste felklassen i det här
projektet, eftersom ingen granskning kan höra det som aldrig transkriberades.

**Läget 2026-09-16:** 201 transkript (43,7 h) är gjorda med small, och **ingen av
dem är granskad, applicerad eller har `.md`.** Ett modellbyte kostar alltså bara
CPU för dem — än så länge. Varje granskad small-fil gör bytet dyrare, eftersom en
omtranskribering raderar granskningsbesluten. De 82 medium-filerna (18,8 h) bär
däremot 59 granskningar och 55 `.md`.

**Beslut 2026-09-17: frågan läggs åt sidan. `small` förblir standardmodell, och
arbetet går vidare med de transkript som finns.** Underlaget ovan är inte
motbevisat, bara inte prövat på memon — den mätning som skulle avgöra saken (large
och small på fyra hela memon, ~2 h CPU) är inte gjord. Öppna inte frågan igen utan
att Lars tar upp den. Värt att veta för den som ändå gör det: kostnaden för ett
byte stiger med varje small-fil som granskas, eftersom en omtranskribering raderar
besluten.

**Tokenvakt (`tokenvakt.py` + `korrigeringar.specialtoken_traffar`).** Modellens
styrtoken kan hamna i texten som om den vore tal. Uppmätt 2026-09-21: **107
träffar i 27 av 293 transkript**, i båda modellerna, och de följer med ut i 32
`.txt` och 26 `.srt`. Hittat av en slump när ett granskat memo slutade på
`<|nospeech|>`.

**Den går inte att hitta med en sökning i orden.** Whisper delar tokenen över
flera ordtokens — `<`, `|nospeech`, `|`, `>` — så varje ord för sig ser oskyldigt
ut, och ett mönster som `<\|[^|]*\|>` mot enskilda ord ger **noll träffar** i hela
arkivet. Därför sätter vakten ihop orden per segment, söker mönstret i den
sammanslagna strängen och mappar tillbaka till de ordindex träffen täcker.

Två saker gör en rensning svårare än den ser ut:

- **13 av 107 träffar sitter mitt i tal**, ihopklistrade med riktiga ord
  (`<|nospeech|>ologi,`, `h<|nospeech|>`). En mekanisk strykning tar text med sig.
  Resten utgör hela sitt segment och går att ta bort rakt av.
- **7 filer bär granskningsbeslut, apply eller `.md`.** Tokenorden ingår i
  `global_index`, så en rensning förskjuter varje index efter träffen och
  förstör besluten. De kräver att sidecarens index skrivs om i samma svep.

Vakten **rensar därför inte** — den rapporterar, och avslutar med kod 1 när något
hittas. Rensningen är ett eget steg som inte är byggt.

Skadan är mindre än den ser ut: **noll flaggor** har slösats på tokenen, och
**ingen `.md`** bär den — steg c städar bort den. Kvar är skräp i sanningskällan
och i `.txt`/`.srt`.

`andel` i rapporten (tokenord av alla ord) **duger inte ensam som mått på en
misslyckad transkription**: en kort fil får hög andel av en enda träff
(`zego-bill-j-son-i-vita-huset`, 12 %, är fullt normal). Den värsta filen —
`zego-pingstkarismatiska-…` med 92 % — var däremot redan märkt "ny transkription
behövs" för hand, så vakten hade kunnat säga det automatiskt.

**Transkriptionsvakt (`transkriptionsvakt.py` + `korrigeringar.transkriptionsmatt`).**
Vakten bedömer **hela filen**, aldrig enskilda ord — det är detektorns jobb.
Frågan här är den motsatta: är det här över huvud taget en transkription av det
som sägs i ljudet?

Whisper kan fastna i en upprepningsloop: *"Jag tackar för mig. Jag tackar för mig
själv. Jag tackar för mig."* i elva minuter. Ingenting nedströms fångar det —
texten är välformad svenska, så granskningen ser inget konstigt, och detektorn
letar efter felhörda ord och inte efter att filen saknar innehåll.

**Uppmätt 2026-09-21: 9 av 293 transkript är trasiga, 8 av dem omärkta.** Den
enda som redan var märkt hittades för hand. Fallen hittades av en slump, när
andelen specialtoken prövades som mått på en misslyckad transkription.

Två mått, och de mäter olika saker:

| Mått | Loopfilerna | Riktiga filer | Tröskel |
| --- | --- | --- | --- |
| andel unika segment | 0,11–0,50 | ≥ 0,92 (p5 = 0,98) | **0,80** |
| ord per minut | 1,6–18,6 | nästa riktiga 39 (p5 = 77, median 96) | **30** |

Båda trösklarna ligger i **breda tomrum** i mätningen, inte nära datan. Filer
under en minut bedöms inte alls — där blir måtten brus, och en tvåsekunders
minnesanteckning på två ord är inte trasig.

**Whispers egna mått dög inte.** `compression_ratio` räknas per segment och ser
därför inte en loop som går *över* segmentgränser: högsta värdet i hela arkivet
är 2,23, under Whispers egen larmgräns 2,4. `no_speech_prob` är 0,00 i samtliga
filer. Pröva dem inte igen.

**Loopen är ett säkert omdöme; lågt ordtempo utan loop är det inte.**
`zego-olika-slags-fel` ligger på 39 ord/minut med sammanhängande innehåll — en
människa som tänker mellan meningarna. Därför flaggar vakten, men rättar och
märker aldrig: det kräver en lyssning. Duger inspelningen inte märks den i GUI:t,
och då hoppar `batch-flagga.py` över den i stället för att betala för att flagga
nonsens.

`transkribera.py` och `batch-transkribera.py` varnar direkt när en ny
transkription faller under någon tröskel.

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
~0,69) — mätt på medium och **inte** omprövat på small, som är standard sedan
2026-09-04. Slutsatsen bör hålla ändå, eftersom den vilar på *vilka* ord som får
hög konfidens och inte på nivån; men siffran är medium-siffran.
De flesta felen i Lars stora felklass — egennamn och engelska
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

**Propagering av fattade rättelser (`propagera-namn.py`, issue #7).** Detektorn
bedömer varje ord isolerat, inte dokumentet: samma namn förvanskas olika många
gånger och bara någon variant flaggas (`Boltz,` gick igenom medan `Bolts`
fångades). Men när en människa väl rättat `Vertobius` → `Posobiec` är resten
härledbar utan modell. Skriptet vänder riktningen och letar **orättade
förekomster av redan rättade fel**. Deterministiskt, inga API-anrop, och det
applicerar aldrig — allt blir `pending`-flaggor med `note: "propagering"`, vilket
också är idempotensnyckeln.

**Matchningen mättes, den gissades inte.** `jellyfish` installerades tillfälligt
för att pröva fonetik på riktigt. Facit: åtta obeslutade varianter i
`zego-kirk-owens-posobiec` (655 ord, 29 beslut), med totalvolymen över 25 filer som
brusmått.

| Metod | Tröskel | Mål av 8 | Förslag i filen | Över alla filer |
| --- | --- | --- | --- | --- |
| **normaliserat Levenshtein** | **0,62** | **6** | **8** | **22** |
| Jaro-Winkler | 0,80 | 6 | 9 | 39 |
| Metaphone | 0,60 | 6 | 17 | 133 |
| NYSIIS | 0,70 | 6 | 13 | 51 |
| Soundex | 0,60 | 7 | 19 | 212 |
| Match Rating | 0,60 | 4 | 12 | 93 |

Levenshtein vann på brus vid samma träffbild. Jaro-Winklers prefixbonus är fel
sorts likhet här — den föreslog `Charlie`←`chri` och `Krig`←`kristi`. De rent
fonetiska nycklarna kastar för mycket: `Soundex` fick 212 förslag för 7 träffar.
`jellyfish` avinstallerades igen; koden använder bara stdlib.

**Två fynd vägde tyngre än metodvalet.** Ankarurvalet: med hela rättelseloggen
som ankare gav vardagsorden (`vi`→`hon`, `men`→`med`) **140 förslag i en fil om
655 ord**. Begränsat till namnlika beslut föll det till 11. Och uteslutningen av
korrekta former måste ske på **grundform**, inte exakt sträng — annars föreslås
`Trumps` för att bara `Trump` står som rättelse (59 → 34 förslag).

Utfallet i testfilen: 6 av 8 mål, plus `Pont`→`Point` som bonus, en falsk
(`University`). Missarna är `Colbieks` (0,50) och `Sovjet` (0,43) — för långt från
sina grundformer, som väntat. **Tröskeln trimmades inte tills alla åtta träffade;
det vore överanpassning mot en fil.** Över hela materialet: 22 förslag på 57
filer, ~0,4 per fil, ungefär hälften riktiga fynd.

En absolut gräns på redigeringsavståndet (≤ 2) prövades och förkastades: den kostade
två riktiga träffar och tog bort nästan inget brus.

Issue #7:s andra hälft — en vakt som larmar när samma namn stavas olika **utan**
att något beslut finns — är inte byggd. Den kräver klustring av hela ordförrådet.

**Filstatus "ny transkription behövs".** `zego-josh-hawley-jonathan-edwards`
talades in på **engelska**. KB-Whisper är tränad för svenska och **översatte** i
stället för att transkribera — resultatet är flytande svenska som inte är vad som
sägs i ljudet. Ingenting fångade det automatiskt: ord-konfidensen låg på 0,686 mot
normala 0,70–0,76, och `config.toml` sätter `language = "sv"`, så JSON:ens
`language_probability: 1` betyder ingenting. Det krävs ett mänskligt märke.

Knappen finns i granskningsvyn; märkningen bor i **`granska/status.json`**, som är
versionerad — `granska/state/` är gitignorerad och `/data` monteras read-only med
flit. Märkta filer syns i väljaren och hoppas över av `batch-flagga.py`.

**Batch för apply:** `batch-applicera.py` skriver in besluten i alla filer som är
färdiggranskade och ännu inte applicerade. `applicera-corrections.py` tar en fil,
den GUI:t pekar ut — efter en batchtranskribering ligger tiotals granskade filer
och väntar, och att välja dem en och en är rent klickarbete. Delad kod: apply har
brutits ut till `applicera_en(json_path, sidecar_path=, torrkorning=)`, som båda
vägarna anropar. `--dry-run` gör hela beräkningen och låter varje vakt smälla men
skriver ingenting, så siffrorna är riktiga och inte uppskattade.

Fyra vakter, i den ordning de träffar: filer märkta "ny transkription behövs"
avvisas alltid; sidecarens `word_count` måste stämma med källans ordantal; alla
ordindex måste ligga inom källan (den vakten är den **enda** som finns för
sidecars som `granska/index.php` skapat tomma — de bär inget `word_count`); och
en fil som är applicerad men saknar `-bak.json` avvisas, eftersom apply annars
skulle läsa den redan rättade texten som bas och förskjuta varje index efter
första raderingen.

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

**Granskningen sker alltid mot rundans orörda bas**, aldrig mot den senaste
`.json`:en: runda 2 mot `base_json` (`-bak2.json`), runda 1 mot `-bak.json` när
den finns, annars mot `.json` — som då *är* basen. Skälet är att sidecarens
`global_index` refererar basens ordpositioner, och apply tillämpar besluten mot
samma bas.

Detta var fel för runda 1 fram till 2026-08-13 och gav ett **tyst** fel: efter en
apply som ändrat ordantalet ritades varje flagga efter första raderingen eller
infogningen på fel ord. Texten är ju läsbar, så ingenting såg konstigt ut. Det
upptäcktes först när `propagera-namn.py` lade nya flaggor i en redan applicerad
fil och de inte gick att hitta — `Kerr` satt på *vad*, `Kerps` på *i*. **20 av
filerna hade formen**; bara den ena hade ogranskade flaggor, så resten märktes
aldrig. `index.php` jämför numera sidecarens `word_count` och högsta flaggindex
mot den visade texten och skriver en varning i huvudet i stället för att tiga.

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

**Batch:** `batch-forbattra.py` kör steg c över alla applicerade filer som saknar
`.md`, och **negationsvakten direkt efter varje fil** — den är gratis, och att köra
den för hand tio gånger vore samma klickarbete batchen finns för att slippa.
Uppskattar kostnaden först; `--dry-run` gör inget annat.

**Kostnaden är uppmätt (2026-08-13, `claude-opus-4-8`): ~$0,010 per ljudminut.**
Tio filer, 87 ljudminuter, 23 anrop: $0,90 utfall mot $0,85 uppskattat. Underlaget
till uppskattningen, som konstanter i batchen: **2,13 tecken per token** på svensk
prompttext (mätt med `count_tokens` över 128k tecken — den engelska tumregeln 3,3
underskattade med en tredjedel), **766 tokens** schema-overhead per anrop, och
utdata **0,355×** indata inklusive adaptiv thinking. Hela arkivet blir ~$45.

En bit som misslyckas ger inte längre en halv `.md`. Ett tapp på 90 segment mitt
i filen syns inte i utdatan, och negationsvakten skulle larma på hela luckan utan
att förklara varför — därför skrivs ingen `.md` alls när en bit fallerar.

**Steg c rättar transkriptionsfel som steg b missade.** Uppmätt i
`zego-kirk-owens-posobiec`: *koncentrationsjuristerna* → konspirationsteoretikerna,
*Sobiek/Colbieks/Sovjet* → Posobiec, *Trilling Pont New Day* → Turning Point.
Utfallet är rätt, men det betyder att `.md` och `.json` skiljer sig i **innehåll**
och inte bara i putsning — JSON:en, sanningskällan, bär kvar felen. Det är ett
argument för att steg d och e indexerar `.md`:n, och en påminnelse om att
`-borttaget.txt` inte är det enda som behöver ögon.

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
över tidigare värden i de fält som fått nytt underlag. Ordningen följer
helhetsflödet: taggarna sätts efter steg c, och album-taggen först när filen
sorterats — en fil i `incoming/` har inget tema att sätta.

| När | Underlag | Vad som sätts |
| --- | --- | --- |
| efter **c** | JSON + `.md` | titel ur rubriken, artist, datum, längd, sammanfattning i kommentaren |
| efter **d** | kodboken | QDA-koder som genre/grupp |
| efter **sortering** | temamappen | album |

**Fälten:**

- **Titel** — `.md`-rubriken när den finns, annars ljudfilens namn. Preliminära
  titlar ska gå att känna igen som preliminära.
- **Artist** — `Lars Gunther`.
- **Album** — temamappens namn (`NAR-profetrorelsen`, `trump-politik`, ...). Gör
  att mediaspelare grupperar memona per tema utan extra arbete. Memon i
  `incoming/` får ingen album-tagg förrän de sorterats.
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
transkribering direkt vid uppladdning är avfärdat — en körning äter datorn i
minuter (uppmätt på medium; small är snabbare men inte snabb).

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
eller långsammare — en halvtimmes memo tar en halvtimme eller mer. Därför en
mindre modell som standard: **`small` sedan 2026-09-04**, dessförinnan medium.

**Hastigheten varierar oförklarat och kan inte planeras på (issue #8).** Uppmätt
på **medium**/CPU/int8, samma maskin och samma inställningar. Tabellen är alltså
inte längre standardmodellens siffror — small bör ligga lägre, men det är
omätt, och spannets *storlek* är poängen och den lär bestå:

| Kvot vägg/ljud | Omständighet |
| --- | --- |
| 0,56× | den ursprungliga referensmätningen |
| 0,76×–0,82× | 22-minutersfil, dagtid |
| 0,99× | 50-sekundersfil (fast overhead väger tungt på korta filer) |
| 1,25×–1,31× | 8- och 15-minutersfiler |
| 1,52×–1,99× | med ordlisteprompt (hotwords) |

Spannet är **3,5×** mellan bästa och sämsta. **Räkna inte på 0,56× när arkivet
planeras** — skillnaden mellan 0,8× och 1,5× är ungefär hundra timmar CPU på de
277 återstående filerna (60,5 h ljud). Hypotes värd att pröva: `cpu_threads = 0`
(auto) låter CTranslate2 välja trådantal utifrån maskinens tillfälliga last. Ett
explicit värde skulle göra mätningarna jämförbara.

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

**Namnvakt (`namnvakt.py` + `korrigeringar.vakta_ljud/vakta_transkript`).** Varje
skript kontrollerar filnamnet innan det arbetar. Vakten är inte en spärr mot
konventionsbrott — den är en spärr mot **kollisioner**.

Skillnaden är mätt, inte antagen. **113 av 363 ljudfiler bryter mot konventionen**
(112 versaler, 1 icke-ASCII, 1 blanksteg). Alla 113 är ofarliga: `transkribera.py`
normaliserar stammen på väg ut, så `zego-Trump-akrist-1.m4a` ger
`zego-trump-akrist-1.json` och allt nedströms är redan rent. En spärr mot versaler
hade stoppat en tredjedel av arkivet utan att avvärja ett enda fel — och den hade
motsagt regeln ovan om att ljudet inte döps om.

Det farliga är i stället när normaliseringen får två filer att falla ihop:

| Fel | Följd |
| --- | --- |
| två ljudfiler i samma mapp → samma stam | båda skriver samma `.json`; den som körs sist raderar den förstas transkript |
| samma stam i två temamappar | `granska/state/` är platt — sidecars skriver över varandra, och besluten landar i fel fil |
| transkript vars egen stam inte är normaliserad | `json_path_for()` kan aldrig härleda fram till det; härledda namn blandas med grannens |

**Sex filer spärrades först, och en av dem dolde ett verkligt tapp.**
`zego-torpseminarium.aac` (47:18) och `zego-torpseminarium.m4a` (54:20) är **två
olika inspelningar** — inte samma ljud i två format. Bara `.m4a`:ns 54 minuter är
transkriberade; `.aac`:ns 47 minuter har aldrig kunnat komma in i pipelinen,
eftersom utdatasökvägen redan var upptagen. Ingenting sa ifrån.
`zego-benefit-of-the-doubt-...` (`.m4a`/`.mp3`) är däremot samma inspelning i två
format — 548,2794 s i båda — alltså ofarlig dubblett, men samma spärr.
`zego-predikan-2` finns i två temamappar och fångas **innan** någon av dem
transkriberats; hade båda körts hade den ena granskningen skrivit i den andras
sidecar.

**Den sjunde (2026-09-16) kom med `incoming/`.**
`zego-liberalteologi-nagot-nytt-joel-halldorf.m4a` ligger både i `incoming/` och i
`bibelsyn-lib-fund-equmeniakyrkan/` — **byte-identiska** (samma längd, storlek och
hash), och ingen av dem har transkript. Vakten jämför stammar över hela arkivet
oavsett mapp, så en fil som laddas upp i inkorgen men redan finns sorterad fångas
innan den kostar CPU. Det är precis den dubbelregistrering ett flöde med en
inkorg bjuder in till. **Löst 2026-09-19:** Lars raderade kopian i `incoming/`;
den i `bibelsyn-lib-fund-equmeniakyrkan/` står kvar. Namnvakten är tillbaka på sex.

Vakten skiljer därför på tre utfall: **spärrat** (avbryter, exit-kod 2),
**varning** (körs vidare — t.ex. ett avvikande ljudfilnamn, eller ett transkript
vars ljudfil inte längre finns i mappen) och rent. Batcharna hoppar över spärrade
filer och kör de övriga; `batch-flagga.py` filtrerar dem ur kön **före**
kostnadsuppskattningen, så en spärrad fil aldrig hinner kosta ett API-anrop.

`namnvakt.py` är översikten över hela arkivet — vad som är spärrat, vad som bara
avviker, och vad som ska döpas om. `--alla` listar de avvikande namnen ett och ett.

**När ljudet döps om (`synka-namn.py`).** Ljudet döps om för hand — det är Lars
filer och hans taxonomi. Men de härledda filerna bär stammen både i sitt namn och
i sitt innehåll, och de följer inte med av sig själv. Namnvakten ser resultatet
("ingen ljudfil med den stammen i mappen") men kan bara larma; `synka-namn.py`
åtgärdar.

Efter arkivomdöpningen 2026-08-28 var läget: **sex grupper om 26 härledda filer**
var föräldralösa, och **89 innehållsfält** pekade på filer som inte fanns.

Två fel, båda tysta:

- **Föräldralösa grupper.** Hela familjen döps om — `.json`, `.srt`, `.txt`,
  `.md`, `-bak*.json`, `-corrections*`, `-borttaget.txt` — plus arbetskopian i
  `granska/state/`. `planering-med-ai` behövde dessutom **flyttas**: ljudet hade
  fått en temamapp.
- **Pekarfält.** `audio_file`, `transcript_json`, `base_json`, och `.md`:ns
  frontmatter. Skiftlägesexakt, för det är där felet gömmer sig: Windows låter
  `zego-Kirk-debatt.m4a` matcha `zego-kirk-debatt.m4a`, men GUI:t kör i Docker på
  Linux där `valj.php:65` läser fältet rakt av och ljudknappen är död. 19
  transkript hade det felet utan att någon märkt det.

**Parningen bevisas mot ljudlängden, den gissas inte på namnet.** `ffprobe` mot
JSON:ens `duration`, tolerans ±1 s — fyra av sex parades så, på hundradelen. De
två äldre transkripten (`word_segments`/`text`-schemat) saknar `duration` och
verifierades i stället på sista segmentets sluttid, som alltid ligger strax före
ljudslutet; där krävs **också** namnsläktskap, så att två lika långa memon inte
kan förväxlas. Vid flera eller inga kandidater görs ingenting och fallet
rapporteras. `zego-Adam-Abraham` hamnade där: två lika långa kandidater, alltså
Lars beslut.

Tre regler som är lätta att göra fel, och som alla tre kostade en rättning:

- **Suffixet härleds aldrig om, bara stammen byts.** En runda 2-sidecar pekar med
  flit på `<stam>-bak2.json` — rundans orörda bas. Att "rätta" den till
  `<stam>.json` hade riktat granskningen mot den redan applicerade texten.
- **Filer som namnvakten spärrar rörs inte.** `zego-torpseminarium` har två
  ljudfiler; att välja den alfabetiskt första hade satt `.aac` (47 min) som källa
  för en text som kommer ur `.md`:ns `.m4a` (54 min).
- **`titel` i `.md` är maskinsatt** — uppmätt 54 av 55 filer — och följer därför
  stammen. En titel med blanksteg eller versal är en rubrik du skrivit och rörs
  inte.

`--dry-run` är **standard**; skriptet skriver bara med `--kor`. Det avviker från
de andra batcharna med flit: här ligger färdiggranskat material, och 27
omdöpningar är inget man ångrar med en knapp.

**Fällan som hittades på vägen:** `applicera-corrections.py:main()` läste aldrig
`sys.argv`. `--dry-run` gav alltså en **skarp** apply, och en filsökväg på
kommandoraden ignorerades tyst så att fel fil bearbetades. Biblioteket stödde
`torrkorning` hela tiden — det var bara enfilsvägen som inte kopplade in flaggan.
Rättat: flaggan läses, rapporten säger "Skulle skriva:" i stället för "Skrev:",
och ett okänt argument avbryter i stället för att tolkas som inga argument.

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
3. **Sortering — besvarad, och byggd 2026-09-21.** `sortera.py` + `sortering.toml`
   föreslår temamapp; Lars godkänner en fil i taget. Se Sorteringen nedan.
4. **Resten av arkivet.** 80 ljudfiler / 17,8 h återstår (2026-09-16), ned från
   277 / 60,5 h före körningen 2026-09-05–06. Issue #9 (vänteläget) är inte
   åtgärdat i koden.
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
| 7 | Konsistensvakt: samma namn förvanskat olika, bara ett flaggat | halv — `propagera-namn.py` klar, klustringsvakten kvar |
| 8 | Väggklockemätningen räknar in sömn; hastigheten oförutsägbar | mätproblem |
| 9 | Modernt vänteläge stryper nattbatch | **blockerar arkivet** |
| 10 | Ordlistan per temamapp | ✅ genomförd i denna omgång |
