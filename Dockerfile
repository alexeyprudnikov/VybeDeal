FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source only – NO secrets in the image (see below)
COPY app.py .
COPY dealfinder/ ./dealfinder/
COPY .streamlit/config.toml ./.streamlit/config.toml

# Run unprivileged; /app/data holds favourites, price history, saved searches
RUN useradd --create-home --uid 10001 vybe \
    && mkdir -p /app/data \
    && chown -R vybe:vybe /app
USER vybe

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health',timeout=4)" || exit 1

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]

# ── Secrets ──────────────────────────────────────────────────────────────────
# Exactly ONE source, resolved by dealfinder.ai.resolve_key(). The key is never
# baked into the image (an earlier version COPY'd .streamlit/secrets.toml, which
# leaked it into a downloadable layer). Preferred source: secrets/gemini.key.
#
# Preferred (file, same path locally and in the container):
#
#   mkdir -p secrets && printf '%s\n' "AIza…" > secrets/gemini.key
#   docker run -p 8501:8501 \
#     --mount type=bind,src=$PWD/secrets/gemini.key,dst=/run/secrets/gemini_key:ro \
#     -v vybedeal-data:/app/data vybedeal
#
# CI alternative (no file) – then do NOT mount a key file, two sources abort:
#
#   docker run -p 8501:8501 -e GEMINI_API_KEY="$KEY" vybedeal
#
# compose: see docker-compose.yml (secrets: [gemini_key]).
