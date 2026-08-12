"""Steg 2: applicera granskningsbesluten på transkript-JSON:en.

Läser arbetskopian i granska/state/ (fallback: /data-fröet) och skriver den
korrigerade datan tillbaka i <stem>.json. Rundmedveten: finns en runda 2-sidecar
(<stem>-corrections-2.json, från granska-igen.py) används den, och källan blir
sidecarens base_json (<stem>-bak2.json) i stället för -bak.json. Originalet
säkerhetskopieras EN gång till <stem>-bak.json (rörs aldrig om den redan finns,
så sanna originalet består). Regenererar också .srt och .txt.

Beslut (per ord, via global_index):
    replace  -> ordet byts, probability = 1.0
    accept   -> ordet bekräftat rätt, probability = 1.0
    delete   -> ordet tas bort
    osaker   -> lämnas orört (vi vet inte rättelsen)
    pending  -> lämnas orört (ej granskat)
Fras (span_start..span_end) ersätts som enhet med tider interpolerade över spanet.
Infogning (after_index) lägger in ett nytt ord med tid mellan grannarna.

Ordantal får alltså ändras. Idempotent: kör om utan skada (-bak.json orörd).

Filen är både skript och bibliotek: `applicera_en()` gör EN fil och används av
batch-applicera.py, medan main() väljer sin enda fil ur GUI:ts val och skriver
rapporten. Se CLAUDE.md.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import korrigeringar as k

PROJECT_ROOT = Path(__file__).resolve().parent


class ApplyFel(Exception):
    """Vägran: sidecaren eller källan går inte att applicera säkert.

    `atgard` är den handgripliga raden skriptet redan skriver ut idag. Ett
    undantag (och inte en statuskod) därför att varje vägran har sin egen text:
    enfilsvägen skriver den till stderr och avslutar, batchen skriver den och
    går vidare till nästa fil."""

    def __init__(self, meddelande: str, *, atgard: str = "") -> None:
        super().__init__(meddelande)
        self.atgard = atgard


@dataclass(slots=True)
class Utfall:
    """Vad en apply gjorde. Enfilsvägen skriver ut den i sin helhet; batchen
    plockar ut det som får plats på en rad och summerar resten."""
    json_path: Path
    sidecar_path: Path
    sidecar_var: str
    runda: int
    kalla: Path
    bak_meddelande: str
    ord_fore: int
    ord_efter: int
    segment_fore: int
    segment_efter: int
    tillampat: Counter
    olosta: int = 0
    otolkade: int = 0
    skrivna: list[Path] = field(default_factory=list)
    torrkorning: bool = False


def flatten(segments: list[dict]) -> list[dict]:
    """Alla ord i ordning, var och en med sitt segmentindex och säkra tider."""
    flat: list[dict] = []
    for si, seg in enumerate(segments):
        seg_start, seg_end = seg.get("start"), seg.get("end")
        for w in seg.get("words") or []:
            start = w.get("start")
            end = w.get("end")
            flat.append({
                "seg": si,
                "word": w.get("word", ""),
                "start": start if start is not None else seg_start,
                "end": end if end is not None else (start if start is not None else seg_end),
                "probability": w.get("probability"),
            })
    return flat


def tokens_over(text: str, t0: float, t1: float, seg: int) -> list[dict]:
    """Dela text i ord och fördela [t0, t1] jämnt över dem. probability=1.0."""
    parts = text.split()
    if not parts:
        return []
    if t1 is None or t0 is None or t1 <= t0:
        return [{"seg": seg, "word": " " + p, "start": t0, "end": t0, "probability": 1.0}
                for p in parts]
    dt = (t1 - t0) / len(parts)
    out = []
    for j, p in enumerate(parts):
        out.append({"seg": seg, "word": " " + p,
                    "start": t0 + j * dt, "end": t0 + (j + 1) * dt, "probability": 1.0})
    return out


def _kontrollera_index(side: dict, n: int) -> None:
    """Alla ordindex måste ligga inom källan.

    Ordantalskontrollen nedan bygger på sidecarens `word_count`, men det fältet
    SAKNAS i sidecars som granska/index.php skapat tomma (filer som aldrig
    AI-flaggats och som Lars rättat genom att klicka ord). För dem är detta den
    enda vakten. Utan den blir ett span_end utanför listan ett IndexError mitt i
    skrivningen, och ett after_index utanför listan tappas tyst."""
    fel = []
    for f in side.get("flags", []):
        gi = f.get("global_index")
        if gi is not None and not 0 <= gi < n:
            fel.append(f"flagga global_index={gi}")
    for p in side.get("phrase_edits", []):
        for nyckel in ("span_start", "span_end"):
            v = p.get(nyckel)
            if v is None or not 0 <= v < n:
                fel.append(f"fras {nyckel}={v}")
    for x in side.get("insertions", []):
        v = int(x.get("after_index", -2))
        if not -1 <= v < n:
            fel.append(f"infogning after_index={v}")
    if fel:
        raise ApplyFel(
            f"sidecaren pekar utanför källans {n} ord: " + ", ".join(fel[:5])
            + (f" (och {len(fel) - 5} till)" if len(fel) > 5 else ""),
            atgard="Åtgärd: transkriptionen har bytts under sidecaren. Ta bort "
                   "sidecaren och flagga om filen.")


def _valj_kalla(json_path: Path, side: dict, runda: int, data_applicerad) -> tuple[Path, Path]:
    """(källa, bak) — rundans ORÖRDA bas, som besluten alltid tillämpas mot.

    Sidecarens global_index/span/after_index refererar basens ordpositioner, så
    apply MÅSTE läsa den. Det är också det som gör körningen idempotent."""
    bak = json_path.with_name(f"{json_path.stem}-bak.json")
    if side.get("base_json"):
        kalla = json_path.with_name(side["base_json"])
        if not kalla.is_file():
            raise ApplyFel(f"sidecarens base_json saknas: {kalla.name}")
        return kalla, bak
    if bak.exists():
        return bak, bak
    # Ingen -bak.json. Normalt betyder det första körningen, och då ÄR .json
    # originalet. Men är filen redan applicerad har originalet försvunnit, och
    # då skulle vi läsa den redan rättade texten som bas: replace-besluten vore
    # ofarliga, men varje delete och infogning förskjuter indexen och skriver på
    # FEL ord. Tyst. Hellre stopp.
    if data_applicerad:
        raise ApplyFel(
            f"{bak.name} saknas, men {json_path.name} är redan applicerad "
            f"({data_applicerad}) — originalet är borta",
            atgard="Åtgärd: transkribera om filen, eller ta bort sidecaren om "
                   "besluten redan står i texten.")
    return json_path, bak


def _skriv_atomiskt(path: Path, text: str) -> None:
    """Skriv via tempfil + byte. Sanningskällan får aldrig lämnas halvskriven —
    filerna ligger i Dropbox, och en batch skriver tio i rad."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def applicera_en(json_path: Path, *, sidecar_path: Path | None = None,
                 torrkorning: bool = False) -> Utfall:
    """Applicera EN fils granskningsbeslut. Skriver ingenting när torrkorning är
    satt — allt räknas ändå, och alla vakter smäller, så en torrkörning ger de
    riktiga siffrorna och inte en uppskattning.

    Kastar ApplyFel vid vägran. Andra fel (OSError m.m.) bubblar upp."""
    if not json_path.is_file():
        raise ApplyFel(f"JSON saknas: {json_path}")

    if sidecar_path is None:
        sidecar_path, runda, sidecar_var = k.valj_sidecar(json_path)
        if sidecar_path is None:
            raise ApplyFel(
                f"ingen sidecar för {json_path.name} (varken -corrections-2.json "
                f"eller -corrections.json i granska/state/ eller {json_path.parent})")
    else:
        runda = 2 if sidecar_path.name.endswith("-corrections-2.json") else 1
        sidecar_var = ("arbetskopia i granska/state/"
                       if sidecar_path.parent.name == "state" else "frö i datamappen")

    side = json.loads(sidecar_path.read_text(encoding="utf-8"))
    data_json = json.loads(json_path.read_text(encoding="utf-8"))

    kalla, bak = _valj_kalla(json_path, side, runda,
                             data_json.get("corrections_applied_at"))
    data = json.loads(kalla.read_text(encoding="utf-8")) if kalla != json_path else data_json
    segments = data.get("segments", [])
    flat = flatten(segments)
    n = len(flat)
    if not n:
        raise ApplyFel(f"{kalla.name} saknar ord.")

    # Stämmer inte ordantalet har källan bytts under sidecaren — t.ex. efter en
    # omtranskribering — och besluten skulle skrivas in på FEL ord, tyst.
    vantat = side.get("word_count")
    if vantat is not None and vantat != n:
        raise ApplyFel(
            f"sidecaren gjordes mot {vantat} ord, men {kalla.name} har {n} — "
            "transkriptionen har ändrats sedan flaggningen, ordindexen pekar fel",
            atgard=f"Åtgärd: ta bort {sidecar_path.name} (och ev. -corrections.txt) "
                   "och flagga om filen.")
    _kontrollera_index(side, n)

    # Operationer ur sidecaren.
    alla_flaggor = side.get("flags", [])
    flags = {f["global_index"]: f for f in alla_flaggor if f.get("global_index") is not None}
    olosta = len(alla_flaggor) - len(flags)
    phrases = [p for p in side.get("phrase_edits", []) if "span_start" in p and "span_end" in p]
    inserts = [x for x in side.get("insertions", []) if "after_index" in x]

    phrase_by_start = {p["span_start"]: p for p in phrases}
    covered = set()
    for p in phrases:
        covered.update(range(p["span_start"], p["span_end"] + 1))

    ins_by_after: dict[int, list[str]] = {}
    for x in inserts:
        ins_by_after.setdefault(int(x["after_index"]), []).append(x["word"])

    applied = Counter()
    new_flat: list[dict] = []

    def emit_insert_after(gi, word):
        t0 = flat[gi]["end"]
        t1 = flat[gi + 1]["start"] if gi + 1 < n else flat[gi]["end"]
        end = t1 if (t1 is not None and t0 is not None and t1 > t0) else t0
        new_flat.append({"seg": flat[gi]["seg"], "word": " " + word.strip(),
                         "start": t0, "end": end, "probability": 1.0})
        applied["infogning"] += 1

    # Infogningar före första ordet.
    for word in ins_by_after.get(-1, []):
        first = flat[0]
        new_flat.append({"seg": first["seg"], "word": " " + word.strip(),
                         "start": 0.0, "end": first["start"] or 0.0, "probability": 1.0})
        applied["infogning"] += 1

    for gi in range(n):
        if gi in phrase_by_start:
            p = phrase_by_start[gi]
            se = p["span_end"]
            toks = tokens_over(p.get("replacement", ""), flat[gi]["start"], flat[se]["end"],
                               flat[gi]["seg"])
            new_flat.extend(toks)
            applied["fras"] += 1

        if gi not in covered:
            f = flags.get(gi)
            w = dict(flat[gi])
            if f:
                dec = f.get("decision")
                if dec == "delete":
                    applied["delete"] += 1
                elif dec == "replace" and (f.get("replacement") or "").strip():
                    w["word"] = " " + f["replacement"].strip()
                    w["probability"] = 1.0
                    applied["replace"] += 1
                    new_flat.append(w)
                elif dec in ("accept", "replace"):   # replace utan text = bekräftat
                    w["probability"] = 1.0
                    applied["accept"] += 1
                    new_flat.append(w)
                else:                                 # osaker / pending -> orört
                    applied[dec or "pending"] += 1
                    new_flat.append(w)
            else:
                new_flat.append(w)

        for word in ins_by_after.get(gi, []):
            emit_insert_after(gi, word)

    # Återbygg segmenten ur new_flat, gruppera på seg-index i ordning.
    by_seg: dict[int, list[dict]] = {}
    for w in new_flat:
        by_seg.setdefault(w["seg"], []).append(w)

    new_segments = []
    for si, seg in enumerate(segments):
        ws = by_seg.get(si)
        if not ws:
            continue  # alla ord raderade -> segmentet försvinner
        seg = dict(seg)
        seg["words"] = [{"start": w["start"], "end": w["end"],
                         "word": w["word"], "probability": w["probability"]} for w in ws]
        seg["text"] = "".join(w["word"] for w in ws)
        seg["start"] = ws[0]["start"]
        seg["end"] = ws[-1]["end"]
        new_segments.append(seg)

    if bak.exists():
        bak_meddelande = f"{bak.name} orörd (original)"
    else:
        bak_meddelande = ("skulle skapa " if torrkorning else "skapade ") + bak.name

    utfall = Utfall(
        json_path=json_path, sidecar_path=sidecar_path, sidecar_var=sidecar_var,
        runda=runda, kalla=kalla, bak_meddelande=bak_meddelande,
        ord_fore=n, ord_efter=len(new_flat),
        segment_fore=len(segments), segment_efter=len(new_segments),
        tillampat=applied, olosta=olosta, otolkade=len(side.get("review", [])),
        torrkorning=torrkorning,
    )
    if torrkorning:
        return utfall

    # Säkerhetskopiera originalet EN gång (json_path är fortfarande originalet
    # här om -bak.json saknades), skriv sedan över .json. I runda 2+ är basen
    # redan skriven av granska-igen.py — -bak.json rörs inte.
    if not bak.exists():
        shutil.copy2(json_path, bak)

    data["segments"] = new_segments
    data["corrections_applied_at"] = datetime.now().isoformat(timespec="seconds")
    _skriv_atomiskt(json_path, json.dumps(data, ensure_ascii=False, indent=2))

    srt_path = json_path.with_suffix(".srt")
    txt_path = json_path.with_suffix(".txt")
    k.write_srt(new_segments, srt_path)
    k.write_txt(new_segments, txt_path)
    utfall.skrivna = [json_path, srt_path, txt_path]
    return utfall


def skriv_rapport(u: Utfall, vald_via: str) -> None:
    left = u.tillampat.get("osaker", 0) + u.tillampat.get("pending", 0)
    print(f"Fil:     {u.json_path.name}   <- {vald_via}")
    print(f"Sidecar: {u.sidecar_path}  (runda {u.runda})")
    print(f"Källa:   {u.kalla.name}")
    print(f"Backup:  {u.bak_meddelande}")
    print(f"Ord:     {u.ord_fore} -> {u.ord_efter}")
    print(f"Tillämpat: replace {u.tillampat['replace']}, accept {u.tillampat['accept']}, "
          f"delete {u.tillampat['delete']}, fras {u.tillampat['fras']}, "
          f"infogning {u.tillampat['infogning']}")
    print(f"Lämnat orört: osäkra {u.tillampat.get('osaker', 0)}, "
          f"pending {u.tillampat.get('pending', 0)}"
          f"  (=  {left} ord utan probability=1.0)")
    if u.olosta or u.otolkade:
        print(f"Ej tillämpat: {u.olosta} flagga(or) utan ordindex, "
              f"{u.otolkade} otolkad(e) rad(er) i sidecaren")
    print(f"Segment: {u.segment_fore} -> {u.segment_efter}")
    print("Skrev:   " + ", ".join(p.name for p in u.skrivna))


def main() -> int:
    cfg = k.load_config()
    json_path, vald_via = k.aktuell_json(cfg)

    # Märkningen är policy, inte en vakt i biblioteket: enfilsvägen varnar och
    # kör vidare (du har valt filen med flit), batchen hoppar över.
    st = k.status_for(cfg, json_path)
    if st and st.get("status") == "ny-transkription":
        print(f"VARNING: {json_path.name} är märkt 'ny transkription behövs'"
              + (f" ({st['note']})" if st.get("note") else ""), file=sys.stderr)
        print("         Besluten bygger på en transkription du underkänt.",
              file=sys.stderr)

    try:
        u = applicera_en(json_path)
    except ApplyFel as e:
        print(f"FEL: {e}", file=sys.stderr)
        if e.atgard:
            print(e.atgard, file=sys.stderr)
        return 1

    skriv_rapport(u, vald_via)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
