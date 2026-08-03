<?php
// Filstatus — märk ett memo som "behöver ny transkription" och tillbaka igen.
//
// Bakgrund: zego-josh-hawley-jonathan-edwards talades in på ENGELSKA. KB-Whisper
// är tränad för svenska och ÖVERSATTE i stället för att transkribera — resultatet
// är flytande svenska som inte är vad som sägs i ljudet. Ord-konfidensen (0,686)
// avslöjade det knappt, och språkdetekteringen är avstängd av config (language="sv"),
// så ingenting fångade det automatiskt. Det behövs alltså ett mänskligt märke.
//
// Statusen bor i granska/status.json — INTE i state/ (gitignorerad, försvinner)
// och inte i datamappen (monterad read-only, med flit: ljudet ska skyddas).
// Filen är alltså versionerad och överlever.

header('Content-Type: application/json; charset=utf-8');

$here = __DIR__;
$statusFil = $here . '/status.json';

const GILTIGA = ['ny-transkription'];   // utöka här när fler lägen behövs

function bail(int $kod, string $msg): void {
    http_response_code($kod);
    echo json_encode(['error' => $msg], JSON_UNESCAPED_UNICODE);
    exit;
}

$in = json_decode(file_get_contents('php://input'), true);
if (!is_array($in) || empty($in['fil'])) bail(400, 'fil saknas');

$fil = $in['fil'];
if (str_contains($fil, '..') || str_starts_with($fil, '/')) bail(400, 'ogiltig sökväg');

$status = $in['status'] ?? null;          // null = ta bort märkningen
if ($status !== null && !in_array($status, GILTIGA, true)) bail(400, 'okänd status');

$fp = fopen($statusFil, 'c+');
if (!$fp || !flock($fp, LOCK_EX)) bail(500, 'kunde inte låsa status.json');
$alla = json_decode(stream_get_contents($fp), true);
if (!is_array($alla)) $alla = [];

if ($status === null) {
    unset($alla[$fil]);
} else {
    $alla[$fil] = [
        'status' => $status,
        'note' => trim((string) ($in['note'] ?? '')),
        'satt' => date('c'),
    ];
}

ftruncate($fp, 0);
rewind($fp);
fwrite($fp, json_encode($alla, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
fflush($fp);
flock($fp, LOCK_UN);
fclose($fp);

echo json_encode(['ok' => true, 'status' => $status, 'antal' => count($alla)], JSON_UNESCAPED_UNICODE);
