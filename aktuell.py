"""Visar vilken fil pipelinen arbetar med — utan att röra något.

Steg 4 och 5 (applicera-corrections.py, forbattra.py, negationsvakt.py) följer
alla `granska/current.json`, alltså GUI:ts filval. Men valet syns bara i GUI:t,
och rapporten kommer först EFTER att skriptet redan skrivit. Står man i
terminalen med Docker nedsläckt är det annars `cat granska/current.json` — som
ger ett filnamn men inte om filen är granskad, applicerad eller ens flaggad.

Skriver ingenting. Kör den före steg 4 när du är osäker.

    python aktuell.py

Se CLAUDE.md steg b.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import korrigeringar as k

PROJECT_ROOT = Path(__file__).resolve().parent


def las(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def status_markning(cfg: dict, json_path: Path) -> str | None:
    """"Ny transkription behövs"-märkningen ur granska/status.json."""
    v = k.status_for(cfg, json_path)
    if not v or v.get("status") != "ny-transkription":
        return None
    return "ny transkription behövs" + (f" — {v['note']}" if v.get("note") else "")


def main() -> int:
    cfg = k.load_config()
    json_path, vald_via = k.aktuell_json(cfg)

    tema = k.temamapp_for(cfg, json_path) or "(inkorgen — ingen temamapp)"
    print(f"Fil:      {json_path.name}")
    print(f"Mapp:     {tema}")
    print(f"Vald via: {vald_via}")
    print(f"Sökväg:   {json_path}")

    if not json_path.is_file():
        print("\nFEL: transkriptet saknas på disk.", file=sys.stderr)
        return 1

    data = las(json_path)
    ord_antal = sum(len(s.get("words") or []) for s in data.get("segments", []))
    langd = data.get("duration")
    print(f"Längd:    {langd / 60:.1f} min, {ord_antal} ord"
          if langd else f"Ord:      {ord_antal}")

    print()
    sidecar, runda, var = k.valj_sidecar(json_path)
    if sidecar is None:
        print("Flaggning: ingen sidecar — kör batch-flagga.py, eller öppna filen "
              "i väljaren och rätta för hand.")
    else:
        side = las(sidecar)
        flaggor = side.get("flags", [])
        kvar = k.antal_ogranskade(side)
        print(f"Sidecar:   {sidecar.name} (runda {runda}, {var})")
        print(f"Flaggor:   {len(flaggor)}, varav {kvar} ogranskade"
              + (f", {len(side.get('phrase_edits', []))} fraser" if side.get("phrase_edits") else "")
              + (f", {len(side.get('insertions', []))} infogningar" if side.get("insertions") else ""))
        vantat = side.get("word_count")
        if vantat is not None and vantat != ord_antal:
            print(f"           VARNING: sidecaren gjordes mot {vantat} ord — "
                  "apply kommer att vägra.")

    applicerad = data.get("corrections_applied_at")
    print(f"Applicerad: {applicerad}" if applicerad
          else "Applicerad: nej — besluten står bara i sidecaren.")

    md = json_path.with_suffix(".md")
    print(f"Steg c:    {md.name} finns" if md.is_file()
          else "Steg c:    ingen .md ännu")

    markning = status_markning(cfg, json_path)
    if markning:
        print(f"\nMÄRKT:     {markning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
