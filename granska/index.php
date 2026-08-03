<?php
// Steg 1b — redigeringsvy. Hybrid: hela transkriptet som löptext, flaggorna
// markerade, en åtgärdspanel för det ord som är i fokus. Tangentbord först.
// Beslut sparas i arbetskopian (state/), källan i /data rörs aldrig.
//
// Ordredigering (typ 1) + klicka valfritt oflaggat ord (typ 3). Fras (F) och
// infogning (typ 2) kommer i nästa steg.

$here = __DIR__;
$dataDir = getenv('DATA_DIR') ?: '/data';

function fail(string $msg): void {
    http_response_code(500);
    echo "<!doctype html><meta charset='utf-8'><body style='font:16px system-ui;padding:2rem;max-width:40rem'>";
    echo "<h1>Kan inte visa granskningen</h1><p>" . htmlspecialchars($msg) . "</p>";
    echo "<p>Kör <code>python migrera-corrections.py</code> i projektroten först, och "
       . "starta sedan om med <code>docker compose up</code> i <code>granska/</code>.</p></body>";
    exit;
}

// Ingen fil vald än -> till väljaren i stället för ett felmeddelande.
$curFile = $here . '/current.json';
if (!is_file($curFile)) { header('Location: valj.php'); exit; }
$cur = json_decode(file_get_contents($curFile), true);
if (!$cur) { header('Location: valj.php'); exit; }

$tjPath = $dataDir . '/' . $cur['transcript_json'];
if (!is_file($tjPath)) fail("Transkript saknas i /data: {$cur['transcript_json']}");

// Arbetskopian: fröas från /data-sidecaren första gången, redigeras sedan här.
$stateDir = $here . '/state';
if (!is_dir($stateDir)) @mkdir($stateDir, 0777, true);
// Fröet ligger i /data på sin relativa sökväg (ljudet bor i temamappar), men
// arbetskopian hålls platt i state/ — så applicera-corrections.py hittar den på
// <stem>-corrections.json oavsett vilken temamapp memot kommer från.
$workPath = $stateDir . '/' . basename($cur['sidecar_json']);
$seedPath = $dataDir . '/' . $cur['sidecar_json'];
if (!is_file($workPath)) {
    if (is_file($seedPath)) {
        if (!@copy($seedPath, $workPath)) fail("Kunde inte skapa arbetskopia i state/ (skrivrättigheter?).");
    } else {
        // Ingen sidecar: filen har inte flaggats av steg b, eller detektorn hittade
        // inget. Det är inget fel — en tom arbetskopia räcker för att granska, och
        // typ 3 (klicka valfritt ord) fungerar som vanligt.
        $tom = ['flags' => [], 'phrase_edits' => [], 'insertions' => [], 'review' => [],
                'source' => 'granska/index.php (tom — ingen flaggning fanns)'];
        if (@file_put_contents($workPath, json_encode($tom, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT)) === false) {
            fail("Kunde inte skapa tom arbetskopia i state/ (skrivrättigheter?).");
        }
    }
}

$data = json_decode(file_get_contents($tjPath), true);
$side = json_decode(file_get_contents($workPath), true);
if (!$data || !$side) fail("JSON går inte att tolka.");
$side['flags'] = $side['flags'] ?? [];

// Ljudfilen kan saknas (äldre transkript bär ingen audio_file, och gissningen kan
// slå fel). Granskningen ska fungera ändå — text går att rätta utan ljud.
$harLjud = is_file($dataDir . '/' . ($cur['audio_file'] ?? ''));

// Filstatus (granska/status.json): märkning om att transkriptionen inte duger
// och behöver göras om, t.ex. för att memot är på ett annat språk än modellen.
$statusAlla = json_decode(@file_get_contents($here . '/status.json'), true) ?: [];
$filStatus = $statusAlla[$cur['transcript_json']] ?? null;
$temamapp = dirname($cur['transcript_json']);
$temamapp = ($temamapp === '.' || $temamapp === '') ? '(inkorgen)' : $temamapp;

// Platta ut orden EXAKT som korrigeringar.flatten_words (samma globala index).
$words = [];
foreach ($data['segments'] ?? [] as $seg) {
    $segStart = $seg['start'] ?? null;
    foreach ($seg['words'] ?? [] as $w) {
        $words[] = ['word' => $w['word'] ?? '', 'start' => $w['start'] ?? $segStart];
    }
}

$flagByIndex = [];
foreach ($side['flags'] as $f) {
    if ($f['global_index'] !== null) $flagByIndex[$f['global_index']] = $f;
}

// Fraser: strukturerade (från GUI, har span_start) vs migrerade (referens).
$phrasesStruct = [];
$phrasesMigr = [];
$phraseCover = [];   // globalt ordindex -> täcks av en strukturerad fras
foreach ($side['phrase_edits'] ?? [] as $p) {
    if (isset($p['span_start'], $p['span_end'])) {
        $phrasesStruct[] = $p;
        for ($k = $p['span_start']; $k <= $p['span_end']; $k++) $phraseCover[$k] = true;
    } else {
        $phrasesMigr[] = $p;
    }
}

// Infogningar: strukturerade (har after_index) vs migrerade (referens).
$insStruct = [];
$insMigr = [];
$insByAfter = [];   // after_index -> [ord, ...] att rendera som pills inline
foreach ($side['insertions'] ?? [] as $ins) {
    if (isset($ins['after_index'])) {
        $insStruct[] = $ins;
        $insByAfter[(int) $ins['after_index']][] = $ins['word'];
    } else {
        $insMigr[] = $ins;
    }
}
function pill_html(string $word): string {
    return '<span class="pill">&#8248;' . htmlspecialchars($word) . '</span>';
}

$decCount = ['replace' => 0, 'delete' => 0, 'accept' => 0, 'osaker' => 0, 'pending' => 0];
foreach ($side['flags'] as $f) { $decCount[$f['decision']] = ($decCount[$f['decision']] ?? 0) + 1; }
$decided = $decCount['replace'] + $decCount['delete'] + $decCount['accept'];
?>
<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Granska — <?= htmlspecialchars($cur['stem']) ?></title>
<style>
  :root {
    --bg:#faf9f7; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4;
    --pending:#fde68a; --pending-b:#d97706;
    --decided:#bbf7d0; --decided-b:#16a34a;
    --delete:#fecaca; --delete-b:#dc2626;
    --osaker:#c7d2fe; --osaker-b:#4f46e5;
    --bad:#f0abfc; --bad-b:#a21caf;
    --acc:#2563eb;
  }
  * { box-sizing:border-box; }
  body { margin:0; font:17px/1.7 Georgia,'Iowan Old Style',serif; color:var(--fg); background:var(--bg); }
  header { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line);
           padding:.6rem 1.2rem; display:flex; gap:1.2rem; align-items:baseline; flex-wrap:wrap;
           font-family:system-ui,sans-serif; font-size:14px; }
  header h1 { font-size:15px; margin:0; font-weight:700; }
  #counts b { font-variant-numeric:tabular-nums; }
  .layout { display:grid; grid-template-columns: minmax(0,1fr) 24rem; gap:0; }
  main { padding:1.5rem clamp(1rem,4vw,3rem); max-width:52rem; }
  .w { border-radius:3px; cursor:pointer; }
  .flag { padding:0 1px; border-bottom:2px solid transparent; }
  .flag.d-pending { background:var(--pending); border-bottom-color:var(--pending-b); }
  .flag.d-replace, .flag.d-accept { background:var(--decided); border-bottom-color:var(--decided-b); }
  .flag.d-delete  { background:var(--delete); border-bottom-color:var(--delete-b); }
  .flag.d-osaker  { background:var(--osaker); border-bottom:2px dashed var(--osaker-b); }
  .flag.badverify { outline:2px dashed var(--bad-b); }
  .w.cursor { box-shadow:0 0 0 3px var(--acc); background:#dbeafe; }
  .w.inspan { background:#bfdbfe; }
  .w.inphrase { background:#d1fae5; border-bottom:2px dotted var(--decided-b); }
  .pill { display:inline-block; background:var(--decided); border:1px solid var(--decided-b);
          border-radius:5px; padding:0 5px; margin:0 2px; font-size:.85em; cursor:pointer; }
  aside { border-left:1px solid var(--line); font-family:system-ui,sans-serif; font-size:13px;
          height:100vh; position:sticky; top:0; overflow-y:auto; }
  .panel { padding:1rem 1.1rem; border-bottom:1px solid var(--line); }
  .panel h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:.2rem 0 .7rem; }
  .detail dt { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.04em; margin-top:.5rem; }
  .detail dd { margin:.1rem 0 0; font-size:14px; }
  .detail .heard { font-family:Georgia,serif; font-size:16px; }
  .chip { display:inline-block; border-radius:4px; padding:1px 7px; font-size:12px; font-weight:600; }
  .chip.d-pending { background:var(--pending); } .chip.d-replace,.chip.d-accept { background:var(--decided); }
  .chip.d-delete { background:var(--delete); } .chip.d-osaker { background:var(--osaker); }
  .actions { display:flex; flex-wrap:wrap; gap:.4rem; margin-top:.9rem; }
  .actions button { font:600 13px system-ui; padding:.35rem .6rem; border:1px solid var(--line);
                    background:#fff; border-radius:6px; cursor:pointer; }
  .actions button:hover { border-color:var(--muted); }
  .actions button.primary { background:var(--acc); color:#fff; border-color:var(--acc); }
  .actions button kbd { font:inherit; opacity:.65; margin-left:.35rem; }
  .editrow { margin-top:.7rem; display:none; gap:.4rem; }
  .editrow.open { display:flex; }
  .editrow input { flex:1; font:15px Georgia,serif; padding:.35rem .5rem; border:1px solid var(--muted); border-radius:6px; }
  .hint { color:var(--muted); font-size:12px; margin-top:.8rem; line-height:1.5; }
  .hint kbd { background:#0000000f; border-radius:3px; padding:0 4px; font-family:ui-monospace,monospace; }
  #saved { position:fixed; bottom:1rem; right:1rem; background:var(--decided-b); color:#fff;
           padding:.4rem .8rem; border-radius:6px; font:600 13px system-ui; opacity:0; transition:opacity .2s;
           pointer-events:none; }
  #saved.show { opacity:1; }
  .rev { list-style:none; margin:0; padding:0; }
  .rev li { padding:.35rem 0; border-top:1px solid var(--line); }
  .rev li:first-child { border-top:0; }
  .rev .ln { color:var(--muted); font-variant-numeric:tabular-nums; }
  code { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.85em; background:#0000000a; padding:0 3px; border-radius:3px; }
  a.tillbaka { color:var(--acc); text-decoration:none; font-weight:600; white-space:nowrap; }
  a.tillbaka:hover { text-decoration:underline; }
  .var { color:var(--muted); font-size:13px; }
  .varning { background:#fecaca; border:1px solid var(--delete-b); color:#7f1d1d;
             border-radius:5px; padding:2px 9px; font-size:13px; font-weight:600; }
  #statusBtn { font:600 13px system-ui; padding:.4rem .6rem; border:1px solid var(--line);
               background:#fff; border-radius:6px; cursor:pointer; width:100%; }
  #statusBtn:hover { border-color:var(--muted); }
  #statusBtn.pa { background:#fecaca; border-color:var(--delete-b); }
  #statusNote { width:100%; font:13px system-ui; padding:.35rem .5rem; margin-top:.4rem;
                border:1px solid var(--line); border-radius:6px; }
  @media (max-width:900px){ .layout{grid-template-columns:1fr} aside{position:static;height:auto;border-left:0;border-top:1px solid var(--line)} }
</style>
</head>
<body>
<header>
  <a class="tillbaka" href="valj.php">&#9664; Alla filer</a>
  <h1><?= htmlspecialchars($cur['stem']) ?></h1>
  <span class="var"><?= htmlspecialchars($temamapp) ?></span>
  <?php if ($filStatus): ?>
    <span class="varning" id="varning">⚠ Ny transkription behövs<?=
      $filStatus['note'] ? ' — ' . htmlspecialchars($filStatus['note']) : '' ?></span>
  <?php endif; ?>
  <span id="counts">
    <b id="c-tot"><?= count($side['flags']) ?></b> flaggor ·
    beslutade <b id="c-done"><?= $decided ?></b> ·
    kvar <b id="c-pend"><?= $decCount['pending'] ?></b> ·
    osäkra <b id="c-osaker"><?= $decCount['osaker'] ?></b>
  </span>
</header>

<div class="layout">
<main>
  <div class="transcript"><?php
    if (isset($insByAfter[-1])) foreach ($insByAfter[-1] as $iw) echo pill_html($iw);
    foreach ($words as $i => $w) {
        $raw = $w['word'];
        $lead = substr($raw, 0, strlen($raw) - strlen(ltrim($raw)));
        $core = ltrim($raw);
        echo htmlspecialchars($lead);
        $cls = 'w';
        if (isset($flagByIndex[$i])) {
            $f = $flagByIndex[$i];
            $cls = 'w flag d-' . htmlspecialchars($f['decision']);
            if (!$f['verify_ok']) $cls .= ' badverify';
        }
        if (isset($phraseCover[$i])) $cls .= ' inphrase';
        echo '<span class="' . $cls . '" id="w' . $i . '" data-i="' . $i . '">'
           . htmlspecialchars($core) . '</span>';
        if (isset($insByAfter[$i])) foreach ($insByAfter[$i] as $iw) echo pill_html($iw);
    }
  ?></div>
</main>

<aside>
  <div class="panel" id="panel">
    <h2>Åtgärd</h2>
    <div id="pbody" style="color:var(--muted)">Klicka ett ord, eller tryck <kbd>n</kbd> för första kvarvarande flagga.</div>
  </div>

  <div class="panel">
    <h2>Filstatus</h2>
    <button id="statusBtn" class="<?= $filStatus ? 'pa' : '' ?>">
      <?= $filStatus ? '✓ Märkt: ny transkription behövs' : 'Märk: ny transkription behövs' ?>
    </button>
    <input id="statusNote" placeholder="skäl, t.ex. 'talat på engelska'"
           value="<?= htmlspecialchars($filStatus['note'] ?? '') ?>" autocomplete="off">
    <p class="hint">Märkningen syns i filväljaren och gör att <code>batch-flagga.py</code>
      hoppar över filen — ingen mening att flagga ord i en transkription som ska göras om.</p>
  </div>

  <div class="panel" id="audioPanel">
    <h2>Ljud</h2>
    <?php if ($harLjud): ?>
    <div class="actions">
      <button id="btnPlay">▶ Loop <kbd>Space</kbd></button>
      <button id="btnNarrow">−5s <kbd>[</kbd></button>
      <button id="btnWiden">+5s <kbd>]</kbd></button>
    </div>
    <p class="hint" id="audioStatus">±5s runt ordet · fokusera ett ord och tryck Space</p>
    <?php else: ?>
    <p class="hint">Ljudfilen hittades inte:<br><code><?= htmlspecialchars($cur['audio_file'] ?? '—') ?></code><br>
      Granskningen fungerar ändå — men utan ljud går bara texten att bedöma.</p>
    <?php endif; ?>
  </div>

  <div class="panel">
    <h2>Osäkra (<span id="osakerN">0</span>) — återkom</h2>
    <ul class="rev" id="osakerList"></ul>
  </div>

  <div class="panel">
    <h2>Fraser (<span id="phraseN">0</span>)</h2>
    <p class="hint">Shift-klicka för att markera ett span, tryck <kbd>f</kbd>.</p>
    <ul class="rev" id="phraseList"></ul>
  </div>

  <?php if ($phrasesMigr): ?>
  <div class="panel">
    <h2>Migrerade fraser (<?= count($phrasesMigr) ?>) — referens, gör om via span</h2>
    <ul class="rev"><?php foreach ($phrasesMigr as $p): ?>
      <li><span class="ln">rad <?= $p['line'] ?? '?' ?></span> · <?= htmlspecialchars($p['kind'] ?? 'fras') ?><br>
        <?= htmlspecialchars($p['text'] ?? '') ?></li>
    <?php endforeach; ?></ul>
  </div>
  <?php endif; ?>

  <div class="panel">
    <h2>Infogningar (<span id="insertN">0</span>)</h2>
    <p class="hint">Fokusera ett ord, <kbd>i</kbd> infogar efter · <kbd>Shift+i</kbd> före.</p>
    <ul class="rev" id="insertList"></ul>
  </div>

  <?php if ($insMigr): ?>
  <div class="panel">
    <h2>Migrerade infogningar (<?= count($insMigr) ?>) — referens</h2>
    <ul class="rev"><?php foreach ($insMigr as $ins): ?>
      <li><span class="ln">rad <?= $ins['line'] ?? '?' ?></span> · infoga <code><?= htmlspecialchars($ins['word'] ?? '') ?></code></li>
    <?php endforeach; ?></ul>
  </div>
  <?php endif; ?>

  <?php if ($side['review']): ?>
  <div class="panel">
    <h2>Otolkade rader (<?= count($side['review']) ?>)</h2>
    <p class="hint">Fixa genom att klicka rätt ord i texten (typ 3).</p>
    <ul class="rev"><?php foreach ($side['review'] as $r): ?>
      <li><span class="ln">rad <?= $r['line'] ?></span>: <code><?= htmlspecialchars($r['raw']) ?></code></li>
    <?php endforeach; ?></ul>
  </div>
  <?php endif; ?>
</aside>
</div>
<?php // ?f=<stem> är en cache-nyckel: utan den har varje fil samma URL, och
      // webbläsaren spelar upp föregående fils ljud ur cachen (max-age=3600). ?>
<audio id="player" src="audio.php?f=<?= rawurlencode($cur['stem']) ?>" preload="none"></audio>
<div id="saved">sparat ✓</div>

<script>
const FLAGS = <?= json_encode($flagByIndex, JSON_UNESCAPED_UNICODE) ?>;
const WORDS = <?= json_encode(array_map(fn($w) => ['w'=>$w['word'], 's'=>$w['start']], $words), JSON_UNESCAPED_UNICODE) ?>;
let PHRASES = <?= json_encode(array_values($phrasesStruct), JSON_UNESCAPED_UNICODE) ?>;
let INSERTS = <?= json_encode(array_values($insStruct), JSON_UNESCAPED_UNICODE) ?>;

let ORDER = Object.keys(FLAGS).map(Number).sort((a,b)=>a-b);
let focus = null;               // globalt ordindex i fokus
let selStart = null, selEnd = null;   // spanmarkering för fras
let insertAfter = null;               // after_index för pågående infogning
let audioPad = 5, loopStart = 0, loopEnd = 0, looping = false;   // ljudloop ±pad
const pbody = document.getElementById('pbody');
const savedEl = document.getElementById('saved');

function esc(s){ return String(s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmt(s){ if(s==null) return '?'; const m=Math.floor(s/60); return m+':'+(s-m*60).toFixed(2).padStart(5,'0'); }

function setFocus(i){
  if(focus!==null){ const p=document.getElementById('w'+focus); if(p) p.classList.remove('cursor'); }
  focus=i; selStart=i; selEnd=i;
  const el=document.getElementById('w'+i);
  el.classList.add('cursor');
  el.scrollIntoView({block:'center', behavior:'smooth'});
  markSpan();
  renderPanel();
  if(looping) startLoop(); else updateAudioUI();   // ljudloopen följer fokus
}

function clearSpanMarks(){ document.querySelectorAll('.w.inspan').forEach(e=>e.classList.remove('inspan')); }
function markSpan(){
  clearSpanMarks();
  if(selStart===null) return;
  const a=Math.min(selStart,selEnd), b=Math.max(selStart,selEnd);
  if(b>a) for(let k=a;k<=b;k++){ const e=document.getElementById('w'+k); if(e) e.classList.add('inspan'); }
}
function extendSpan(i){ selEnd=i; markSpan(); renderPanel(); }
function spanText(a,b){ let t=''; for(let k=a;k<=b;k++) t+=WORDS[k].w; return t.trim(); }

function renderPanel(){
  const i=focus, f=FLAGS[i];
  const a=Math.min(selStart,selEnd), b=Math.max(selStart,selEnd), spanN=b-a+1;
  const heard = f ? f.heard : WORDS[i].w.trim();
  const anchor = f ? f.anchor : WORDS[i].s;
  const guess = f ? f.ai_guess : '';
  const dec = f ? f.decision : '(ingen flagga än — typ 3)';
  const dm = {pending:'kvar', replace:'ersätt', accept:'rätt som är', delete:'radera', osaker:'osäker (vet ej)'}[dec] || dec;
  pbody.innerHTML = `
    <dl class="detail">
      <dt>Ord</dt><dd class="heard">${esc(heard)}</dd>
      <dt>AI-gissning</dt><dd>${guess? esc(guess):'<span style="color:var(--muted)">—</span>'} ${f&&f.ai_reason? '<span style="color:var(--muted)">('+esc(f.ai_reason)+')</span>':''}</dd>
      <dt>Status</dt><dd>${f? '<span class="chip d-'+f.decision+'">'+dm+'</span>':'<span style="color:var(--muted)">'+dm+'</span>'}${f&&f.replacement? ' → <b>'+esc(f.replacement)+'</b>':''}</dd>
      <dt>Ankare</dt><dd><code>@${anchor!=null?anchor.toFixed(2):'?'}</code> · index ${i} · ${fmt(anchor)}${f&&!f.verify_ok?' · <span style="color:var(--bad-b)">⚠ kontrollera ord</span>':''}</dd>
      ${spanN>1? `<dt>Span</dt><dd>ord ${a}–${b} (${spanN}) · <span class="heard">${esc(spanText(a,b))}</span></dd>`:''}
    </dl>
    <div class="actions">
      ${guess? `<button class="primary" data-act="acceptai">Förslag: ${esc(guess)} <kbd>↵</kbd></button>`:''}
      <button data-act="correct">Korrigera <kbd>c</kbd></button>
      <button data-act="right">Rätt som är <kbd>r</kbd></button>
      <button data-act="delete">Radera <kbd>d</kbd></button>
      <button data-act="osaker">Osäker, vet ej <kbd>s</kbd></button>
      <button data-act="phrase">Fras${spanN>1?' ('+spanN+' ord)':''} <kbd>f</kbd></button>
      <button data-act="insafter">Infoga efter <kbd>i</kbd></button>
      <button data-act="insbefore">Infoga före</button>
    </div>
    <div class="editrow" id="editrow">
      <input id="editinput" value="${esc(guess||heard)}" autocomplete="off">
      <button data-act="savecorrect" class="primary">Spara ↵</button>
    </div>
    <div class="editrow" id="phraserow">
      <input id="phraseinput" autocomplete="off">
      <button data-act="savephrase" class="primary">Spara fras ↵</button>
    </div>
    <div class="editrow" id="insertrow">
      <input id="insertinput" placeholder="ord att infoga" autocomplete="off">
      <button data-act="saveinsert" class="primary">Infoga ↵</button>
    </div>
    <p class="hint">
      <kbd>↵</kbd> godta förslag · <kbd>c</kbd> korrigera · <kbd>r</kbd> rätt som är ·
      <kbd>d</kbd> radera · <kbd>s</kbd> osäker · <kbd>n</kbd>/<kbd>p</kbd> nästa/förra flagga ·
      <kbd>Esc</kbd> avbryt
    </p>`;
  pbody.querySelectorAll('button[data-act]').forEach(b =>
    b.addEventListener('click', () => act(b.dataset.act)));
  const inp = document.getElementById('editinput');
  inp.addEventListener('keydown', e => {
    e.stopPropagation();
    if(e.key==='Enter'){ e.preventDefault(); saveCorrect(); }
    else if(e.key==='Escape'){ closeEdit(); }
  });
  const pinp = document.getElementById('phraseinput');
  pinp.addEventListener('keydown', e => {
    e.stopPropagation();
    if(e.key==='Enter'){ e.preventDefault(); savePhrase(); }
    else if(e.key==='Escape'){ closePhrase(); }
  });
  const iinp = document.getElementById('insertinput');
  iinp.addEventListener('keydown', e => {
    e.stopPropagation();
    if(e.key==='Enter'){ e.preventDefault(); saveInsert(); }
    else if(e.key==='Escape'){ closeInsert(); }
  });
}

function openEdit(){ document.getElementById('editrow').classList.add('open');
  const inp=document.getElementById('editinput'); inp.focus(); inp.select(); }
function closeEdit(){ const r=document.getElementById('editrow'); if(r) r.classList.remove('open'); }
function openPhrase(){
  const a=Math.min(selStart,selEnd), b=Math.max(selStart,selEnd);
  const inp=document.getElementById('phraseinput');
  inp.value=spanText(a,b);
  document.getElementById('phraserow').classList.add('open');
  inp.focus(); inp.select();
}
function closePhrase(){ const r=document.getElementById('phraserow'); if(r) r.classList.remove('open'); }
function openInsert(pos){
  insertAfter = pos==='after' ? focus : focus-1;   // "före" = infoga efter föregående ord
  const inp=document.getElementById('insertinput'); inp.value='';
  document.getElementById('insertrow').classList.add('open');
  inp.focus();
}
function closeInsert(){ const r=document.getElementById('insertrow'); if(r) r.classList.remove('open'); }

function act(a){
  if(focus===null) return;
  const f=FLAGS[focus];
  if(a==='acceptai'){ if(f&&f.ai_guess) doSave('replace', f.ai_guess); }
  else if(a==='right')  doSave('accept', '');
  else if(a==='delete') doSave('delete', '');
  else if(a==='osaker') doSave('osaker', '');
  else if(a==='correct') openEdit();
  else if(a==='savecorrect') saveCorrect();
  else if(a==='phrase') openPhrase();
  else if(a==='savephrase') savePhrase();
  else if(a==='insafter') openInsert('after');
  else if(a==='insbefore') openInsert('before');
  else if(a==='saveinsert') saveInsert();
}

function saveCorrect(){
  const v=document.getElementById('editinput').value.trim();
  if(!v){ return; }
  const f=FLAGS[focus];
  const heard=(f?f.heard:WORDS[focus].w.trim());
  doSave(v===heard? 'accept':'replace', v===heard? '':v);
}

function doSave(decision, replacement){
  const i=focus, f=FLAGS[i];
  const body={ global_index:i, decision, replacement:replacement||'',
    heard: f? f.heard : WORDS[i].w.trim(),
    anchor: f? f.anchor : WORDS[i].s,
    ai_guess: f? f.ai_guess : '' };
  fetch('save.php', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
    .then(r=>r.json())
    .then(res=>{
      if(!res.ok){ alert('Sparfel: '+(res.error||'okänt')); return; }
      applyLocal(i, decision, body.replacement, body, res.created);
      flashSaved(); updateCounts(res.counts); closeEdit(); advance();
    })
    .catch(e=>alert('Nätverksfel vid sparning: '+e));
}

function applyLocal(i, decision, replacement, body, created){
  if(!FLAGS[i]){
    FLAGS[i]={anchor:body.anchor, global_index:i, heard:body.heard, verify_ok:true,
              ai_guess:body.ai_guess, ai_reason:'', decision, replacement, note:'ny (GUI)', line:null};
    if(!ORDER.includes(i)){ ORDER.push(i); ORDER.sort((a,b)=>a-b); }
  } else { FLAGS[i].decision=decision; FLAGS[i].replacement=replacement; }
  const el=document.getElementById('w'+i);
  el.classList.add('flag');
  el.classList.remove('d-pending','d-replace','d-accept','d-delete','d-osaker');
  el.classList.add('d-'+decision);
  if(i===focus) renderPanel();
  rebuildOsakerList();
}

function rebuildOsakerList(){
  const items = ORDER.filter(i => FLAGS[i] && FLAGS[i].decision==='osaker');
  document.getElementById('osakerN').textContent = items.length;
  const ul = document.getElementById('osakerList');
  ul.innerHTML = items.length
    ? items.map(i => { const f=FLAGS[i];
        return `<li><a href="#" data-jump="${i}" style="color:inherit;text-decoration:none">`
             + `<span class="ln">@${f.anchor!=null?f.anchor.toFixed(2):'?'}</span> · ${esc(f.heard)}</a></li>`; }).join('')
    : '<li style="color:var(--muted)">inga än</li>';
  ul.querySelectorAll('a[data-jump]').forEach(a =>
    a.addEventListener('click', e => { e.preventDefault(); setFocus(+a.dataset.jump); }));
}

function savePhrase(){
  const v=document.getElementById('phraseinput').value.trim();
  if(!v) return;
  const a=Math.min(selStart,selEnd), b=Math.max(selStart,selEnd), orig=spanText(a,b);
  fetch('save.php', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'phrase', span_start:a, span_end:b, anchor:WORDS[a].s, original:orig, replacement:v})})
    .then(r=>r.json())
    .then(res=>{
      if(!res.ok){ alert('Sparfel: '+(res.error||'okänt')); return; }
      PHRASES = PHRASES.filter(p=>p.span_start!==a);
      PHRASES.push({kind:'fras', span_start:a, span_end:b, anchor:WORDS[a].s, original:orig, replacement:v, reviewed:true});
      PHRASES.sort((x,y)=>x.span_start-y.span_start);
      markPhrases(); rebuildPhraseList(); flashSaved(); closePhrase();
    })
    .catch(e=>alert('Nätverksfel: '+e));
}
function markPhrases(){
  document.querySelectorAll('.w.inphrase').forEach(e=>e.classList.remove('inphrase'));
  PHRASES.forEach(p=>{ for(let k=p.span_start;k<=p.span_end;k++){ const e=document.getElementById('w'+k); if(e) e.classList.add('inphrase'); } });
}
function rebuildPhraseList(){
  document.getElementById('phraseN').textContent=PHRASES.length;
  const ul=document.getElementById('phraseList');
  ul.innerHTML = PHRASES.length ? PHRASES.map(p =>
    `<li><a href="#" data-jump="${p.span_start}" style="color:inherit;text-decoration:none">`
    + `<span class="ln">@${p.anchor!=null?p.anchor.toFixed(2):'?'}</span> · ${esc(p.original)} <span class="arrow">→</span> <b>${esc(p.replacement)}</b></a></li>`
  ).join('') : '<li style="color:var(--muted)">inga än</li>';
  ul.querySelectorAll('a[data-jump]').forEach(a =>
    a.addEventListener('click', e => { e.preventDefault(); setFocus(+a.dataset.jump); }));
}

function updateCounts(c){
  document.getElementById('c-done').textContent = c.replace+c.delete+c.accept;
  document.getElementById('c-pend').textContent = c.pending;
  document.getElementById('c-osaker').textContent = c.osaker;
}

let savedT=null;
function flashSaved(){ savedEl.classList.add('show'); clearTimeout(savedT);
  savedT=setTimeout(()=>savedEl.classList.remove('show'), 900); }

function advance(){
  // Nästa kvarvarande (pending) flagga efter fokus; annars första pending; annars stanna.
  const after=ORDER.filter(i=>i>focus && FLAGS[i].decision==='pending');
  const any=ORDER.filter(i=>FLAGS[i].decision==='pending');
  if(after.length) setFocus(after[0]);
  else if(any.length) setFocus(any[0]);
  else pbody.innerHTML='<b>Alla flaggor gjorda 🎉</b><p class="hint">Klicka ett ord för att ändra, eller stäng.</p>';
}
function step(dir){
  if(focus===null){ if(ORDER.length) setFocus(ORDER[0]); return; }
  const list = dir>0 ? ORDER.filter(i=>i>focus) : ORDER.filter(i=>i<focus).reverse();
  if(list.length) setFocus(list[0]);
}

function saveInsert(){
  const w=document.getElementById('insertinput').value.trim();
  if(!w || insertAfter===null) return;
  const ai=insertAfter;
  const anchor = ai>=0 ? WORDS[ai].s : (WORDS[0]? WORDS[0].s : 0);
  fetch('save.php', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'insertion', after_index:ai, word:w, anchor})})
    .then(r=>r.json())
    .then(res=>{
      if(!res.ok){ alert('Sparfel: '+(res.error||'okänt')); return; }
      INSERTS.push({kind:'infogning', after_index:ai, word:w, anchor, reviewed:true});
      addPill(ai, w); rebuildInsertList(); flashSaved(); closeInsert();
    })
    .catch(e=>alert('Nätverksfel: '+e));
}
function addPill(afterIndex, word){
  const html=`<span class="pill">&#8248;${esc(word)}</span>`;
  if(afterIndex<0) document.querySelector('.transcript').insertAdjacentHTML('afterbegin', html);
  else { const el=document.getElementById('w'+afterIndex); if(el) el.insertAdjacentHTML('afterend', html); }
}
function rebuildInsertList(){
  document.getElementById('insertN').textContent=INSERTS.length;
  const ul=document.getElementById('insertList');
  ul.innerHTML = INSERTS.length ? INSERTS.map(x =>
    `<li><a href="#" data-jump="${Math.max(0,x.after_index)}" style="color:inherit;text-decoration:none">`
    + `<span class="ln">@${x.anchor!=null?x.anchor.toFixed(2):'?'}</span> · efter idx ${x.after_index} · <b>${esc(x.word)}</b></a></li>`
  ).join('') : '<li style="color:var(--muted)">inga än</li>';
  ul.querySelectorAll('a[data-jump]').forEach(a =>
    a.addEventListener('click', e => { e.preventDefault(); setFocus(+a.dataset.jump); }));
}

// Klick på valfritt ord (flaggat eller ej — typ 3).
document.querySelector('.transcript').addEventListener('click', e => {
  const sp=e.target.closest('span[data-i]'); if(!sp) return;
  const i=+sp.dataset.i;
  if(e.shiftKey && focus!==null) extendSpan(i);   // utöka span för fras
  else setFocus(i);
});

document.addEventListener('keydown', e => {
  if(e.target.tagName==='INPUT') return;          // inputen sköter sina egna tangenter
  if(e.metaKey||e.ctrlKey||e.altKey) return;
  const k=e.key.toLowerCase();
  if(k==='n'){ e.preventDefault(); focus===null?step(1):advance(); }
  else if(k==='p'){ e.preventDefault(); step(-1); }
  else if(k===' '){ e.preventDefault(); toggleLoop(); }
  else if(k===']'){ e.preventDefault(); adjustPad(5); }
  else if(k==='['){ e.preventDefault(); adjustPad(-5); }
  else if(focus===null) return;
  else if(k==='enter'){ e.preventDefault(); act('acceptai'); }
  else if(k==='c'){ e.preventDefault(); act('correct'); }
  else if(k==='r'){ e.preventDefault(); act('right'); }
  else if(k==='d'){ e.preventDefault(); act('delete'); }
  else if(k==='s'){ e.preventDefault(); act('osaker'); }
  else if(k==='f'){ e.preventDefault(); act('phrase'); }
  else if(k==='i'){ e.preventDefault(); openInsert(e.shiftKey?'before':'after'); }
  else if(k==='escape'){ closeEdit(); closePhrase(); closeInsert(); }
  else if(k==='arrowdown'){ e.preventDefault(); step(1); }
  else if(k==='arrowup'){ e.preventDefault(); step(-1); }
});

// --- Ljudloop: ±audioPad sekunder runt fokusordet, loopad ---
const player = document.getElementById('player');
function loopWindow(){
  const c = focus!==null ? (WORDS[focus].s||0) : 0;
  loopStart = Math.max(0, c - audioPad);
  loopEnd = c + audioPad;
}
function startLoop(){
  if(focus===null) return;
  loopWindow();
  try { player.currentTime = loopStart; } catch(e) {}
  player.play().catch(()=>{});
  looping = true; updateAudioUI();
}
function stopLoop(){ player.pause(); looping=false; updateAudioUI(); }
function toggleLoop(){ looping ? stopLoop() : startLoop(); }
function adjustPad(d){
  audioPad = Math.max(1, audioPad + d);
  if(looping){ loopWindow(); if(player.currentTime<loopStart || player.currentTime>loopEnd) player.currentTime=loopStart; }
  updateAudioUI();
}
function updateAudioUI(){
  const bp=document.getElementById('btnPlay');
  if(bp) bp.innerHTML = (looping?'⏸ Pausa':'▶ Loop')+' <kbd>Space</kbd>';
  const st=document.getElementById('audioStatus');
  if(st) st.textContent = focus!==null
    ? `±${audioPad}s · ${loopStart.toFixed(1)}–${loopEnd.toFixed(1)} s`
    : `±${audioPad}s runt ordet · fokusera ett ord och tryck Space`;
}
player.addEventListener('timeupdate', () => { if(looping && player.currentTime>=loopEnd) player.currentTime=loopStart; });
player.addEventListener('ended', () => { if(looping){ player.currentTime=loopStart; player.play().catch(()=>{}); } });
// Knapparna saknas när ljudfilen inte hittades — utan ?. kastas ett fel här och
// resten av initieringen nedan körs aldrig.
document.getElementById('btnPlay')?.addEventListener('click', toggleLoop);
document.getElementById('btnNarrow')?.addEventListener('click', () => adjustPad(-5));
document.getElementById('btnWiden')?.addEventListener('click', () => adjustPad(5));

// --- Filstatus: "ny transkription behövs" ---
const TRANSKRIPT = <?= json_encode($cur['transcript_json'], JSON_UNESCAPED_UNICODE) ?>;
let statusPa = <?= $filStatus ? 'true' : 'false' ?>;
const statusBtn = document.getElementById('statusBtn');
statusBtn.addEventListener('click', () => {
  const note = document.getElementById('statusNote').value.trim();
  fetch('status.php', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({fil: TRANSKRIPT, status: statusPa ? null : 'ny-transkription', note})})
    .then(r => r.json())
    .then(res => {
      if(!res.ok){ alert('Kunde inte spara status: ' + (res.error||'okänt')); return; }
      statusPa = !statusPa;
      statusBtn.textContent = statusPa ? '✓ Märkt: ny transkription behövs' : 'Märk: ny transkription behövs';
      statusBtn.classList.toggle('pa', statusPa);
      flashSaved();
      const v = document.getElementById('varning');
      if (statusPa && !v) location.reload();     // visa varningen i huvudet
      else if (!statusPa && v) v.remove();
    })
    .catch(e => alert('Nätverksfel: ' + e));
});

rebuildOsakerList();   // fyll listorna vid start
markPhrases();
rebuildPhraseList();
rebuildInsertList();
updateAudioUI();
</script>
</body>
</html>
