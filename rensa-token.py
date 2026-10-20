"""Stryk Whisper-specialtoken ur transkript som ingenting annat pekar på.

    rensa-token.py                  visa vad som skulle rensas (hela arkivet)
    rensa-token.py <fil.json> ...   bara de filerna
    rensa-token.py --kor            gör det

**`--dry-run` är standard**, som i `radera.py` och `synka-namn.py`: här skrivs
sanningskällan om, och skriptet ändrar bara med `--kor`.

Det här är steg 1 av issue #12. Bara transkript **utan härledda filer** rensas
— ingen sidecar, ingen `-bak.json`, ingen `.md`, ingen arbetskopia i
`granska/state/`, inte applicerade. Tokenorden ingår i `global_index`, och
allt som pekar på ordpositioner skulle annars hamna på fel ord. Steg 2, som
skriver om indexen i samma svep, är inte byggt.

Bara **rena** träffar stryks: orden innehåller ingenting utöver tokenen. En
oren träff sitter ihop med tal ('h<|nospeech|>') och lämnas åt en människa.
Ett segment som bara var token försvinner helt; ett segment med tal behåller
talet och sina tider.

Originalet sparas i `sakerhetskopior/tokenrensning/` (gitignorerad), inte som
`-bak.json` i datamappen: en `-bak.json` betyder "applicerad" för GUI:t och
apply, som då skulle granska mot den orensade texten. `.srt` och `.txt` skrivs
om ur den rensade JSON:en. Varje rensad fil loggas i `logs/arkivlogg.log`.

Se CLAUDE.md, steg a under "Tokenvakt".
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import korrigeringar as k

LOGGFIL = k.PROJECT_ROOT / "logs" / "arkivlogg.log"
KOPIOR = k.PROJECT_ROOT / "sakerhetskopior" / "tokenrensning"


class RensFel(Exception):
    """Rensningen gav inte det den skulle — filen lämnas orörd."""


def rensa(data: dict) -> tuple[dict, list[int], list[dict], int]:
    """Stryk de rena träffarna. Returnerar (ny data, borttagna globala ordindex,
    orena träffar som lämnades, antal borttagna segment).

    Indata ändras inte. Borttagna index returneras i ordning så att steg 2 kan
    räkna fram förskjutningen för varje index efter dem."""
    segs = data.get("segments") or []
    traffar = k.specialtoken_traffar(segs)
    bort: set[int] = set()
    token_per_seg: dict[int, list[str]] = {}
    orena: list[dict] = []
    for t in traffar:
        if t["ren"]:
            bort.update(t["ord"])
            token_per_seg.setdefault(t["segment"], []).append(t["token"])
        else:
            orena.append(t)

    nya: list[dict] = []
    tomma = 0
    gi = 0
    for si, seg in enumerate(segs):
        ord_ = seg.get("words") or []
        if si not in token_per_seg:
            nya.append(seg)
            gi += len(ord_)
            continue
        behall = [w for j, w in enumerate(ord_) if gi + j not in bort]
        gi += len(ord_)
        text = seg.get("text") or ""
        for tok in token_per_seg[si]:
            if tok not in text:
                raise RensFel(f"segment {si}: '{tok}' finns i orden men inte i texten")
            text = text.replace(tok, "", 1)
        text = re.sub(r"[ \t]{2,}", " ", text)
        if not behall:
            if text.strip():
                raise RensFel(f"segment {si}: orden är borta men texten har kvar "
                              f"{text.strip()!r}")
            tomma += 1
            continue
        nytt = dict(seg)
        nytt["words"] = behall
        nytt["text"] = text
        nya.append(nytt)

    ny = dict(data)
    ny["segments"] = nya
    return ny, sorted(bort), orena, tomma


def kontrollera(gammal: dict, ny: dict, bort: list[int], orena: list[dict]) -> None:
    """Rensningen tog bort exakt tokenorden och inget annat."""
    fore = k.flatten_words(gammal.get("segments") or [])
    efter = k.flatten_words(ny.get("segments") or [])
    if len(efter) != len(fore) - len(bort):
        raise RensFel(f"ordantal {len(fore)} -> {len(efter)}, väntat "
                      f"{len(fore) - len(bort)}")
    bortset = set(bort)
    vantade = [w for i, w in enumerate(fore) if i not in bortset]
    for a, b in zip(vantade, efter):
        if (a.get("word"), a.get("start"), a.get("end")) != \
                (b.get("word"), b.get("start"), b.get("end")):
            raise RensFel(f"ordet {a.get('word')!r} @{a.get('start')} ändrades")
    kvar = k.specialtoken_traffar(ny.get("segments") or [])
    if len(kvar) != len(orena) or any(t["ren"] for t in kvar):
        raise RensFel(f"{len(kvar)} träffar kvar efteråt, väntat {len(orena)} orena")
    if ny.get("duration") != gammal.get("duration"):
        raise RensFel("duration ändrades")


def skriv_atomiskt(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def logga(p: Path, root: Path, rad: dict) -> None:
    """En rad per rensad fil, i arkivets gemensamma logg över medvetna ändringar."""
    LOGGFIL.parent.mkdir(parents=True, exist_ok=True)
    delar = [datetime.now().isoformat(timespec="seconds"),
             "handelse=tokenrensning",
             f"stam={p.stem}",
             f"mapp={p.parent.relative_to(root).as_posix()}"]
    delar += [f"{n}={v}" for n, v in rad.items()]
    with LOGGFIL.open("a", encoding="utf-8") as f:
        f.write("  ".join(delar) + "\n")


def main() -> int:
    cfg = k.load_config()
    root = Path(cfg["data"]["root"])
    kor = "--kor" in sys.argv[1:]
    okanda = [a for a in sys.argv[1:] if a.startswith("--") and a != "--kor"]
    if okanda:
        print(f"FEL: okänt argument {okanda[0]}", file=sys.stderr)
        return 2

    explicit = [a for a in sys.argv[1:] if not a.startswith("--")]
    if explicit:
        filer = []
        for a in explicit:
            q = Path(a)
            q = q if q.is_absolute() else (root / a)
            if not q.is_file():
                print(f"FEL: saknas: {q}", file=sys.stderr)
                return 2
            filer.append(q)
    else:
        filer = sorted(k.iter_transkript(root))

    rensbara, hindrade, orena_tot, fel = [], [], [], []
    for p in filer:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            fel.append((p, f"går inte att läsa: {e}"))
            continue
        traffar = k.specialtoken_traffar(data.get("segments") or [])
        if not traffar:
            continue
        hinder = k.tokenrensning_hinder(p, data)
        if hinder:
            hindrade.append((p, len(traffar), hinder))
            continue
        try:
            ny, bort, orena, tomma = rensa(data)
            kontrollera(data, ny, bort, orena)
        except RensFel as e:
            fel.append((p, str(e)))
            continue
        orena_tot += [(p, t) for t in orena]
        if bort:
            rensbara.append((p, data, ny, bort, tomma, len(orena)))

    rel = lambda p: p.relative_to(root).as_posix() if p.is_relative_to(root) else str(p)

    print(f"Datamapp: {root}")
    print()
    if rensbara:
        print(f"RENSAS ({len(rensbara)} filer):")
        for p, _, ny, bort, tomma, n_orena in rensbara:
            extra = f", {n_orena} oren lämnas" if n_orena else ""
            print(f"  {rel(p)}: {len(bort)} tokenord, {tomma} tomma segment bort{extra}")
    else:
        print("Inget att rensa bland filerna utan härledda filer.")
    if hindrade:
        print()
        print(f"HOPPAS ÖVER — härledda filer pekar på ordindexen ({len(hindrade)} filer, "
              f"{sum(n for _, n, _ in hindrade)} träffar). Kräver steg 2 (issue #12):")
        for p, n, hinder in hindrade:
            print(f"  {rel(p)}: {n} träff — {', '.join(hinder[:3])}"
                  f"{' …' if len(hinder) > 3 else ''}")
    if orena_tot:
        print()
        print("ORENA — sitter ihop med tal, för hand:")
        for p, t in orena_tot:
            print(f"  {rel(p)} @{t['start']:.1f}s: {t['token']}")
    if fel:
        print()
        print("FEL — lämnade orörda:")
        for p, msg in fel:
            print(f"  {rel(p)}: {msg}")

    if not kor or not rensbara:
        print()
        if rensbara:
            print("--dry-run (standard). Kör med --kor för att rensa.")
        return 1 if fel else 0

    for p, data, ny, bort, tomma, n_orena in rensbara:
        kopia = KOPIOR / p.relative_to(root)
        kopia.parent.mkdir(parents=True, exist_ok=True)
        if not kopia.exists():          # första originalet är det som räknas
            kopia.write_bytes(p.read_bytes())
        ny["tokenrensning"] = {
            "datum": datetime.now().isoformat(timespec="seconds"),
            "borttagna_ord": len(bort),
            "borttagna_segment": tomma,
            "orena_kvar": n_orena,
        }
        skriv_atomiskt(p, json.dumps(ny, ensure_ascii=False, indent=2))
        k.write_srt(ny["segments"], p.with_suffix(".srt"))
        k.write_txt(ny["segments"], p.with_suffix(".txt"))
        logga(p, root, {"ord": len(bort), "segment": tomma, "orena_kvar": n_orena})
    print()
    print(f"Klart: {len(rensbara)} filer rensade. Original i {KOPIOR}, "
          f"loggat i {LOGGFIL}.")
    return 1 if fel else 0


if __name__ == "__main__":
    raise SystemExit(main())
