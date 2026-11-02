# VisionOps worker: Python + Tesseract OCR
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MODEL_CONFIG_DIR=/app/model_configs

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY visionops/ visionops/
COPY model_configs/ model_configs/

ARG GIT_SHA=dev
LABEL org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.title="visionops-worker"
ENV GIT_SHA=$GIT_SHA

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin worker
USER 10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "-m", "visionops.worker", "healthcheck"]

CMD ["python", "-m", "visionops.worker"]
