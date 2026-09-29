# Dispodex in a container: the web server and background worker in one process
# (`manage.py serve`). Everything that must survive a rebuild lives in /data
# (database, photos, backups, logs); mount it from the host. See docs/docker.md.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PINKSHEET_IN_DOCKER=1 \
    PINKSHEET_DATA_DIR=/data \
    PINKSHEET_HOST=0.0.0.0 \
    PINKSHEET_PORT=8765

# tzdata: Django needs the time-zone database for PINKSHEET_TIME_ZONE (nightly backup hour, log times).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1000 --user-group --home-dir /app dispodex

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x docker/entrypoint.sh \
    && mkdir -p /data \
    && chown dispodex:dispodex /data

VOLUME ["/data"]
EXPOSE 8765

# Ctrl+C is how Dispodex stops cleanly (the worker finishes its current step first).
STOPSIGNAL SIGINT
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health/' % os.environ.get('PINKSHEET_PORT', '8765'), timeout=4)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "manage.py", "serve"]
