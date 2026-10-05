<?php
// Begär nästa steg (propagering + applicering) för den fil som är vald.
//
// PHP kan inte göra jobbet självt: containern har ingen Python, /data är
// monterad read-only med flit, och projektroten är inte monterad. Därför läggs
// en beställning i granska/state/jobb.json, och arbetare.py på andra sidan
// volymen utför den och svarar i jobb-status.json.
//
//   POST {}                 -> skapar ett jobb för filen i current.json
//   GET  ?id=<jobb-id>      -> senaste status (hela jobb-status.json)
//
// Vi skriver BARA jobb.json. Statusfilen ägs av arbetaren, arbetskopian av
// save.php — en skrivare per fil, så inget lås behövs mellan språken.

header('Content-Type: application/json; charset=utf-8');

$here = __DIR__;
$dataDir = getenv('DATA_DIR') ?: '/data';
$stateDir = $here . '/state';
$jobbFil = $stateDir . '/jobb.json';
$statusFil = $stateDir . '/jobb-status.json';
$hjartFil = $stateDir . '/arbetare.json';

// Så gammalt hjärtslag får arbetaren ha och ändå räknas som vaken. Den slår
// varje pollvarv (2 s), så 15 s är gott om marginal för en seg Dropbox-volym.
const HJARTSLAG_MAX = 15;

function bail(int $kod, string $msg, array $extra = []): void {
    http_response_code($kod);
    echo json_encode(['error' => $msg] + $extra, JSON_UNESCAPED_UNICODE);
    exit;
}

function las(string $path): ?array {
    if (!is_file($path)) return null;
    $d = json_decode(@file_get_contents($path), true);
    return is_array($d) ? $d : null;
}

/** Sekunder sedan arbetaren senast hördes, eller null om aldrig. */
function hjartslag_alder(string $path): ?int {
    $h = las($path);
    if (!$h || empty($h['hjartslag'])) return null;
    $t = strtotime($h['hjartslag']);
    return $t === false ? null : max(0, time() - $t);
}

// --------------------------------------------------------------------------- //
// GET: status
// --------------------------------------------------------------------------- //

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'GET') {
    $st = las($statusFil);
    $alder = hjartslag_alder($hjartFil);
    echo json_encode([
        'ok' => true,
        'status' => $st,
        'arbetare' => ['alder' => $alder, 'vaken' => $alder !== null && $alder <= HJARTSLAG_MAX],
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// --------------------------------------------------------------------------- //
// POST: beställ
// --------------------------------------------------------------------------- //

require_once __DIR__ . '/gemensam.php';

$cur = las($here . '/current.json');
if (!$cur || empty($cur['stem'])) bail(409, 'ingen fil är vald i GUI:t');

$stem = $cur['stem'];
$arbetskopia = basename($cur['sidecar_json'] ?? '');
$workPath = $stateDir . '/' . $arbetskopia;
if ($arbetskopia === '' || !is_file($workPath)) {
    bail(409, 'ingen arbetskopia i state/ — öppna filen i granskningsvyn först');
}

$side = las($workPath);
if ($side === null) bail(500, 'arbetskopian går inte att tolka');

// Samma spärr som knappen har i gränssnittet, men serversidan måste också hålla:
// en gammal flik kan posta efter att nya flaggor tillkommit.
$kvar = rakna_beslut($side)['pending'];
if ($kvar > 0) bail(409, "$kvar flagga(or) är ogranskade — avgör dem först");

// Arbetaren måste vara vaken, annars hamnar jobbet i en fil ingen läser.
$alder = hjartslag_alder($hjartFil);
if ($alder === null) {
    bail(503, 'arbetaren har aldrig hörts av — starta den med "docker compose up" '
            . 'i granska/, eller kör "python arbetare.py --en-gang" på värden');
}
if ($alder > HJARTSLAG_MAX) {
    bail(503, "arbetaren svarar inte (senast sedd för {$alder} s sedan) — "
            . 'se terminalen där "docker compose up" kör');
}

// Ett jobb i taget. Kör ett annat just nu vägrar vi hellre än att skriva över
// beställningen — den som kör skulle inte märka det.
$gammal = las($statusFil);
$jobbNu = las($jobbFil);
if ($jobbNu && $gammal && ($gammal['id'] ?? null) === ($jobbNu['id'] ?? null)
    && ($gammal['lage'] ?? '') === 'kor') {
    bail(409, 'ett jobb kör redan för ' . ($gammal['stem'] ?? '?'));
}
if ($jobbNu && (!$gammal || ($gammal['id'] ?? null) !== ($jobbNu['id'] ?? null))) {
    bail(409, 'ett jobb väntar redan på att köras');
}

// current.json pekar på rundans ORÖRDA bas (-bak.json / -bak2.json) för en
// applicerad fil. Jobbet ska bära den LEVANDE texten, som apply skriver —
// samma normalisering som korrigeringar.aktuell_json() gör.
$transcript = $cur['transcript_json'] ?? '';
foreach (['-bak2.json', '-bak.json'] as $slut) {
    if (str_ends_with($transcript, $slut)) {
        $transcript = substr($transcript, 0, -strlen($slut)) . '.json';
        break;
    }
}
if ($transcript === '' || !is_file($dataDir . '/' . $transcript)) {
    bail(409, "transkriptet saknas i /data: $transcript");
}

$jobb = [
    'id' => date('Ymd-His') . '-' . $stem,
    'begard' => date('c'),
    'atgard' => 'propagera-applicera',
    'stem' => $stem,
    'transcript_json' => $transcript,
    'sidecar_json' => $cur['sidecar_json'],
    'arbetskopia' => $arbetskopia,
    // Innehållsstämpeln ersätter låset: ändras arbetskopian mellan klicket och
    // körningen vägrar arbetaren, så inget appliceras på ett underlag Lars inte
    // godkänt.
    'sidecar_sha256' => hash_file('sha256', $workPath),
];

$fp = fopen($jobbFil, 'c+');
if (!$fp || !flock($fp, LOCK_EX)) bail(500, 'kunde inte låsa jobb.json');
ftruncate($fp, 0);
rewind($fp);
fwrite($fp, json_encode($jobb, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
fflush($fp);
flock($fp, LOCK_UN);
fclose($fp);

echo json_encode(['ok' => true, 'id' => $jobb['id'], 'stem' => $stem],
                 JSON_UNESCAPED_UNICODE);
