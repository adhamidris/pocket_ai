# ==============================================================================
# PRODUCTION DOCKERFILE - Multi-Tenant AI SaaS Platform
# ==============================================================================
# Multi-stage build for optimized production image
# Supports: Railway, Render, Fly.io, AWS ECS, any Docker platform

# ==============================================================================
# Stage 1: Builder - Install dependencies and prepare environment
# ==============================================================================
FROM python:3.12-slim as builder

# Install system dependencies for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements first for better caching
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# Pre-download FastEmbed models (if using local embeddings)
# This happens at build time so the image contains the models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')" || true

# ==============================================================================
# Stage 2: Production - Minimal runtime image
# ==============================================================================
FROM python:3.12-slim

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 pocketai && \
    mkdir -p /app /app/var/logs /app/var/media /app/var/staticfiles && \
    chown -R pocketai:pocketai /app

# Copy virtual environment from builder
COPY --from=builder --chown=pocketai:pocketai /opt/venv /opt/venv

# Copy FastEmbed cache from builder (if it exists)
COPY --from=builder --chown=pocketai:pocketai /root/.cache/fastembed /home/pocketai/.cache/fastembed 2>/dev/null || true

# Set working directory
WORKDIR /app

# Copy application code
COPY --chown=pocketai:pocketai . /app/

# Make scripts executable
RUN chmod +x /app/scripts/*.sh 2>/dev/null || true

# Switch to non-root user
USER pocketai

# Activate virtual environment
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

# Default command (can be overridden)
CMD ["/app/scripts/docker-entrypoint.sh"]
