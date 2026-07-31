<?php
// Steg 1b — skrivendpunkt. Uppdaterar arbetskopian (granska/state/<stem>-corrections.json).
// Källan i /data rörs aldrig. Tre sorters beslut via fältet "type":
//
//   type:"word"       { global_index, decision, replacement, [heard, anchor, ai_guess] }
//                       decision: replace | accept | delete | osaker | pending
//                       upsert på global_index (typ 1 + typ 3).
//   type:"phrase"     { span_start, span_end, anchor, original, replacement }
//                       upsert i phrase_edits på span_start (typ 4).
//   type:"insertion"  { after_index, word, [anchor] }
//                       append i insertions (typ 2).

header('Content-Type: application/json; charset=utf-8');

function bail(int $code, string $msg): void {
    http_response_code($code);
    echo json_encode(['error' => $msg], JSON_UNESCAPED_UNICODE);
    exit;
}

$here = __DIR__;
$cur = json_decode(@file_get_contents($here . '/current.json'), true);
if (!$cur) bail(500, 'current.json saknas');
$path = $here . '/state/' . basename($cur['sidecar_json']);   // state/ är platt
if (!is_file($path)) bail(500, 'arbetskopia saknas — ladda om läsvyn först');

$in = json_decode(file_get_contents('php://input'), true);
if (!is_array($in)) bail(400, 'ogiltig indata');
$type = $in['type'] ?? 'word';

$fp = fopen($path, 'c+');
if (!$fp || !flock($fp, LOCK_EX)) bail(500, 'kunde inte låsa arbetskopian');
$side = json_decode(stream_get_contents($fp), true);
if (!is_array($side)) { flock($fp, LOCK_UN); fclose($fp); bail(500, 'arbetskopian går inte att tolka'); }
$side['phrase_edits'] = $side['phrase_edits'] ?? [];
$side['insertions']  = $side['insertions']  ?? [];

$extra = [];

if ($type === 'word') {
    if (!isset($in['global_index'], $in['decision'])) bail(400, 'ordbeslut kräver global_index + decision');
    if (!in_array($in['decision'], ['replace','accept','delete','osaker','pending'], true)) bail(400, 'okänt beslut');
    $gi = (int) $in['global_index'];
    $decision = $in['decision'];
    $replacement = trim((string) ($in['replacement'] ?? ''));
    $found = false;
    foreach ($side['flags'] as &$f) {
        if ($f['global_index'] === $gi) {
            $f['decision'] = $decision; $f['replacement'] = $replacement; $f['reviewed'] = true;
            $found = true; break;
        }
    }
    unset($f);
    if (!$found) {
        $side['flags'][] = [
            'anchor' => isset($in['anchor']) ? (float) $in['anchor'] : null,
            'global_index' => $gi, 'heard' => (string) ($in['heard'] ?? ''),
            'verify_ok' => true, 'ai_guess' => (string) ($in['ai_guess'] ?? ''), 'ai_reason' => '',
            'decision' => $decision, 'replacement' => $replacement,
            'note' => 'ny (GUI, typ 3)', 'line' => null, 'reviewed' => true,
        ];
    }
    $extra['created'] = !$found;

} elseif ($type === 'phrase') {
    if (!isset($in['span_start'], $in['span_end'], $in['replacement'])) bail(400, 'fras kräver span_start + span_end + replacement');
    $ss = (int) $in['span_start']; $se = (int) $in['span_end'];
    if ($se < $ss) bail(400, 'span_end < span_start');
    $entry = [
        'kind' => 'fras', 'span_start' => $ss, 'span_end' => $se,
        'anchor' => isset($in['anchor']) ? (float) $in['anchor'] : null,
        'original' => (string) ($in['original'] ?? ''),
        'replacement' => trim((string) $in['replacement']),
        'reviewed' => true,
    ];
    $found = false;
    foreach ($side['phrase_edits'] as &$p) {
        if (isset($p['span_start']) && $p['span_start'] === $ss) { $p = $entry; $found = true; break; }
    }
    unset($p);
    if (!$found) $side['phrase_edits'][] = $entry;
    $extra['created'] = !$found;

} elseif ($type === 'insertion') {
    if (!isset($in['after_index'], $in['word'])) bail(400, 'infogning kräver after_index + word');
    $w = trim((string) $in['word']);
    if ($w === '') bail(400, 'tomt ord');
    $side['insertions'][] = [
        'kind' => 'infogning', 'after_index' => (int) $in['after_index'], 'word' => $w,
        'anchor' => isset($in['anchor']) ? (float) $in['anchor'] : null, 'reviewed' => true,
    ];
    $extra['created'] = true;

} else {
    flock($fp, LOCK_UN); fclose($fp); bail(400, 'okänd type');
}

ftruncate($fp, 0);
rewind($fp);
fwrite($fp, json_encode($side, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
fflush($fp);
flock($fp, LOCK_UN);
fclose($fp);

$c = ['replace' => 0, 'delete' => 0, 'accept' => 0, 'osaker' => 0, 'pending' => 0];
foreach ($side['flags'] as $f) { $c[$f['decision']] = ($c[$f['decision']] ?? 0) + 1; }
$structPhrases = 0; foreach ($side['phrase_edits'] as $p) { if (isset($p['span_start'])) $structPhrases++; }
$structInserts = 0; foreach ($side['insertions'] as $i) { if (isset($i['after_index'])) $structInserts++; }

echo json_encode(array_merge(['ok' => true, 'counts' => $c, 'total' => count($side['flags']),
    'phrases' => $structPhrases, 'insertions' => $structInserts], $extra), JSON_UNESCAPED_UNICODE);
