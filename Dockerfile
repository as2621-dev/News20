# News20 FastAPI worker (grounded Q&A + Gemini Live voice-token + source search).
# Runtime image for Railway. Secrets are injected at runtime by Railway — never
# baked into the image (CLAUDE.md env-var safety; see .dockerignore excludes .env*).
FROM python:3.12-slim

# ffmpeg/ffprobe on PATH: pydub (audio concat in the TTS handoff) needs them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the Python agent package is needed to serve the worker.
COPY agents ./agents

# Railway injects $PORT at runtime; default 8000 for a local `docker run`.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn agents.worker.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
