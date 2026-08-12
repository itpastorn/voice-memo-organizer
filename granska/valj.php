<?php
// Filväljare — förteckning över alla transkriptioner i /data, senaste överst.
//
// Fanns inte förut: current.json skrevs bara av migrera-corrections.py, så att byta
// fil krävde ett Python-anrop. Med batchtranskribering (39 filer i en temamapp,
// 355 i arkivet) blir det ohållbart.
//
// Listar ALLA filer med .json, inte bara de som flagga-llm+migrera förberett.
// Filer utan sidecar öppnas med en tom sådan; typ 3 (klicka valfritt ord) räcker
// för att rätta dem. GUI:t blir därmed en komplett översikt över arkivet.
//
// Klick skriver current.json och skickar vidare till index.php.

require_once __DIR__ . '/gemensam.php';

$here = __DIR__;
$dataDir = getenv('DATA_DIR') ?: '/data';

// --------------------------------------------------------------------------- //
// Val: skriv current.json och gå till granskningen
// --------------------------------------------------------------------------- //

if (isset($_GET['valj'])) {
    $rel = $_GET['valj'];
    // Sökvägen kommer från vår egen lista, men den är ändå indata: neka allt som
    // försöker ta sig ur /data.
    if (str_contains($rel, '..') || str_starts_with($rel, '/') || !preg_match('#^[\w./\- åäöÅÄÖ]+\.json$#u', $rel)) {
        http_response_code(400);
        exit('ogiltig sökväg');
    }
    $abs = $dataDir . '/' . $rel;
    if (!is_file($abs)) { http_response_code(404); exit('transkript saknas: ' . htmlspecialchars($rel)); }

    $data = json_decode(file_get_contents($abs), true) ?: [];
    $dir = dirname($rel) === '.' ? '' : dirname($rel) . '/';
    $stem = pathinfo($rel, PATHINFO_FILENAME);

    // Sidecar: senaste rundan vinner (runda 2 före runda 1).
    $sidecar = null;
    foreach (["{$dir}{$stem}-corrections-2.json", "{$dir}{$stem}-corrections.json"] as $cand) {
        if (is_file($dataDir . '/' . $cand)) { $sidecar = $cand; break; }
    }
    if ($sidecar === null) $sidecar = "{$dir}{$stem}-corrections.json";  // skapas tom i index.php

    // Runda 2 granskas mot sin bas (-bak2.json), inte mot senaste .json.
    $transcript = $rel;
    if (str_ends_with($sidecar, '-corrections-2.json')) {
        $side = json_decode(@file_get_contents($dataDir . '/' . $sidecar), true) ?: [];
        if (!empty($side['base_json']) && is_file($dataDir . '/' . $dir . $side['base_json'])) {
            $transcript = $dir . $side['base_json'];
        }
    }

    file_put_contents($here . '/current.json', json_encode([
        'stem' => $stem,
        'transcript_json' => $transcript,
        'sidecar_json' => $sidecar,
        'audio_file' => $dir . ($data['audio_file'] ?? finn_ljud($dataDir, $dir, $stem)),
    ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));

    header('Location: index.php');
    exit;
}

// --------------------------------------------------------------------------- //
// Inventering
// --------------------------------------------------------------------------- //

/** Ljudfil bredvid JSON:en. Legacy-transkript saknar audio_file i JSON:en. */
function finn_ljud(string $dataDir, string $dir, string $stem): string {
    foreach (['m4a', 'mp3', 'aac', 'mp4'] as $ext) {
        if (is_file("$dataDir/$dir$stem.$ext")) return "$stem.$ext";
    }
    return "$stem.m4a";   // bästa gissning; index.php märker ut om den saknas
}

function ar_transkript(string $namn): bool {
    if (!str_ends_with($namn, '.json')) return false;
    // Sidecars och backupper är inte transkript. Både -bak.json (nuvarande
    // konvention) och .bak.json (legacy i transcripts/json/) måste bort.
    foreach (['-corrections', '-bak.json', '.bak.json', '-bak2.json', '.bak2.json'] as $m) {
        if (str_contains($namn, $m)) return false;
    }
    return true;
}

$UNDANTAG = ['test', 'sammanfatta'];
$rader = [];

$it = new RecursiveIteratorIterator(
    new RecursiveDirectoryIterator($dataDir, FilesystemIterator::SKIP_DOTS),
    RecursiveIteratorIterator::SELF_FIRST
);
foreach ($it as $fil) {
    if (!$fil->isFile() || !ar_transkript($fil->getFilename())) continue;
    $rel = str_replace('\\', '/', substr($fil->getPathname(), strlen($dataDir) + 1));
    $delar = explode('/', $rel);
    if (array_intersect(array_map('strtolower', $delar), $UNDANTAG)) continue;

    $data = json_decode(file_get_contents($fil->getPathname()), true);
    if (!is_array($data) || !isset($data['segments'])) continue;   // inte ett transkript

    $dir = count($delar) > 1 ? implode('/', array_slice($delar, 0, -1)) : '';
    $stem = pathinfo($rel, PATHINFO_FILENAME);
    $prefix = $dir === '' ? '' : $dir . '/';

    // Status. Ordningen speglar pipelinen: applicerad > granskas > flaggad > ingen.
    $runda = 1;
    $seed = null;
    foreach (["{$prefix}{$stem}-corrections-2.json" => 2, "{$prefix}{$stem}-corrections.json" => 1] as $cand => $r) {
        if (is_file("$dataDir/$cand")) { $seed = $cand; $runda = $r; break; }
    }
    $work = $seed !== null ? $here . '/state/' . basename($seed) : null;
    $flaggor = $kvar = null;
    if ($work !== null && is_file($work)) {
        $s = json_decode(file_get_contents($work), true) ?: [];
        $flaggor = count($s['flags'] ?? []);
        $kvar = rakna_beslut($s)['pending'];
    } elseif ($seed !== null) {
        $s = json_decode(file_get_contents("$dataDir/$seed"), true) ?: [];
        $flaggor = count($s['flags'] ?? []);
        $kvar = $flaggor;
    }

    $rader[] = [
        'rel' => $rel,
        'mapp' => $dir === '' ? '(inkorgen)' : $dir,
        'stem' => $stem,
        // Legacy-transkript saknar generated_at — filens ändringstid får duga.
        'tid' => $data['generated_at'] ?? date('c', $fil->getMTime()),
        'tid_gissad' => !isset($data['generated_at']),
        'langd' => $data['duration'] ?? null,
        'applicerad' => isset($data['corrections_applied_at']),
        'runda' => $runda,
        'flaggor' => $flaggor,
        'kvar' => $kvar,
        'md' => is_file("$dataDir/$prefix$stem.md"),
    ];
}

usort($rader, fn($a, $b) => strcmp($b['tid'], $a['tid']));   // senaste överst

$mappar = array_values(array_unique(array_map(fn($r) => $r['mapp'], $rader)));
sort($mappar);

$cur = json_decode(@file_get_contents($here . '/current.json'), true) ?: [];
$aktiv = $cur['transcript_json'] ?? '';
$statusAlla = json_decode(@file_get_contents($here . '/status.json'), true) ?: [];

function hms(?float $s): string {
    if ($s === null) return '—';
    $s = (int) round($s);
    return $s >= 3600
        ? sprintf('%d:%02d:%02d', intdiv($s, 3600), intdiv($s % 3600, 60), $s % 60)
        : sprintf('%d:%02d', intdiv($s, 60), $s % 60);
}
?>
<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Välj fil att granska</title>
<style>
  :root { --bg:#faf9f7; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --acc:#2563eb;
          --pending:#fde68a; --done:#bbf7d0; --none:#f5f5f4; }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.5 system-ui,sans-serif; color:var(--fg); background:var(--bg); }
  header { position:sticky; top:0; background:var(--bg); border-bottom:1px solid var(--line);
           padding:.9rem clamp(1rem,3vw,2rem); display:flex; gap:1rem; align-items:center; flex-wrap:wrap; }
  h1 { font-size:17px; margin:0; }
  #q { font:14px system-ui; padding:.4rem .6rem; border:1px solid var(--muted); border-radius:6px; min-width:15rem; }
  select { font:14px system-ui; padding:.4rem; border:1px solid var(--muted); border-radius:6px; }
  main { padding:1rem clamp(1rem,3vw,2rem) 3rem; }
  table { border-collapse:collapse; width:100%; max-width:70rem; }
  th { text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:.05em;
       color:var(--muted); padding:.4rem .6rem; border-bottom:1px solid var(--line); font-weight:600; }
  td { padding:.45rem .6rem; border-bottom:1px solid var(--line); }
  tr:hover td { background:#fff; }
  tr.aktiv td { background:#dbeafe; }
  a.fil { color:var(--acc); text-decoration:none; font-weight:600; }
  a.fil:hover { text-decoration:underline; }
  .mapp { color:var(--muted); font-size:13px; }
  .tid { color:var(--muted); font-size:13px; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .tid.gissad::after { content:" ~"; color:var(--muted); }
  .num { font-variant-numeric:tabular-nums; white-space:nowrap; }
  .chip { display:inline-block; border-radius:4px; padding:1px 7px; font-size:12px; font-weight:600; white-space:nowrap; }
  .chip.applicerad { background:var(--done); }
  .chip.kvar { background:var(--pending); }
  .chip.ingen { background:var(--none); color:var(--muted); }
  .chip.ny { background:#fecaca; color:#7f1d1d; }
  .r2 { display:inline-block; background:#e0e7ff; color:#3730a3; border-radius:4px;
        padding:1px 5px; font-size:11px; font-weight:600; margin-left:.3rem; }
  .md { color:var(--muted); font-size:12px; }
  footer { color:var(--muted); font-size:13px; padding:0 clamp(1rem,3vw,2rem) 2rem; max-width:70rem; }
</style>
</head>
<body>
<header>
  <h1>Välj fil att granska</h1>
  <input id="q" placeholder="filtrera på filnamn…" autocomplete="off">
  <select id="mapp">
    <option value="">alla temamappar</option>
    <?php foreach ($mappar as $m): ?><option><?= htmlspecialchars($m) ?></option><?php endforeach; ?>
  </select>
  <span class="mapp"><b id="antal"><?= count($rader) ?></b> av <?= count($rader) ?></span>
</header>

<main>
<table>
  <thead><tr>
    <th>Fil</th><th>Temamapp</th><th>Transkriberad</th><th>Längd</th><th>Status</th><th></th>
  </tr></thead>
  <tbody>
  <?php foreach ($rader as $r): ?>
    <tr class="<?= $r['rel'] === $aktiv ? 'aktiv' : '' ?>"
        data-namn="<?= htmlspecialchars(strtolower($r['stem'])) ?>"
        data-mapp="<?= htmlspecialchars($r['mapp']) ?>">
      <td><a class="fil" href="?valj=<?= rawurlencode($r['rel']) ?>"><?= htmlspecialchars($r['stem']) ?></a></td>
      <td class="mapp"><?= htmlspecialchars($r['mapp']) ?></td>
      <td class="tid <?= $r['tid_gissad'] ? 'gissad' : '' ?>"><?= htmlspecialchars(substr(str_replace('T', ' ', $r['tid']), 0, 16)) ?></td>
      <td class="num"><?= hms($r['langd']) ?></td>
      <td>
        <?php if (isset($statusAlla[$r['rel']])): ?>
          <span class="chip ny" title="<?= htmlspecialchars($statusAlla[$r['rel']]['note'] ?? '') ?>">⚠ ny transkription behövs</span>
        <?php elseif ($r['applicerad']): ?>
          <span class="chip applicerad">applicerad</span>
        <?php elseif ($r['flaggor'] === null): ?>
          <span class="chip ingen">ej flaggad</span>
        <?php elseif ($r['flaggor'] === 0): ?>
          <span class="chip ingen">inga fel funna</span>
        <?php elseif ($r['kvar'] > 0): ?>
          <span class="chip kvar"><?= $r['flaggor'] ?> flaggor, <?= $r['kvar'] ?> kvar</span>
        <?php else: ?>
          <span class="chip applicerad"><?= $r['flaggor'] ?> flaggor, granskade</span>
        <?php endif; ?>
        <?php if ($r['runda'] > 1): ?><span class="r2">runda <?= $r['runda'] ?></span><?php endif; ?>
      </td>
      <td class="md"><?= $r['md'] ? 'steg c ✓' : '' ?></td>
    </tr>
  <?php endforeach; ?>
  </tbody>
</table>
</main>

<footer>
  <p><b>~</b> efter tiden = filen saknar <code>generated_at</code> (äldre försök); filens
  ändringstid används i stället. <b>ej flaggad</b> går att öppna ändå — klicka valfritt
  ord i texten för att rätta det.</p>
  <p>Valet skrivs till <code>current.json</code>, och <code>applicera-corrections.py</code>
  följer det. Listan läses om vid varje sidladdning, så nya transkriberingar dyker upp
  av sig själva.</p>
</footer>

<script>
const q = document.getElementById('q'), mapp = document.getElementById('mapp'),
      antal = document.getElementById('antal'), rader = [...document.querySelectorAll('tbody tr')];
function filtrera() {
  const t = q.value.trim().toLowerCase(), m = mapp.value;
  let n = 0;
  for (const r of rader) {
    const visa = (!t || r.dataset.namn.includes(t)) && (!m || r.dataset.mapp === m);
    r.style.display = visa ? '' : 'none';
    if (visa) n++;
  }
  antal.textContent = n;
}
q.addEventListener('input', filtrera);
mapp.addEventListener('change', filtrera);
q.focus();
</script>
</body>
</html>
