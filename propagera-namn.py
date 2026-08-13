"""Propagera fattade rättelser: hitta ORÄTTADE förekomster av samma fel (issue #7).

Anrop:
    venv/Scripts/python.exe propagera-namn.py --dry-run
    venv/Scripts/python.exe propagera-namn.py
    venv/Scripts/python.exe propagera-namn.py Kirk-.../zego-x.json --troskel=0.65

Detektorn i steg b bedömer varje ord ISOLERAT, inte dokumentet. Samma namn
förvanskas olika många gånger i samma fil och bara någon variant flaggas —
`Boltz,` gick igenom medan `Bolts` fångades. Men när en människa väl rättat
`Vertobius` till `Posobiec` är de återstående varianterna härledbara utan modell.

Skriptet vänder alltså riktningen: i stället för att leta fel i texten letar det
efter orättade förekomster av fel som REDAN är rättade. Deterministiskt, inga
API-anrop.

**Applicerar aldrig.** Varje träff blir en `pending`-flagga i sidecaren; Lars
avgör i GUI:t. Idempotent — flaggorna märks `note: "propagering"` och ett index
som redan har en flagga rörs inte.

Se CLAUDE.md steg b.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import korrigeringar as k

PROJECT_ROOT = Path(__file__).resolve().parent

# Uppmätt 2026-08-13 mot kirk-owens-posobiec (655 ord, 29 beslut) och 25 filer
# med beslut. Normaliserat Levenshtein vann över Jaro-Winkler, Metaphone, NYSIIS,
# Soundex och Match Rating (jellyfish). Se CLAUDE.md för tabellen.
TROSKEL = 0.62

# Ankare och kandidater måste vara minst så här långa efter normalisering. Korta
# strängar får hög likhet av en enda redigering: "vi" liknar allt.
MIN_LANGD = 4

# Vanliga svenska ord som ofta står först i en mening och därmed ser versala ut.
# Utan listan föreslås Den/Där/Han/Men så fort ett ankare råkar likna dem.
STOPPORD = {
    "den", "det", "han", "hon", "hen", "men", "och", "att", "som", "där", "här",
    "detta", "denna", "utan", "sedan", "efter", "före", "under", "över", "alla",
    "allt", "ingen", "inget", "vilket", "vilken", "därför", "eftersom", "sådan",
    "sådant", "säger", "sade", "varit", "blev", "kommer", "genom", "man",
    "mycket", "mera", "bara", "också", "hela", "stor", "stort", "dessa",
    "deras", "hans", "hennes", "fråga", "frågan", "kanske", "alltså", "precis",
    "själv", "själva", "något", "några",
}


# --------------------------------------------------------------------------- #
# Likhet
# --------------------------------------------------------------------------- #

def levenshtein(a: str, b: str) -> int:
    """Redigeringsavstånd. Egen implementation — projektet har inga
    fuzzy-beroenden, och detta är tolv rader."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    rad = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        ny = [i]
        for j, cb in enumerate(b, 1):
            ny.append(min(rad[j] + 1, ny[j - 1] + 1, rad[j - 1] + (ca != cb)))
        rad = ny
    return rad[-1]


def likhet(a: str, b: str) -> float:
    """1,0 = identiska. Normaliserat mot den längsta strängen, så ett fel i ett
    kort ord väger tyngre än ett fel i ett långt."""
    langst = max(len(a), len(b))
    return 1 - levenshtein(a, b) / langst if langst else 0.0


# --------------------------------------------------------------------------- #
# Underlag
# --------------------------------------------------------------------------- #

def valj_bas(json_path: Path, side: dict) -> Path | None:
    """Rundans orörda bas — samma val som applicera-corrections.py gör.

    Sidecarens ordindex refererar basens ordpositioner. Läser vi en annan fil
    pekar varje index fel, och förslagen skulle hamna på slumpmässiga ord."""
    if side.get("base_json"):
        kandidat = json_path.with_name(side["base_json"])
        return kandidat if kandidat.is_file() else None
    bak = json_path.with_name(f"{json_path.stem}-bak.json")
    if bak.is_file():
        return bak
    # Ingen backup: filen är inte applicerad än, alltså ÄR .json originalet.
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return None if data.get("corrections_applied_at") else json_path


def rattelselogg(side: dict) -> list[tuple[str, str]]:
    """(förvanskad, korrekt) ur alla fattade beslut.

    Ordflaggor kräver `reviewed` — en `replace` utan mänskligt påskrift är
    AI:ns förslag, inte ett beslut, och att propagera ur den vore att bygga
    gissning på gissning. Fraser har inget eget reviewed-krav: GUI:t sätter
    reviewed=true på varje fras det skriver, och en fras utan span är en
    otolkad stubbe från .txt-migreringen som inte går att använda."""
    par: list[tuple[str, str]] = []
    for f in side.get("flags", []):
        if (f.get("decision") == "replace" and f.get("reviewed")
                and (f.get("replacement") or "").strip()):
            par.append((f["heard"], f["replacement"]))
    for p in side.get("phrase_edits", []):
        if "span_start" in p and p.get("original") and p.get("replacement"):
            par.append((p["original"], p["replacement"]))
    return par


def ankare(par: list[tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Namnlika ankare: nyckel (grundform) -> (ankarordet, korrekt form).

    BARA namn. Rättelseloggen innehåller också engångshörfel på vardagsord
    ('vi'->'hon', 'men'->'med'), och de ankarna matchar halva texten: uppmätt
    gav de 140 förslag i en fil om 655 ord mot 11 när de filtrerades bort.
    Propagering handlar om namn — det är också vad issue #7 beskriver.

    Både den förvanskade och den korrekta formen blir ankare. En ny variant kan
    ligga nära endera: 'Kerps' liknar den förvanskade 'Kerr', medan 'Sobiek'
    ligger närmare det korrekta 'Posobiec' än den förvanskade 'Sobjects'.

    Varje ankare bär den korrekta form det ska föreslå — ETT ord. GUI:ts
    Förslag-knapp ersätter ett ord, så en flerordig gissning vore fel: 'Kerr'
    skulle bli 'att Charlie Kirk'. Ur en flerordig rättelse väljs därför det ord
    som ligger närmast ankarordet ('Charlie Kerrs' -> 'att Charlie Kirk' ger
    Kerrs -> Kirk). Finns inget tillräckligt nära ord lämnas gissningen tom, och
    skälet får bära hela rättelsen — samma grepp som granska-igen.py använder för
    bortfall, där en felaktig ersättning vore värre än ingen."""
    ut: dict[str, tuple[str, str]] = {}
    MIN_GISSNING = 0.40

    def basta_ord(token: str, ratt_ord: list[str]) -> str:
        if len(ratt_ord) == 1:
            return k.karna(ratt_ord[0])
        stam = k.grundform(k.normalisera_ord(token))
        bast, val = 0.0, ""
        for r in ratt_ord:
            poang = likhet(stam, k.grundform(k.normalisera_ord(r)))
            if poang > bast:
                bast, val = poang, k.karna(r)
        return val if bast >= MIN_GISSNING else ""

    def lagg(token: str, korrekt: str) -> None:
        nyckel = k.grundform(k.normalisera_ord(token))
        if (len(nyckel) >= MIN_LANGD and token[:1].isupper()
                and nyckel not in STOPPORD):
            ut.setdefault(nyckel, (k.karna(token), korrekt))

    for forv, ratt in par:
        forv_ord, ratt_ord = forv.split(), ratt.split()
        if not (any(t[:1].isupper() for t in forv_ord)
                or any(t[:1].isupper() for t in ratt_ord)):
            continue
        for t in forv_ord:
            lagg(t, basta_ord(t, ratt_ord))
        for t in ratt_ord:
            lagg(t, k.karna(t))     # en korrekt form föreslår sig själv
    return ut


def beslutade_index(side: dict) -> set[int]:
    """Ordpositioner som redan har ett beslut eller en flagga. Fraser täcker
    hela sitt span — frasen ÄR beslutet för varje ord i det."""
    tagna = {f["global_index"] for f in side.get("flags", [])
             if f.get("global_index") is not None}
    return tagna | k.fras_tackta(side)


def redan_ratt(par: list[tuple[str, str]], ordlista: list[str]) -> set[str]:
    """Grundformer som ÄR korrekta: allt som står som rättelse, plus ordlistan.
    Jämförelsen sker på grundform, inte exakt sträng — annars föreslås 'Trumps'
    därför att bara 'Trump' står som rättelse."""
    ratt = {k.grundform(k.normalisera_ord(t)) for _, r in par for t in r.split()}
    ratt |= {k.grundform(k.normalisera_ord(t)) for term in ordlista for t in term.split()}
    return ratt - {""}


def hms(sek) -> str:
    s = int(round(sek or 0))
    return f"{s // 60}:{s % 60:02d}"


def bojd_som(korrekt: str, kandidat: str) -> str:
    """Förslaget böjt som kandidaten: 'Kerps' -> 'Kirks', inte 'Kirk'.
    Bara genitiv-/pluralt s, som är den böjning som faktiskt förekommer på namn."""
    kn = k.normalisera_ord(kandidat)
    if kn != k.grundform(kn) and kn.endswith("s") and not korrekt.endswith("s"):
        return korrekt + "s"
    return korrekt


# --------------------------------------------------------------------------- #
# Propagering
# --------------------------------------------------------------------------- #

def propagera(json_path: Path, sidecar_path: Path, side: dict,
              troskel: float, cfg: dict) -> tuple[list[dict], int, int]:
    """(nya flaggor, antal ankare, antal prövade kandidater)."""
    bas = valj_bas(json_path, side)
    if bas is None:
        raise ValueError(f"hittar ingen orörd bas för {json_path.name} "
                         "(applicerad men -bak.json saknas)")
    words = k.flatten_words(json.loads(bas.read_text(encoding="utf-8"))["segments"])

    par = rattelselogg(side)
    ankaren = ankare(par)
    if not ankaren:
        return [], 0, 0

    tagna = beslutade_index(side)
    ratt = redan_ratt(par, k.load_ordlista(k.temamapp_for(cfg, json_path)))

    nya: list[dict] = []
    provade = 0
    for i, w in enumerate(words):
        if i in tagna:
            continue
        # probability 1.0 = människobekräftat i en tidigare runda (apply sätter
        # det). Slår bara till när basen är -bak2.json; i ett Whisper-original
        # finns inga sådana ord.
        if w.get("probability") == 1.0:
            continue
        ord_ = w.get("word", "").strip()
        if not ord_[:1].isupper():
            continue
        norm = k.normalisera_ord(ord_)
        if len(norm) < MIN_LANGD or norm in STOPPORD:
            continue
        stam = k.grundform(norm)
        if stam in STOPPORD or stam in ratt:
            continue

        provade += 1
        bast = 0.0
        traff = None
        for nyckel, (ankarord, korrekt) in ankaren.items():
            poang = likhet(stam, nyckel)
            if poang > bast:
                bast, traff = poang, (ankarord, korrekt)
        if traff is None or bast < troskel:
            continue

        ankarord, korrekt = traff
        gissning = bojd_som(korrekt, ord_) if korrekt else ""
        ratt_text = korrekt or "(se beslutet — flerordig rättelse)"
        skal = (f"propagering: '{k.karna(ord_)}' liknar '{ankarord}' som rättades "
                f"till '{ratt_text}' @{hms(w.get('start'))}")
        nya.append({
            "anchor": round(w["start"], 2) if w.get("start") is not None else None,
            "global_index": i,
            "heard": ord_,
            "verify_ok": True,
            "ai_guess": gissning,
            "ai_reason": skal,
            "decision": "pending",
            "replacement": "",
            "note": "propagering",
            "line": None,
            "_poang": round(bast, 2),
        })
    return nya, len(ankaren), provade


def main() -> int:
    cfg = k.load_config()
    root = Path(cfg["data"]["root"])

    dry_run = "--dry-run" in sys.argv
    troskel = TROSKEL
    explicit = None
    for a in sys.argv[1:]:
        if a.startswith("--troskel="):
            troskel = float(a.split("=", 1)[1])
        elif a.startswith("--"):
            continue
        else:
            explicit = a

    if explicit:
        json_path = Path(explicit)
        if not json_path.is_absolute():
            json_path = root / explicit
        varifran = "kommandoraden"
    else:
        json_path, varifran = k.aktuell_json(cfg)
    if not json_path.is_file():
        print(f"FEL: JSON saknas: {json_path}", file=sys.stderr)
        return 1

    sidecar_path, runda, var = k.valj_sidecar(json_path)
    if sidecar_path is None:
        print(f"FEL: ingen sidecar för {json_path.name} — kör steg b först.",
              file=sys.stderr)
        return 1
    side = json.loads(sidecar_path.read_text(encoding="utf-8"))

    try:
        nya, n_ankare, n_provade = propagera(json_path, sidecar_path, side, troskel, cfg)
    except ValueError as e:
        print(f"FEL: {e}", file=sys.stderr)
        return 1

    print(f"Fil:      {json_path.name}   <- {varifran}")
    print(f"Sidecar:  {sidecar_path.name} (runda {runda}, {var})")
    print(f"Ankare:   {n_ankare} namnlika former ur {len(rattelselogg(side))} beslut")
    print(f"Prövade:  {n_provade} obeslutade ord, tröskel {troskel:.2f}")
    print()

    if not nya:
        print("Inga nya förslag.")
        return 0

    for f in sorted(nya, key=lambda x: x["global_index"]):
        print(f"  ord {f['global_index']:<6} {f['heard']:<18} -> {f['ai_guess']:<18} "
              f"({f['_poang']:.2f})")
        print(f"      {f['ai_reason']}")

    print()
    if dry_run:
        print(f"--dry-run: {len(nya)} förslag, inget skrivet.")
        return 0

    for f in nya:
        del f["_poang"]
    flaggor = side.get("flags", []) + nya
    flaggor.sort(key=lambda f: (f.get("global_index") is None,
                                f.get("global_index") or 0))
    side["flags"] = flaggor
    sidecar_path.write_text(json.dumps(side, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"Skrev {len(nya)} nya flaggor (pending) i {sidecar_path.name}.")
    print("Granska dem i GUI:t — inget är applicerat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
