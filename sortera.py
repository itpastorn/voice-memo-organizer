"""Föreslå vilken temamapp ett memo hör hemma i. Flyttar bara på godkännande.

    sortera.py                    förslag för allt i incoming/
    sortera.py --alla             visa även poängen bakom varje förslag
    sortera.py --mat              mät reglerna mot hela det sorterade arkivet
    sortera.py --flytta <stam>    genomför förslaget för EN fil
    sortera.py --flytta <stam> --till <mapp>    ... eller till en mapp du väljer

Reglerna bor i `sortering.toml` och är gjorda för att ändras. Skriptet lär sig
inget självt — det är avsiktligt: en regel du kan läsa och rätta är värd mer än
en modell som gissar bättre men inte går att argumentera med.

**Skriptet flyttar aldrig något av sig självt.** Förslagen godkänns en i taget.

Uppmätt 2026-09-21 mot 286 sorterade transkript: förslag ges för 89 % av
filerna, och 55 % av dem är rätt. Spridningen är stor och det är poängen —
NAR 92 %, helande-dunamis 82 %, Kirk 53 %, men god-karismatik 11 % och
andra-ideer 8 %. Kör `--mat` efter varje ändring i reglerna; siffran per mapp
säger mer än totalen.

Se CLAUDE.md.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import korrigeringar as k

REGELFIL = k.PROJECT_ROOT / "sortering.toml"


@dataclass(slots=True)
class Forslag:
    mapp: str | None
    gren: str | None
    varfor: str
    poang: dict[str, int]
    sakerhet: str          # "uttalat", "tydligt", "svagt", "inget"


def las_regler() -> dict:
    with open(REGELFIL, "rb") as f:
        return tomllib.load(f)


def monster(ord_: str) -> re.Pattern:
    """Ordgräns med plats för svensk böjning: 'profet' träffar profeten och
    profetisk, men inte mitt inne i ett annat ord. Utan gränsen matchar 'nar'
    inuti 'när' och drar halva arkivet till NAR-profetrörelsen."""
    return re.compile(r"(?<![\w])" + re.escape(ord_) + r"\w{0,3}(?![\w])", re.I)


def text_och_inledning(p: Path, sekunder: float) -> tuple[str, str]:
    d = json.loads(p.read_text(encoding="utf-8"))
    segs = d.get("segments", [])
    hel = " ".join((s.get("text") or "") for s in segs)
    inled = " ".join((s.get("text") or "") for s in segs
                     if (s.get("start") or 0) < sekunder)
    tvatta = lambda s: " " + re.sub(r"\s+", " ", s.lower()) + " "
    return tvatta(hel), tvatta(inled)


def alla_mappar(regler: dict) -> list[str]:
    ut = []
    for g in regler["gren"]:
        if g.get("mapp"):
            ut.append(g["mapp"])
        for lov in g.get("lov", []):
            ut.append(lov["mapp"])
    return ut


def uttalat_val(regler: dict, inledning: str) -> tuple[str, str] | None:
    """Sa Lars själv var den hör hemma? Det avgör saken, oavsett allt annat.

    Letar efter en inledningsfras och ser vilken mapps ord som nämns strax
    efter. Ingen fil i arkivet har detta ännu — det är den disciplin som ska
    göra sorteringen tillförlitlig framöver."""
    u = regler.get("uttalat", {})
    for fras in u.get("fraser", []):
        i = inledning.find(fras.lower())
        if i < 0:
            continue
        svans = inledning[i + len(fras): i + len(fras) + 120]
        for g in regler["gren"]:
            for mapp, ord_ in ([(g.get("mapp"), g.get("ord", []))] if g.get("mapp") else
                               [(l["mapp"], l["ord"]) for l in g.get("lov", [])]):
                for o in ord_:
                    if monster(o).search(svans):
                        return mapp, f"du sa det själv: {fras!r} … {o!r}"
    return None


def foresla(regler: dict, p: Path) -> Forslag:
    sek = float(regler.get("uttalat", {}).get("inledning_sekunder", 45))
    hel, inledning = text_och_inledning(p, sek)

    uttalat = uttalat_val(regler, inledning)
    if uttalat:
        return Forslag(uttalat[0], None, uttalat[1], {}, "uttalat")

    # Grennivå först: summera alla löv i grenen. Ordningen i filen avgör vid
    # lika poäng — det är prioritetsordningen.
    grenpoang: dict[str, int] = {}
    lovpoang: dict[str, dict[str, int]] = {}
    for g in regler["gren"]:
        namn = g["namn"]
        lov = g.get("lov") or [{"mapp": g.get("mapp"), "ord": g.get("ord", [])}]
        lovpoang[namn] = {}
        for l in lov:
            n = sum(1 for o in l["ord"] if monster(o).search(hel))
            lovpoang[namn][l["mapp"]] = n
        grenpoang[namn] = sum(lovpoang[namn].values())

    ordning = [g["namn"] for g in regler["gren"]]
    basta = max(ordning, key=lambda n: (grenpoang[n], -ordning.index(n)))
    if not grenpoang[basta]:
        return Forslag(None, None, "inget nyckelord träffade", grenpoang, "inget")

    tvaa = sorted((grenpoang[n] for n in ordning), reverse=True)[1]
    marginal = grenpoang[basta] - tvaa
    sakerhet = "tydligt" if marginal >= 2 else "svagt"

    # Hur lövet väljs inom grenen är en inställning, för grenarna är olika.
    #
    # "prioritet" (standard): första lövet med en träff vinner. Rätt när löven
    # är ordnade efter intresse och har egna ord — ett Kirk-memo kan handla
    # mycket om Trump utan att vara ett Trump-memo. Uppmätt lyfte det Kirk från
    # 39 % till 53 % och NAR till 92 %.
    #
    # "poäng": flest träffar vinner. Rätt när ett löv är litet men dess ord är
    # vanligt: 'wimber' finns i 22 filer medan mappen har två, så prioritet
    # skulle låta den äta grenen. Uppmätt föll helande-dunamis från 82 % till
    # 59 % med prioritet i gren 5.
    lov = lovpoang[basta]
    grendef = next(g for g in regler["gren"] if g["namn"] == basta)
    if grendef.get("lovval", "prioritet") == "poang":
        mapp = max(lov, key=lambda m: (lov[m], -list(lov).index(m)))
    else:
        mapp = next((m for m in lov if lov[m]), next(iter(lov)))
    varfor = (f"{grenpoang[basta]} träffar i grenen (tvåan har {tvaa})"
              + (f", lövet {mapp} har {lov[mapp]}" if len(lov) > 1 else ""))
    return Forslag(mapp, basta, varfor, grenpoang, sakerhet)


# --------------------------------------------------------------------------- #
# Mätning mot facit
# --------------------------------------------------------------------------- #

def mat(cfg: dict, regler: dict) -> int:
    root = Path(cfg["data"]["root"])
    import collections
    ratt = collections.Counter()
    fel = collections.Counter()
    inget = collections.Counter()
    forv = collections.Counter()
    for p in sorted(k.iter_transkript(root)):
        facit = k.temamapp_for(cfg, p)
        if not facit:
            continue
        f = foresla(regler, p)
        if f.mapp is None:
            inget[facit] += 1
        elif f.mapp == facit:
            ratt[facit] += 1
        else:
            fel[facit] += 1
            forv[(facit, f.mapp)] += 1

    tot = sum(ratt.values()) + sum(fel.values()) + sum(inget.values())
    gav = sum(ratt.values()) + sum(fel.values())
    print(f"Reglerna mot {tot} sorterade transkript")
    print(f"  förslag gavs för {gav} ({gav/tot:.0%}), varav rätt "
          f"{sum(ratt.values())} ({sum(ratt.values())/gav:.0%} av förslagen, "
          f"{sum(ratt.values())/tot:.0%} av alla)")
    print(f"  inget förslag: {sum(inget.values())}")
    print()
    print(f"{'mapp':44s} {'rätt':>5s} {'fel':>4s} {'tyst':>5s} {'av förslagen':>13s}")
    for mapp in alla_mappar(regler):
        r, f_, i = ratt[mapp], fel[mapp], inget[mapp]
        if r + f_ + i == 0:
            continue
        andel = f"{r/(r+f_):.0%}" if r + f_ else "—"
        print(f"{mapp:44s} {r:5d} {f_:4d} {i:5d} {andel:>13s}")
    print()
    print("vanligaste felen:")
    for (facit, gissning), n in forv.most_common(6):
        print(f"  {n:3d}  {facit[:34]:34s} -> {gissning[:34]}")
    return 0


# --------------------------------------------------------------------------- #
# Flytt — bara på uttryckligt godkännande
# --------------------------------------------------------------------------- #

def flytta(cfg: dict, regler: dict, stam: str, till: str | None) -> int:
    root = Path(cfg["data"]["root"])
    inkorg = root / cfg["data"].get("incoming", "incoming")
    json_path = inkorg / f"{stam}.json"
    if not json_path.is_file():
        print(f"FEL: {json_path} finns inte.", file=sys.stderr)
        return 2

    mal_namn = till
    if mal_namn is None:
        f = foresla(regler, json_path)
        if f.mapp is None:
            print(f"FEL: inget förslag för {stam} — ange mapp med --till.", file=sys.stderr)
            return 2
        mal_namn = f.mapp
        print(f"Följer förslaget: {mal_namn}  ({f.varfor})")
    if mal_namn not in alla_mappar(regler):
        print(f"FEL: {mal_namn!r} finns inte i sortering.toml.", file=sys.stderr)
        return 2

    mal = root / mal_namn
    if not mal.is_dir():
        print(f"FEL: mappen {mal} finns inte.", file=sys.stderr)
        return 2

    # Hela familjen flyttas tillsammans — ljud, transkript och allt härlett.
    # Lämnas något kvar blir resten föräldralöst (det synka-namn.py finns för).
    familj = [q for q in sorted(inkorg.iterdir())
              if q.is_file() and (q.stem == stam or k.stam_av(q.name) == stam)]
    if not familj:
        print(f"FEL: hittade inga filer för {stam}.", file=sys.stderr)
        return 2

    krockar = [q for q in familj if (mal / q.name).exists()]
    if krockar:
        print(f"AVBRYTER: dessa finns redan i {mal_namn}:", file=sys.stderr)
        for q in krockar:
            print(f"  {q.name}", file=sys.stderr)
        return 1

    for q in familj:
        q.rename(mal / q.name)
        print(f"  {q.name}  ->  {mal_namn}/")
    print(f"Klart: {len(familj)} filer flyttade.")
    print("Arbetskopian i granska/state/ är platt och behöver inte flyttas.")
    print("Kör namnvakt.py om du vill kontrollera att inget kolliderar.")
    return 0


def main() -> int:
    cfg = k.load_config()
    regler = las_regler()
    args = sys.argv[1:]

    if "--mat" in args:
        return mat(cfg, regler)

    if "--flytta" in args:
        i = args.index("--flytta")
        if i + 1 >= len(args):
            print("FEL: --flytta kräver en filstam.", file=sys.stderr)
            return 2
        till = args[args.index("--till") + 1] if "--till" in args else None
        return flytta(cfg, regler, args[i + 1], till)

    visa_alla = "--alla" in args
    root = Path(cfg["data"]["root"])
    inkorg = root / cfg["data"].get("incoming", "incoming")
    filer = sorted(p for p in inkorg.glob("*.json") if k.ar_transkript(p.name))
    if not filer:
        print(f"Inga transkript i {inkorg}.")
        return 0

    print(f"Förslag för {len(filer)} memo i {inkorg.name}/")
    print("Skriptet flyttar ingenting av sig självt.")
    print()
    for p in filer:
        f = foresla(regler, p)
        mark = {"uttalat": "★", "tydligt": "•", "svagt": "?", "inget": "—"}[f.sakerhet]
        print(f"  {mark} {p.stem}")
        print(f"      {f.mapp or 'INGET FÖRSLAG':44s} {f.varfor}")
        if visa_alla and f.poang:
            rad = "  ".join(f"{g}:{n}" for g, n in f.poang.items() if n)
            print(f"      poäng: {rad or '(inga träffar)'}")
    print()
    print("★ du sa det själv   • tydligt   ? svagt, kontrollera   — inget förslag")
    print()
    print("Godkänn en i taget:")
    print("    sortera.py --flytta <stam>              följ förslaget")
    print("    sortera.py --flytta <stam> --till <mapp>  välj själv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
