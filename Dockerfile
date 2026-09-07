FROM python:3.13-slim-bookworm AS runtime

ARG APP_VERSION=0.1.0-dev

LABEL org.opencontainers.image.title="Anki Sync Hub" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ANKI_HUB_DATA_DIR=/data

WORKDIR /app

COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src

RUN python -m pip install --no-cache-dir "." \
    && groupadd --gid 1000 ankihub \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin ankihub \
    && mkdir -p /data \
    && chown -R 1000:1000 /data /app

USER 1000:1000

EXPOSE 8080 8081
VOLUME ["/data"]

ENTRYPOINT ["anki-sync-hub"]
CMD ["web"]
