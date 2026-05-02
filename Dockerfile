# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libcairo2-dev \
    libpango1.0-dev \
    libjpeg-dev \
    libpng-dev \
    libwebp-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements/ ./requirements/
RUN pip install --prefix=/install -r requirements/production.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=qrplatform.settings.production

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libcairo2 \
    libpango-1.0-0 \
    libjpeg62-turbo \
    libpng16-16 \
    libwebp7 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r qrapp && useradd -r -g qrapp qrapp

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application
COPY backend/ .

# Create directories and set permissions
RUN mkdir -p /app/media /app/staticfiles /app/logs && \
    chown -R qrapp:qrapp /app

USER qrapp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

CMD ["gunicorn", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--worker-class", "gthread", \
     "--threads", "4", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "100", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "qrplatform.wsgi:application"]
