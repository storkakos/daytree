# syntax=docker/dockerfile:1

# cloudflared is a statically linked Go binary (CGO_ENABLED=0), so it runs on Alpine (musl).
# Pin a concrete version for reproducible builds (tags: hub.docker.com/r/cloudflare/cloudflared).
ARG CLOUDFLARED_TAG=2026.10.7
FROM cloudflare/cloudflared:${CLOUDFLARED_TAG} AS cloudflared

FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Berlin

RUN apk add --no-cache tzdata \
    && adduser -D -u 1000 appuser \
    && mkdir -p /data /secrets \
    && chown appuser:appuser /data /secrets \
    && chmod 700 /secrets

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY app.py ./
COPY daytree ./daytree
COPY templates ./templates
COPY static ./static

USER appuser

# 8080 = dashboard (internal), 8081 = public port for the tunnel (exposure mode is enforced on it)
EXPOSE 8080 8081
VOLUME ["/data", "/secrets"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4)" || exit 1

# Exactly ONE worker: sync state, scheduler and tunnel supervisor live in process memory.
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-b", "0.0.0.0:8081", "--workers", "1", "--threads", "4", "app:app"]
