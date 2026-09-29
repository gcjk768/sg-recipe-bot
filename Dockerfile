FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY recipebot ./recipebot

RUN pip install . && mkdir -p /data

VOLUME ["/data"]

CMD ["recipebot", "loop"]
