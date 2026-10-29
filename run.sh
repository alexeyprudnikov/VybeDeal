#!/usr/bin/env bash
# VybeDeal starten – legt die venv beim ersten Mal an.
set -euo pipefail
cd "$(dirname "$0")"

VENV=.venv
PORT="${PORT:-8501}"

if [ ! -x "$VENV/bin/streamlit" ]; then
    echo "→ Python-Umgebung wird angelegt ($VENV) …"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip -q
    "$VENV/bin/pip" install -r requirements.txt -q
fi

if [ ! -f secrets/gemini.key ]; then
    echo "ℹ  secrets/gemini.key fehlt → KI-Bewertung aus (Heuristik-Modus)."
    echo "   Key anlegen:  printf '%s\n' \"AIza…\" > secrets/gemini.key"
fi

echo "→ VybeDeal auf http://localhost:$PORT  (Strg+C zum Beenden)"
exec "$VENV/bin/streamlit" run app.py --server.port "$PORT"
