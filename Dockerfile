FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Claude Code CLI for LLM_PROVIDER=cli (`claude -p`). Installed outside $HOME so the /data mount
# cannot hide it; its login lives in $HOME=/data/.home and survives rebuilds.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://claude.ai/install.sh | HOME=/opt/claude bash \
    && ln -s /opt/claude/.local/bin/claude /usr/local/bin/claude \
    && claude --version

ENV HOME=/data/.home \
    DISABLE_AUTOUPDATER=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY recipebot ./recipebot

RUN pip install . && mkdir -p /data

VOLUME ["/data"]

CMD ["recipebot", "loop"]
