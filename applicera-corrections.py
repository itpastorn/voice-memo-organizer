"""Steg 2: applicera granskningsbesluten på transkript-JSON:en.

Läser arbetskopian i granska/state/ (fallback: /data-fröet) och skriver den
korrigerade datan tillbaka i <stem>.json. Rundmedveten: finns en runda 2-sidecar
(<stem>-corrections-2.json, från granska-igen.py) används den, och källan blir
sidecarens base_json (<stem>-bak2.json) i stället för .bak.json. Originalet
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

Ordantal får alltså ändras. Idempotent: kör om utan skada (.bak orörd).
Se CLAUDE.md.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import korrigeringar as k

PROJECT_ROOT = Path(__file__).resolve().parent


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


def valj_transkript(cfg: dict) -> tuple[Path, str]:
    """Vilken fil ska appliceras? granska/current.json först — det är den fil GUI:t
    faktiskt visar, och sekvensen är nästan alltid granska -> applicera. Utan detta
    blir filväljaren en fälla: man granskar fil X och applicerar fil Y.

    Faller tillbaka på data.test_file i config.toml (som före väljaren fanns).
    Returnerar (sökväg, varifrån valet kom) — källan skrivs ut i rapporten så att
    ett felaktigt val syns direkt.
    """
    cur_file = PROJECT_ROOT / "granska" / "current.json"
    if cur_file.is_file():
        try:
            cur = json.loads(cur_file.read_text(encoding="utf-8"))
            rel = cur.get("transcript_json")
            if rel:
                # Runda 2 pekar current.json på basen (-bak2.json); apply ska ändå
                # skriva den levande .json:en. Sidecarens base_json styr källan.
                p = Path(cfg["data"]["root"]) / rel
                for slut in ("-bak2.json", "-bak.json"):
                    if p.name.endswith(slut):
                        p = p.with_name(p.name[: -len(slut)] + ".json")
                        break
                if p.is_file():
                    return p, "granska/current.json (GUI:ts val)"
        except (json.JSONDecodeError, OSError):
            pass
    return k.json_path_for(cfg), "config.toml (data.test_file)"


def main() -> int:
    cfg = k.load_config()
    json_path, vald_via = valj_transkript(cfg)
    if not json_path.is_file():
        print(f"FEL: JSON saknas: {json_path}", file=sys.stderr)
        return 1

    # Sidecarval: senaste rundan vinner. Runda 2 (-corrections-2, från
    # granska-igen.py) om den finns, annars runda 1. Arbetskopian i granska/state/
    # föredras framför fröet i datamappen, som tidigare.
    state_dir = PROJECT_ROOT / "granska" / "state"
    sidecar_path = None
    for namn in (f"{json_path.stem}-corrections-2.json",
                 f"{json_path.stem}-corrections.json"):
        for kandidat in (state_dir / namn, json_path.with_name(namn)):
            if kandidat.is_file():
                sidecar_path = kandidat
                break
        if sidecar_path:
            break
    if sidecar_path is None:
        print(f"FEL: ingen sidecar (varken -corrections-2.json eller "
              f"-corrections.json i {state_dir} eller {json_path.parent})", file=sys.stderr)
        return 1

    side = json.loads(sidecar_path.read_text(encoding="utf-8"))
    runda = int(side.get("runda", 1))

    # Källan är alltid rundans ORÖRDA bas — sidecarens global_index/span/after_index
    # refererar basens ordpositioner, så apply MÅSTE tillämpas mot den. Det gör
    # körningen idempotent och korrekt även efter fler GUI-ändringar.
    #   Runda 1: -bak.json om den finns (annars .json vid första körningen).
    #   Runda 2+: sidecarens base_json (t.ex. -bak2.json, skriven av granska-igen.py).
    bak = json_path.with_name(f"{json_path.stem}-bak.json")
    if side.get("base_json"):
        source = json_path.with_name(side["base_json"])
        if not source.is_file():
            print(f"FEL: sidecarens base_json saknas: {source}", file=sys.stderr)
            return 1
    else:
        source = bak if bak.exists() else json_path
    data = json.loads(source.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    flat = flatten(segments)
    n = len(flat)
    if not n:
        print("FEL: JSON saknar ord.", file=sys.stderr)
        return 1

    # Operationer ur sidecaren.
    flags = {f["global_index"]: f for f in side.get("flags", []) if f.get("global_index") is not None}
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

    def emit_insert_after(new_flat, gi, word):
        t0 = flat[gi]["end"]
        t1 = flat[gi + 1]["start"] if gi + 1 < n else flat[gi]["end"]
        end = t1 if (t1 is not None and t0 is not None and t1 > t0) else t0
        new_flat.append({"seg": flat[gi]["seg"], "word": " " + word.strip(),
                         "start": t0, "end": end, "probability": 1.0})
        applied["infogning"] += 1

    new_flat: list[dict] = []

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
            toks = tokens_over(p.get("replacement", ""), flat[gi]["start"], flat[se]["end"], flat[gi]["seg"])
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
            emit_insert_after(new_flat, gi, word)

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

    # Säkerhetskopiera originalet EN gång (json_path är fortfarande originalet här
    # om .bak saknades), skriv sedan över .json. I runda 2+ är basen redan skriven
    # av granska-igen.py — .bak rörs inte.
    if not bak.exists():
        shutil.copy2(json_path, bak)
        bak_msg = f"skapade {bak.name}"
    else:
        bak_msg = f"{bak.name} orörd (original)"

    data["segments"] = new_segments
    data["corrections_applied_at"] = datetime.now().isoformat(timespec="seconds")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    srt_path = json_path.with_suffix(".srt")
    txt_path = json_path.with_suffix(".txt")
    k.write_srt(new_segments, srt_path)
    k.write_txt(new_segments, txt_path)

    # Rapport.
    left = applied.get("osaker", 0) + applied.get("pending", 0)
    print(f"Fil:     {json_path.name}   <- {vald_via}")
    print(f"Sidecar: {sidecar_path}  (runda {runda})")
    print(f"Källa:   {source.name}")
    print(f"Backup:  {bak_msg}")
    print(f"Ord:     {n} -> {len(new_flat)}")
    print(f"Tillämpat: replace {applied['replace']}, accept {applied['accept']}, "
          f"delete {applied['delete']}, fras {applied['fras']}, infogning {applied['infogning']}")
    print(f"Lämnat orört: osäkra {applied.get('osaker', 0)}, pending {applied.get('pending', 0)}"
          f"  (=  {left} ord utan probability=1.0)")
    print(f"Segment: {len(segments)} -> {len(new_segments)}")
    print(f"Skrev:   {json_path.name}, {srt_path.name}, {txt_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
