FROM python:3.12.11-slim-bookworm

ARG BUILD_VERSION=0.1.6
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PAGELAB_DATA_DIR=/data \
    PAGELAB_VERSION=${BUILD_VERSION}

WORKDIR /opt/pageload-lab

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && find /var/lib/apt/lists -type f -delete \
    && chmod -R a+rX /ms-playwright

COPY app ./app
COPY run.sh ./run.sh
RUN chmod 0755 ./run.sh \
    && python -m compileall -q app

EXPOSE 8099
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8099/health', timeout=8)); raise SystemExit(0 if r.get('status') in ('healthy','degraded') else 1)"

ENTRYPOINT ["/opt/pageload-lab/run.sh"]
