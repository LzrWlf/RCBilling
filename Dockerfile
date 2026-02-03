# Multi-stage build for smaller image
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies for Playwright and PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    # Playwright Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder and install
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache /wheels/*

# Install Playwright browsers (Chromium only)
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd --create-home appuser && \
    mkdir -p /app/uploads /app/screenshots && \
    chown -R appuser:appuser /app
USER appuser

# Environment defaults
ENV PLAYWRIGHT_HEADLESS=true
ENV FLASK_ENV=production

# Expose port (Railway sets PORT automatically)
EXPOSE 8080

# Run with gunicorn
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120 run:app"]
