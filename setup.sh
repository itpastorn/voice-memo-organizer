# Bekvämlighetskommandon för Git Bash. Originalet — versionerat med koden det
# driver. Datamappens setup.sh är en rad som sourcar den här filen.
#
# MÅSTE SOURCAS, inte köras. Ett skript som körs får en egen skalprocess, och
# variabler och funktioner dör med den:
#
#     source setup.sh          (eller: . setup.sh)
#
# Ger: harmapp, batch, flagga, granska, aktuell, propagera, applicera,
# forbattra, vmo. Kör `vmohjalp` för listan.

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "setup.sh måste sourcas, annars försvinner allt när skriptet slutar:" >&2
    echo "    source setup.sh" >&2
    exit 1
fi

# Projektroten härleds ur filens egen plats. Ingen absolut sökväg i koden — den
# enda som finns bor i datamappens enradiga setup.sh, och behöver bara ändras
# på ett ställe om projektet flyttar (t.ex. till arbetsstationen).
VMO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$VMO/venv/Scripts/python.exe"

if [ ! -x "$PY" ]; then
    echo "setup.sh: hittar inte python i $PY" >&2
    echo "  Skapa venv:et, eller rätta sökvägen i datamappens setup.sh." >&2
else
    # Funktioner, inte alias: alias expanderas inte i skript och sväljer
    # argument sämre. Alla vidarebefordrar "$@" oförändrat, så --dry-run,
    # --antal=N, --igen och --troskel= fungerar precis som i README.
    batch()     { "$PY" "$VMO/batch-transkribera.py" "$@"; }   # steg a
    flagga()    { "$PY" "$VMO/batch-flagga.py"       "$@"; }   # steg b
    propagera() { "$PY" "$VMO/propagera-namn.py"     "$@"; }   # steg b, issue #7
    applicera() { "$PY" "$VMO/batch-applicera.py"    "$@"; }   # steg 4
    forbattra() { "$PY" "$VMO/batch-forbattra.py"    "$@"; }   # steg c + negationsvakt
    aktuell()   { "$PY" "$VMO/aktuell.py"            "$@"; }   # vilken fil är vald?
    vmo()       { cd "$VMO" || return; }
    granska()   { ( cd "$VMO/granska" && docker compose up ); }  # GUI på :8137

    # Transkribera ljudet i mappen du står i. Utan argument kör
    # batch-transkribera.py sin egen upptäckt över HELA arkivet (fem nyaste),
    # vilket sällan är vad man vill när man står i en temamapp. Den här
    # kapslar in "$PWD"/-mönstret som är lätt att glömma.
    harmapp() {
        local kandidater=("$PWD"/*.m4a "$PWD"/*.mp3 "$PWD"/*.aac)
        local filer=()
        local f
        for f in "${kandidater[@]}"; do
            [ -e "$f" ] && filer+=("$f")
        done
        if [ ${#filer[@]} -eq 0 ]; then
            echo "harmapp: inga ljudfiler i $PWD" >&2
            return 1
        fi
        batch "${filer[@]}" "$@"
    }

    vmohjalp() {
        cat <<'HJALP'
Kommandon (alla tar samma flaggor som skripten):

  harmapp [--dry-run]     steg a på ljudet i mappen du står i
  batch FIL... [flaggor]  steg a på utpekade filer (utan argument: 5 nyaste i arkivet)
  flagga [--dry-run]      steg b, LLM-flaggning + gör granskningsklart
  granska                 starta webb-GUI:t på http://localhost:8137
  aktuell                 vilken fil är vald i GUI:t? (skriver inget)
  propagera [--dry-run]   sprid fattade rättelser till orättade förekomster
  applicera [--dry-run]   skriv in besluten i alla färdiggranskade filer
  forbattra [--dry-run]   steg c -> .md, med negationsvakt efter varje fil
  vmo                     gå till projektmappen

Ordningen: harmapp -> flagga -> granska -> propagera -> granska -> applicera -> forbattra
Se README.md i projektet för kostnader och fallgropar.
HJALP
    }

    echo "voice-memo-organizer laddat. Kör 'vmohjalp' för kommandolistan."
fi
