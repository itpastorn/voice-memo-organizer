# Steg b — flaggning av felhörningar. Komplett spec för att låna steget

Detta är en självständig beskrivning av `voice-memo-organizer`s steg b: en
LLM-detektor som flaggar troliga felhörningar i en Whisper-transkription, och ett
webb-GUI där en människa fattar besluten. Den är skriven för att klistras in i ett
annat projekt tillsammans med filerna som listas sist.

Allt nedan är taget ur körande kod, inte ur minnet. Siffror som anges som uppmätta
är uppmätta.

---

## 1. Vad steget gör, och varför det ser ut som det gör

Whisper producerar flytande text. Felen är därför **osynliga**: ett förvanskat
egennamn läser som ett ord man inte kände till, och en tappad negation vänder en
sats utan att någonting ser konstigt ut. Steget finns för att göra felen synliga
och beslutbara.

**Ord-konfidens duger inte som detektor.** Det var det första man prövade, och det
misslyckades av en icke-uppenbar orsak: de värsta felen får *hög* konfidens.
Modellen är tryggt fel — `dik` = 0,91 för "geek". Uppmätt median på ord-konfidens
låg dessutom på ~0,69 för hela materialet, så en absolut tröskel är meningslös.
En percentilvariant finns kvar i projektet enbart som billig jämförelse; den är
inte det som används.

**Detektorn är därför en LLM som läser semantiskt.** Sex felklasser: fyra på
ordnivå (nonsensord, förvanskade namn, låneord, rätt ord på fel plats) och två som
kräver att man läser efter logiken (**saknade ord**, framför allt negationer, och
avvikelser i citat).

**Människan beslutar, aldrig modellen.** Detektorn föreslår; varje beslut fattas i
GUI:t. Det är inte försiktighet för sakens skull utan en uppmätt erfarenhet: i ett
angränsande försök, där man lät modellen påverka transkriptionen direkt, blev
`Nordbehandlade` till *"en obehandlad rätt"* — nonsens blev **flytande nonsens**,
alltså svårare för både detektorn och ögat att upptäcka. En modells rättning kan
vara värre än felet den rättar, och behöver därför en människa emellan.

---

## 2. Indatakontraktet

Steget kräver Whisper-JSON **med ord-nivå-tidsstämplar**. Utan `words` per segment
fungerar ingenting nedströms — indexen är hela systemets ryggrad.

```json
{
  "audio_file": "zego-exempel.m4a",
  "duration": 504.58,
  "segments": [
    {
      "start": 0.0, "end": 4.2, "text": " Några funderingar...",
      "words": [
        {"word": " Några", "start": 0.0, "end": 0.4, "probability": 0.71},
        {"word": " funderingar", "start": 0.4, "end": 1.1, "probability": 0.69}
      ]
    }
  ]
}
```

Med faster-whisper: `model.transcribe(..., word_timestamps=True)`.

Observera att Whisper-tokens **bär sitt eget inledande blanksteg** (`" Några"`).
All jämförelse mot ordet självt måste `.strip()` först. Det är en återkommande
källa till buggar.

### Den enda invarianten som spelar roll

> **Ett `global_index` är ordets position i den utplattade ordlistan för en
> bestämd version av transkriptet. Varje konsument måste platta ut ord på exakt
> samma sätt, och läsa exakt samma version.**

Utplattningen, i Python:

```python
def flatten_words(segments: list[dict]) -> list[dict]:
    """Platta ut alla ord till en lista i ordning. Varje ord får en säker
    starttid (faller tillbaka på segmentets start om ordets saknas)."""
    words = []
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
```

Och samma sak i PHP, i GUI:t — de **måste** ge identiskt resultat:

```php
$words = [];
foreach ($data['segments'] ?? [] as $seg) {
    $segStart = $seg['start'] ?? null;
    foreach ($seg['words'] ?? [] as $w) {
        $words[] = ['word' => $w['word'] ?? '', 'start' => $w['start'] ?? $segStart];
    }
}
```

**Detta gick fel i originalprojektet och kostade tyst datafördärv.** Efter att
besluten skrivits in i JSON:en (som ändrar ordantalet, eftersom raderingar och
infogningar ingår) visade GUI:t den *nya* texten men ritade flaggorna på de
*gamla* indexen. Varje flagga efter första raderingen hamnade på fel ord. Texten
var läsbar, så ingenting såg fel ut — 20 filer hade formen innan det upptäcktes,
och bara för att ett annat verktyg lade nya flaggor i en redan behandlad fil och
de inte gick att hitta.

Slutsatsen, som måste byggas in från början: **granskningen sker alltid mot
rundans orörda bas.** Se avsnitt 6.3.

---

## 3. Detektorn

### 3.1 Systemprompt

Ordagrant. Den är resultatet av flera omgångar och varje stycke sitter där av
skäl; anpassa domänmeningen i första stycket, men behåll strukturen.

```text
Du granskar en automatisk transkription (KBLabs svenska Whisper) av Lars Gunthers
röstmemon — teologiska resonemang på svenska, ofta med engelska namn och begrepp.

Din uppgift: flagga ord som troligen är FELHÖRDA av transkriberaren. Leta efter
- nonsensord och ord som inte finns (t.ex. "gik", "dik"),
- förvanskade egennamn (t.ex. "Dunper" för "Gunther"),
- engelska namn/låneord renderade som svenska (t.ex. "geek" felstavat),
- riktiga svenska ord som är fel i sammanhanget (t.ex. "punkt" där det borde vara "uns"),
- meningar som verkar SAKNA ord — särskilt negationer (inte, aldrig, ingen) som
  vänder betydelsen, och namn i attributioner ("säger X", "enligt X" där namnet
  fallit bort). Resultatet är flytande svenska, så läs efter logiken. Flagga ordet
  närmast luckan, lämna gissad rättelse TOM och ange i skälet att ett ord troligen
  saknas, och vilket,
- avvikelser i bibelcitat: när texten citerar eller parafraserar Bibeln, jämför mot
  Bibel 2000:s ordalydelse och flagga ord som ligger nära citatets form men är fel
  (t.ex. "lydig inför döden" där Fil 2:8 har "ända till"). Flagga INTE talarens
  egna medvetna parafraser.

Flagga INTE ord som är korrekta, även om de är ovanliga. Ordlistan du får är facit
över namn och begrepp som förekommer — dessa är rättstavade och ska inte flaggas,
men de hjälper dig förstå vad en förvanskning borde vara.

Var hellre för snål än för generös: en enda missad "gik" är värre än att du låter
bli att flagga ett tveksamt men troligen korrekt ord. Returnera bara verkliga
misstankar.
```

Fyra saker i den prompten är värda att inte tappa bort vid anpassning:

1. **Bortfallsklassen ("meningar som verkar SAKNA ord")** — instruktionen att
   lämna gissningen **tom** och lägga förslaget i skälet. GUI:ts förslagsknapp
   *ersätter* ett ord; en ifylld gissning för ett saknat ord skulle alltså radera
   fel ord. Bortfall måste infogas för hand.
2. **Snålhetsinstruktionen.** Utan den flaggar modellen allt som är ovanligt, och
   granskningen drunknar.
3. **Ordlistan som facit, inte som förbud.** Den ska hjälpa modellen förstå vad en
   förvanskning *borde* vara, inte bara skydda korrekta ord.
4. **Domänraden.** Byt ut den mot ert material — den styr vad modellen tycker är
   rimligt.

### 3.2 Strukturerad utdata

```python
from pydantic import BaseModel, Field

class Flaggning(BaseModel):
    index: int = Field(description="Ordets [index] i listan nedan.")
    hort: str = Field(description="Ordet så som det står transkriberat (för kontroll).")
    gissad_rattelse: str = Field(description="Vad det troligen borde vara. Tom sträng om osäker.")
    skal: str = Field(description="Kort skäl: nonsensord, förvanskat namn, engelskt låneord, fel ord i sammanhanget, ...")

class Resultat(BaseModel):
    flaggningar: list[Flaggning]
```

`hort` används aldrig till något funktionellt — den finns som kontroll att
modellen och koden talar om samma ord. Behåll den; den avslöjar indexförskjutning
direkt.

### 3.3 Anropet

```python
response = client.messages.parse(
    model="claude-opus-4-8",
    max_tokens=8000,
    thinking={"type": "adaptive"},
    system=SYSTEM,
    messages=[{"role": "user", "content": build_prompt(...)}],
    output_format=Resultat,
)
if response.stop_reason == "refusal" or response.parsed_output is None:
    return [], response.usage
```

### 3.4 Bitindelning och kontextfönster

Filen delas i bitar om **300 ord** (`llm_chunk_ord`). Varje bit skickas med
**20 ord extra på var sida** som ren kontext (`PAD = 20`), men modellen får bara
flagga inom sitt eget intervall:

```python
PAD = 20

def build_prompt(words, ctx_start, ctx_end, flag_start, flag_end, ordlista):
    lines = [f"[{i}] {words[i]['word'].strip()}" for i in range(ctx_start, ctx_end)]
    ordlista_text = "\n".join(f"- {t}" for t in ordlista) if ordlista else "(tom)"
    return (
        "ORDLISTA (kända, rättstavade namn och begrepp):\n"
        f"{ordlista_text}\n\n"
        "TRANSKRIPT (ett ord per rad, [globalt index] framför varje). Flagga ENDAST "
        f"ord med index {flag_start}–{flag_end - 1}; raderna utanför det intervallet "
        "är bara kontext:\n"
        f"{chr(10).join(lines)}\n\n"
        "Returnera de ord i intervallet du misstänker är felhörda, med [index], vad "
        "du hörde, din gissade rättelse (tom om osäker) och ett kort skäl."
    )

def flag_chunk(client, words, flag_start, flag_end, ordlista, model):
    ctx_start = max(0, flag_start - PAD)
    ctx_end = min(len(words), flag_end + PAD)
    ...
    inom = [f for f in response.parsed_output.flaggningar
            if flag_start <= f.index < flag_end]
    return inom, response.usage
```

Tre designval här som är lätta att göra fel:

- **Globala index i prompten.** Varje rad bär `[i]` som är ordets index i hela
  filen, inte i biten. Ingen index-omräkning behövs någonstans, och en flagga från
  bit 7 är direkt jämförbar med en från bit 1.
- **Filtret efter svaret är inte valfritt.** Modellen flaggar gärna i
  kontextzonen. Utan `flag_start <= f.index < flag_end` får man dubbletter i
  överlappet.
- **Ett ord per rad.** Fri löptext ger modellen möjlighet att räkna fel på
  positioner; en explicit indexerad lista gör det omöjligt.

### 3.5 Ordlistan

En lista över kända, rättstavade namn och begrepp, skickad som facit. Uppdelad i
en gemensam bas plus en fil per ämnesmapp, namngiven exakt som mappen:

```text
ordlista/gemensam.txt
ordlista/<mappnamn>.txt
```

Format: en term per rad; `#` inleder kommentar och en `#`-kommentar sist på raden
skalas bort — så `Shawn Bolz  # (Bolts, Boltz)` bidrar med `Shawn Bolz` men
dokumenterar observerade förvanskningar för människan.

**Scopa listan till materialets ämnesmapp.** Det är uppmätt, inte antaget: med en
global lista fick `Nadia Bolz-Weber` detektorn att *missa* `Shawn Bolz` — den såg
"Bolz" som redan förklarat. Är ämnet okänt (osorterad fil) skickas hela listan;
steg b har ingen längdgräns så det kostar bara tokens.

En term får finnas i flera mappfiler. Dubbletter är billigare än fel placering.

### 3.6 Kostnad

Uppmätt på `claude-opus-4-8`: **~15 tokens in och ~3 ut per ord** (28k/6k för
1919 ord; 34k/7k för 2207). Med $5/$25 per Mtok ger det **~$0,25–0,30 per fil** på
material av den här storleken. Uppskattningen används för `--dry-run`:

```python
kostnad = (ord_tot * 15 / 1e6) * PRIS_IN + (ord_tot * 3 / 1e6) * PRIS_UT
```

---

## 4. Sidecar-formatet — det egentliga gränssnittet

Detektorns utdata och GUI:ts in- och utdata är **en JSON-fil bredvid
transkriptet**, `<stam>-corrections.json`. Källtranskriptet rörs aldrig av
granskningen; alla beslut bor i sidecaren tills de appliceras.

```json
{
  "audio_file": "zego-exempel.m4a",
  "transcript_json": "zego-exempel.json",
  "source": "batch-flagga.py",
  "model": "claude-opus-4-8",
  "tema": "NAR-profetrorelsen",
  "word_count": 655,

  "flags": [
    {
      "anchor": 37.86,
      "global_index": 142,
      "heard": "Dunper",
      "verify_ok": true,
      "ai_guess": "Gunther",
      "ai_reason": "förvanskat namn, ska vara Lars Gunther",
      "decision": "pending",
      "replacement": "",
      "note": "",
      "line": 12,
      "reviewed": true
    }
  ],
  "phrase_edits": [
    {
      "kind": "fras",
      "span_start": 200, "span_end": 203,
      "anchor": 91.4,
      "original": "Gett på Sobjects",
      "replacement": "Jack Posobiecs",
      "reviewed": true
    }
  ],
  "insertions": [
    {"kind": "infogning", "after_index": 310, "word": "inte", "anchor": 140.2, "reviewed": true}
  ],
  "review": []
}
```

### Fälten, ett i taget

| Fält | Betydelse |
| --- | --- |
| `word_count` | Antal ord i **basen** sidecaren gjordes mot. Apply vägrar när det inte stämmer — den enda vakten mot att besluten skrivs på fel text. |
| `transcript_json` | Filnamnet på transkriptet (bara namnet, inte sökväg). |
| `audio_file` | Ljudfilens **faktiska** namn, skiftlägesexakt. GUI:t läser fältet rakt av; på ett skiftlägeskänsligt filsystem tystnar ljudknappen annars. |
| `base_json` | Bara i runda 2+: filnamnet på rundans orörda bas. Se 6.3. |

**`flags[]`** — ett beslut per ord:

| Fält | Betydelse |
| --- | --- |
| `global_index` | Ordets position i den utplattade listan. `null` om det inte gick att lösa. |
| `anchor` | Ordets starttid i sekunder. Används för ljuduppspelning och som mänskligt läsbart fäste. |
| `heard` | Ordet som det står i transkriptet, `strip()`:at. |
| `verify_ok` | `true` när `words[global_index].strip() == heard`. **GUI:t läser fältet oskyddat och ritar varningsram när det saknas** — sätt det alltid. |
| `ai_guess` | Detektorns förslag, ETT ord. Tom sträng när osäker eller vid bortfall. GUI:t visar förslagsknappen bara när fältet är ifyllt. |
| `ai_reason` | Kort skäl, visas för människan. |
| `decision` | `pending` \| `replace` \| `accept` \| `delete` \| `osaker`. |
| `replacement` | Bara meningsfullt vid `replace`. |
| `note` | Fritext om hur flaggan kom till (`"ny (GUI, typ 3)"`, `"flera ord delar ankare"`, ...). Duger också som idempotensnyckel för verktyg som lägger till flaggor. |
| `reviewed` | Sätts av GUI:t när en människa fattat beslutet. **Ett `replace` utan `reviewed` är modellens förslag, inte ett beslut** — den skillnaden är viktig för allt som bygger vidare på besluten. |
| `line` | Radnummer i den ursprungliga `.txt`:en, eller `null`. Legacy, se 5. |

`decision`-värdena är inte utbytbara: `accept` betyder "hört rätt, texten står
kvar" och `osaker` betyder "jag vet inte" — båda lämnar texten orörd, men bara
`accept` markerar ordet som mänskligt bekräftat.

**`phrase_edits[]`** — ersättning av ett helt spann ord. Behövs när felet inte är
ett ord utan en sekvens (`Gett på Sobjects` → `Jack Posobiecs`), alltså när
ordgränserna själva är fel. `span_start`/`span_end` är inklusiva.

**`insertions[]`** — ord som ska föras in efter `after_index`. Den enda vägen att
rätta bortfall.

**`review[]`** — rader som ett migreringssteg inte kunde tolka. Ett nytt projekt
kan lämna listan tom men bör behålla fältet; GUI:t förutsätter att nycklarna finns.

---

## 5. Hoppa över textformatet

Originalprojektet skriver först en `<stam>-corrections.txt` i ett handredigerat
format, och konverterar den sedan till sidecaren med `migrera-corrections.py`.
**Gör inte om det.** Textformatet finns av historiska skäl — det var det format
en människa redan hade granskat två timmar i, och migreringsskriptet skrevs för
att rädda det arbetet.

Det kostar dessutom robusthet: `.txt`:en bär bara *tidsstämplar* som ankare, så
migreringen måste slå tillbaka från tidsstämpel till ordindex via en ankarkarta,
och hantera att flera ord kan dela tidsstämpel. Går man direkt från detektorn till
sidecaren **har man redan `global_index`** och hela det problemet försvinner.

En ny implementation bygger alltså `flags[]` direkt ur `Flaggning`:

```python
flags.append({
    "anchor": words[f.index]["start"],
    "global_index": f.index,
    "heard": words[f.index]["word"].strip(),
    "verify_ok": words[f.index]["word"].strip() == f.hort.strip(),
    "ai_guess": f.gissad_rattelse.strip(),
    "ai_reason": f.skal,
    "decision": "pending",
    "replacement": "",
    "note": "",
    "line": None,
})
```

`verify_ok` faller ut gratis här, och blir en äkta kontroll att modellen läste
rätt rad.

**Skriv alltid en sidecar, även när noll ord flaggades.** En fil utan sidecar ser
i väljaren ut som om steget aldrig körts. En tom sidecar säger "granskad, inget
hittat" — och människan kan ändå öppna filen och rätta det detektorn missade.

---

## 6. GUI:t

PHP 8.3 via `php -S` i Docker. Ingen databas, inget byggsteg, inga beroenden.

```yaml
services:
  granska:
    image: php:8.3-cli
    working_dir: /app
    command: php -S 0.0.0.0:8080 -t /app
    ports: ["8137:8080"]
    environment: [DATA_DIR=/data]
    volumes:
      - ./:/app
      - "${DATA_ROOT:?saknas}:/data:ro"
```

`DATA_ROOT` sätts i `granska/.env` och genereras ur projektets konfiguration —
inga maskinberoende sökvägar i koden.

### 6.1 Läsläge och arbetskopia

**Datamappen monteras read-only.** Det är avsiktligt: sanningskällan får inte
kunna skadas av ett webb-GUI. Besluten skrivs därför till en **arbetskopia**:

```text
granska/state/<stam>-corrections.json
```

Arbetskopian fröas från sidecaren i datamappen första gången filen öppnas. Finns
ingen sidecar skapas en tom.

**`state/` är platt.** Alla arbetskopior ligger i en katalog oavsett vilken
undermapp materialet kommer från, eftersom apply-steget hittar dem på
`<stam>-corrections.json`. Konsekvensen är hård och måste byggas in:
**filstammarna måste vara unika i hela materialet.** Två filer med samma stam i
olika mappar delar arbetskopia, och den ena granskningens beslut hamnar i den
andras fil. Bygg en vakt för det innan ni får materialet, inte efter.

### 6.2 Skrivendpunkten

`save.php` tar POST med JSON och tre `type`. Den låser filen (`flock(LOCK_EX)`),
läser, muterar, skriver tillbaka.

```text
type: "word"       { global_index, decision, replacement, [heard, anchor, ai_guess] }
                   decision ∈ replace | accept | delete | osaker | pending
                   upsert på global_index

type: "phrase"     { span_start, span_end, anchor, original, replacement }
                   upsert på span_start

type: "insertion"  { after_index, word, [anchor] }
                   append
```

`word` gör **upsert**, inte bara update: klickar man ett ord som inte är flaggat
skapas en ny flagga med `note: "ny (GUI, typ 3)"`. Det är en väsentlig funktion —
detektorn missar saker, och människan ska kunna rätta vad som helst utan att gå
via ett skript.

Svaret bär färska räknare så gränssnittet kan uppdatera utan omladdning.

### 6.3 Basvalet — den viktigaste regeln i GUI:t

Granskningen visar **alltid rundans orörda bas**, aldrig den senast skrivna
texten:

```php
$transcript = $rel;                                  // default: .json ÄR basen
if (str_ends_with($sidecar, '-corrections-2.json')) {
    // runda 2: basen står i sidecarens base_json
    if (!empty($side['base_json']) && is_file(...)) $transcript = $dir . $side['base_json'];
} elseif (is_file($dataDir . '/' . $dir . $stem . '-bak.json')) {
    // runda 1: -bak.json finns => .json är redan applicerad
    $transcript = $dir . $stem . '-bak.json';
}
```

Versionskedjan som gör detta möjligt:

```text
<stam>-bak.json    orört original (rörs aldrig efter att den skapats)
<stam>-bak2.json   ögonblicksbild av runda 1-resultatet = runda 2:s bas
<stam>.json        alltid senaste sanningen (skrivs om av apply)
```

Apply skapar `-bak.json` en gång, och läser sedan **alltid** basen som källa. Det
är också vad som gör apply idempotent: kör om den och resultatet blir detsamma,
eftersom den aldrig bygger vidare på sitt eget utflöde.

Lägg dessutom in en **synlig varning** när sidecarens `word_count` eller högsta
`global_index` inte stämmer med den visade texten. Det felet är annars helt tyst.

### 6.4 Räkningen måste vara delad

Hur många flaggor som "återstår" beräknas på tre ställen — i väljaren, i
granskningsvyn och i batchen. Går de isär säger listan "1 kvar" om en fil där
ingenting återstår. Regeln bor därför i **en** funktion per språk
(`gemensam.php` / motsvarande i Python):

```php
function fras_tackta(array $side): array {
    $tackta = [];
    foreach ($side['phrase_edits'] ?? [] as $p) {
        if (isset($p['span_start'], $p['span_end'])) {
            for ($i = $p['span_start']; $i <= $p['span_end']; $i++) $tackta[$i] = true;
        }
    }
    return $tackta;
}
```

**Ord som täcks av en frasersättning räknas som beslutade, inte som väntande.**
Frasen *är* beslutet, och apply hoppar över ordflaggan för varje index i spannet.
Utan regeln ser färdiggranskade filer ogranskade ut. Fraser utan `span_start`
hoppas över — samma villkor som apply använder.

### 6.5 Tangentbordet

Granskning är repetitivt arbete; gränssnittet är byggt tangentbord-först.

| Tangent | Åtgärd |
| --- | --- |
| `n` / `p` | nästa / förra flagga |
| `↓` / `↑` | stega utan att hoppa till nästa flagga |
| `↵` | godta modellens förslag |
| `c` | korrigera med eget ord |
| `r` | rätt som det är (`accept`) |
| `d` | radera ordet (`delete`) |
| `s` | osäker (`osaker`) |
| Shift-klick + `f` | ersätt markerad fras |
| `i` / `Shift+i` | infoga ord efter / före |
| `Esc` | stäng öppen dialog |
| `Space` | loopa ljudet ±5 s runt ordet; `[` / `]` kortare / längre |

Hanteraren returnerar tidigt på `INPUT` och på `Ctrl`/`Cmd`/`Alt`, så
webbläsarens egna genvägar och textfälten fungerar som vanligt. Allt utom
`n`/`p`/`Space`/`[`/`]` kräver att ett ord har fokus.

Ljudloopen runt ordet är den funktion som gör steget praktiskt genomförbart — utan
den måste man leta i en mediaspelare för varje flagga.

---

## 7. Konfiguration

```toml
[corrections]
llm_modell = "claude-opus-4-8"
llm_chunk_ord = 300        # ord per API-anrop
llm_max_ord = 0            # 0 = hela filen; sätt lågt vid prototyping
context_max_words = 10     # kontextfönster i .txt-utdata (om ni behåller det)
context_min_words = 7
merge_gap = 4              # flaggor närmare än så slås ihop till ett block
```

Nyckeln läses ur en gitignorerad `.env` (`ANTHROPIC_API_KEY`).

---

## 8. Fallgropar, alla betalda en gång

- **Skriv aldrig över en befintlig `-corrections.*` utan att fråga.** Den kan bära
  timmar av handgranskning. Enfilsvägen vägrar; batchen hoppar över.
- **Avbryt hela batchen om ingen bit går igenom.** Slut på API-krediter mitt i en
  kö lämnar annars fyrtio filer halvflaggade — det har hänt. Skilj på "en bit
  misslyckades" (hoppa över, rapportera) och "noll tokens gick igenom" (systemiskt
  fel, stanna).
- **Låt inte batchen ändra vilken fil GUI:t visar.** Filvalet är människans, inte
  en biverkning av ett skript.
- **Idempotens genom filnärvaro.** En fil med sidecar hoppas över. En avbruten
  körning fortsätter där den slutade utan bokföring.
- **Materialets språk måste kontrolleras av en människa.** En svensk modell på ett
  engelskt memo **översätter** i stället för att transkribera, och resultatet är
  flytande svenska som inte är vad som sägs. Ingenting fångade det automatiskt:
  ord-konfidensen låg på 0,686 mot normala 0,70–0,76, och `language_probability`
  var 1 eftersom språket var tvingat i konfigurationen. Bygg in en manuell
  märkning ("ny transkription behövs") som batchen respekterar.
- **`probability = 1.0` som mänsklig signatur.** Apply sätter det på granskade
  ord. Praktiskt, men det betyder att fältet inte längre är modellens
  konfidens — dokumentera det, annars mäter någon fel senare.

---

## 9. Filer att kopiera

| Fil | Roll |
| --- | --- |
| `flagga-llm.py` | detektorn: systemprompt, schema, bitindelning |
| `batch-flagga.py` | kö, kostnadsuppskattning, sidecar-skrivning, idempotens |
| `korrigeringar.py` | `flatten_words`, ordlisteläsning, klustring, kontextfönster |
| `granska/index.php` | granskningsvyn, arbetskopian, tangentbordet |
| `granska/save.php` | skrivendpunkten (de tre `type`) |
| `granska/valj.php` | filväljaren och **basvalet** |
| `granska/gemensam.php` | den delade räkningen |
| `granska/audio.php` | strömmar ljud ur den read-only-monterade datamappen |
| `granska/status.php` | märkningen "ny transkription behövs" |
| `granska/compose.yaml` | hela driftsättningen |
| `ordlista/` | facit-listorna |

`migrera-corrections.py` behövs bara om ni har befintliga `.txt`-filer att rädda.
Se avsnitt 5.

Det som **inte** hör till steget och inte ska följa med: `transkribera.py`
(steg a), `forbattra.py` och `negationsvakt.py` (steg c), `applicera-corrections.py`
— men den sista är värd att läsa, eftersom den definierar vad besluten *betyder*
och bär vakterna som skyddar invarianten i avsnitt 2.
