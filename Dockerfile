# chesswhisper web UI with Stockfish baked in.
#
#   docker build -t chesswhisper .
#   docker run -p 127.0.0.1:8000:8000 -v chesswhisper-data:/data chesswhisper
#
# The database, keys and evaluation cache live in the /data volume.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[web,postgres]"

# Download the same Stockfish release the app pins, once, at build time, and
# put it on PATH where chesswhisper looks first.
RUN CHESSWHISPER_DATA_DIR=/opt/chesswhisper python -c \
        "from chesswhisper.engine_provision import get_stockfish_path; print(get_stockfish_path())" \
        > /opt/stockfish-path \
    && ln -s "$(cat /opt/stockfish-path)" /usr/local/bin/stockfish \
    && stockfish quit

RUN useradd --create-home --uid 1000 chesswhisper \
    && mkdir /data \
    && chown chesswhisper:chesswhisper /data
USER chesswhisper

ENV CHESSWHISPER_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=4)"

CMD ["chesswhisper", "serve", "--host", "0.0.0.0", "--port", "8000"]
