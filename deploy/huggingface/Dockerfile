# Image for the public demo on Hugging Face Spaces (Docker SDK).
#
# Differences from the repository's main Dockerfile: it listens on 7860 (the
# Spaces default), runs in demo mode, and seeds the demo account at build time.
# Docker Spaces need a PRO account. Without paid storage there is no
# persistent disk, so every restart comes back to exactly this seeded state.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[web]"

RUN CHESSWHISPER_DATA_DIR=/opt/chesswhisper python -c \
        "from chesswhisper.engine_provision import get_stockfish_path; print(get_stockfish_path())" \
        > /opt/stockfish-path \
    && ln -s "$(cat /opt/stockfish-path)" /usr/local/bin/stockfish \
    && stockfish quit

# Spaces run the container as uid 1000.
RUN useradd --create-home --uid 1000 user
USER user
ENV HOME=/home/user \
    CHESSWHISPER_DATA_DIR=/home/user/data \
    CHESSWHISPER_DEMO=1

# Seed the demo account, then drop the secrets seeding generated so each
# container start creates its own.
RUN chesswhisper seed-demo --analyses 3 \
    && rm -f "$CHESSWHISPER_DATA_DIR/session_secret.key" "$CHESSWHISPER_DATA_DIR/api_key_encryption.key"

# Only the Spaces proxy can reach the container, so take the visitor's address
# from its X-Forwarded-For header; otherwise every visitor shares one login
# rate-limit bucket.
ENV FORWARDED_ALLOW_IPS="*"

EXPOSE 7860
CMD ["chesswhisper", "serve", "--host", "0.0.0.0", "--port", "7860"]
