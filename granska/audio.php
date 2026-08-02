<?php
// Steg 1c — strömmar ljudfilen ur /data med HTTP Range-stöd. php -S serverar bara
// under docroot (/app), men ljudet ligger i /data (läsläge), och <audio> behöver
// Range (206 Partial Content) för att kunna söka och loopa. Därför denna endpoint.

$here = __DIR__;
$cur = json_decode(@file_get_contents($here . '/current.json'), true);
$dataDir = getenv('DATA_DIR') ?: '/data';
$file = $dataDir . '/' . ($cur['audio_file'] ?? '');

if (!$cur || !is_file($file)) { http_response_code(404); exit('ljudfil saknas'); }

// ?f=<stem> sätts av index.php och är dels cache-nyckel, dels en kontroll: en
// gammal flik (eller en cachad sida) kan annars begära ljud för en fil som inte
// längre är vald, och skulle då tyst få FEL memos ljud. Hellre ett fel än det.
if (isset($_GET['f']) && $_GET['f'] !== ($cur['stem'] ?? '')) {
    http_response_code(409);
    exit('annan fil är vald nu (' . htmlspecialchars($cur['stem'] ?? '—') . ') — ladda om sidan');
}

$size = filesize($file);
$ext = strtolower(pathinfo($file, PATHINFO_EXTENSION));
$mime = ['m4a' => 'audio/mp4', 'mp4' => 'audio/mp4', 'aac' => 'audio/aac', 'mp3' => 'audio/mpeg'][$ext]
        ?? 'application/octet-stream';

$start = 0;
$end = $size - 1;

header('Accept-Ranges: bytes');
header("Content-Type: $mime");
header('Cache-Control: public, max-age=3600');

if (isset($_SERVER['HTTP_RANGE']) && preg_match('/bytes=(\d*)-(\d*)/', $_SERVER['HTTP_RANGE'], $m)) {
    if ($m[1] === '' && $m[2] !== '') {                 // suffix: bytes=-N
        $start = max(0, $size - (int) $m[2]);
    } else {
        if ($m[1] !== '') $start = (int) $m[1];
        if ($m[2] !== '') $end = (int) $m[2];
    }
    if ($start > $end || $start >= $size) {
        http_response_code(416);
        header("Content-Range: bytes */$size");
        exit;
    }
    http_response_code(206);
    header("Content-Range: bytes $start-$end/$size");
}

header('Content-Length: ' . ($end - $start + 1));

while (ob_get_level()) ob_end_clean();   // ingen buffring — strömma direkt

$fp = fopen($file, 'rb');
fseek($fp, $start);
$remaining = $end - $start + 1;
while ($remaining > 0 && !feof($fp)) {
    $read = $remaining > 8192 ? 8192 : $remaining;
    echo fread($fp, $read);
    $remaining -= $read;
    flush();
}
fclose($fp);
