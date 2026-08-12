<?php
// Delade regler för GUI:t. Motsvarar korrigeringar.py på Python-sidan: räkningen
// måste ge samma svar i väljaren, i granskningsvyn och i batchen, annars säger
// listan "1 kvar" om en fil där det inte finns något kvar att göra.

/** Ordindex som ligger inom en frasersättning.
 *  Fraser utan span hoppas över — migrera-corrections.py lämnar stubbar för
 *  rader den inte kunde binda till ordindex, och apply filtrerar bort dem
 *  på samma villkor. Speglar korrigeringar.fras_tackta(). */
function fras_tackta(array $side): array {
    $tackta = [];
    foreach ($side['phrase_edits'] ?? [] as $p) {
        if (isset($p['span_start'], $p['span_end'])) {
            for ($i = $p['span_start']; $i <= $p['span_end']; $i++) $tackta[$i] = true;
        }
    }
    return $tackta;
}

/** Beslutsräkning per typ. Ord som täcks av en fras räknas som 'fras', inte som
 *  'pending': frasen ÄR beslutet, och apply hoppar över ordflaggan för varje
 *  index i ett frasspan. Utan detta ser en färdiggranskad fil ogranskad ut och
 *  hålls utanför batch-applicera.py i onödan. Speglar
 *  korrigeringar.antal_ogranskade(). */
function rakna_beslut(array $side): array {
    $tackta = fras_tackta($side);
    $c = ['replace' => 0, 'delete' => 0, 'accept' => 0, 'osaker' => 0,
          'pending' => 0, 'fras' => 0];
    foreach ($side['flags'] ?? [] as $f) {
        if (isset($tackta[$f['global_index'] ?? -1])) { $c['fras']++; continue; }
        $d = $f['decision'] ?? 'pending';
        if ($d === '') $d = 'pending';
        $c[$d] = ($c[$d] ?? 0) + 1;
    }
    return $c;
}
