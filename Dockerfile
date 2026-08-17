# Build stage
FROM python:3.12-slim-bookworm AS builder

# Install uv using the official method
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies required for building certain Python packages
# Add Node.js 20.x LTS for building frontend
# NOTE: gcc/g++/make removed - uv should download pre-built wheels. Add back if build fails.
# NOTE: gcc/g++/make required for some python dependencies
RUN echo 'Acquire::Check-Date "false";' > /etc/apt/apt.conf.d/99ignore-release-date \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Set build optimization environment variables
ENV MAKEFLAGS="-j$(nproc)"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Set the working directory in the container to /app
WORKDIR /app

# Copy dependency files and minimal package structure first for better layer caching
COPY pyproject.toml uv.lock ./
COPY open_notebook/__init__.py ./open_notebook/__init__.py
COPY scripts/patch_moviepy_pillow_metadata.py /tmp/patch_moviepy_pillow_metadata.py

# Install dependencies with optimizations (this layer will be cached unless dependencies change)
RUN uv sync --frozen --no-dev --extra ocr \
    && .venv/bin/python /tmp/patch_moviepy_pillow_metadata.py \
        --site-packages /app/.venv/lib/python3.12/site-packages \
    && .venv/bin/python -m pip check

# Pre-download tiktoken encoding so the app works offline (issue #264).
# /app/tiktoken-cache is intentionally outside /app/data/ so that volume mounts
# of /app/data (for user data persistence) do not hide the pre-baked encoding.
# config.py reads TIKTOKEN_CACHE_DIR from the environment to pick up this path.
ENV TIKTOKEN_CACHE_DIR=/app/tiktoken-cache
RUN mkdir -p /app/tiktoken-cache && \
    .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"

# Install frontend dependencies and build
WORKDIR /app/frontend
ARG NPM_REGISTRY=https://registry.npmjs.org/
# Public Next.js variables are compiled into the browser bundle.  Keep SSE
# disabled by default, but let the isolated pilot image opt in explicitly.
ARG NEXT_PUBLIC_ASK_SSE_ENABLED=false
ENV NEXT_PUBLIC_ASK_SSE_ENABLED=${NEXT_PUBLIC_ASK_SSE_ENABLED}
COPY frontend/package.json frontend/package-lock.json ./
RUN npm config set registry ${NPM_REGISTRY} \
 && npm config set fetch-retries 5 \
 && npm config set fetch-retry-mintimeout 20000 \
 && npm config set fetch-retry-maxtimeout 120000
# Retry npm ci to survive transient registry ECONNRESETs, which are common on
# the QEMU-emulated arm64 leg of the multi-arch build.
RUN i=0; until npm ci; do \
      i=$((i+1)); \
      if [ "$i" -ge 5 ]; then echo "npm ci failed after $i attempts"; exit 1; fi; \
      echo "npm ci failed (attempt $i); retrying in 15s"; sleep 15; \
    done
COPY frontend/ ./
RUN npm run build

# Return to app root
WORKDIR /app

# Copy backend/application source only after the independently cacheable
# frontend dependency and build layers. Host build artifacts are excluded by
# .dockerignore, so this does not overwrite the generated Next.js output.
COPY . /app

# Runtime stage
FROM python:3.12-slim-bookworm AS runtime

# Install only runtime system dependencies (no build tools)
# Add Node.js 20.x LTS for running frontend
RUN echo 'Acquire::Check-Date "false";' > /etc/apt/apt.conf.d/99ignore-release-date \
    && apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ffmpeg \
    supervisor \
    curl \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-vie \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv using the official method
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set the working directory in the container to /app
WORKDIR /app

# Copy the virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv

# The production frontend executes the prebuilt standalone server with Node
# only. Neither system npm nor the npm bundled by nodejs-wheel is invoked at
# runtime. Remove those package-manager trees so their build-time dependency
# CVEs are not shipped in the pilot image; keep both Node executables intact.
RUN rm -rf \
    /usr/lib/node_modules/npm \
    /usr/bin/npm \
    /usr/bin/npx \
    /app/.venv/lib/python3.12/site-packages/nodejs_wheel/lib/node_modules/npm \
    /app/.venv/lib/python3.12/site-packages/nodejs_wheel/bin/npm \
    /app/.venv/lib/python3.12/site-packages/nodejs_wheel/bin/npx

# Copy the source code (the rest)
COPY . /app

# Copy pre-downloaded tiktoken encoding from builder (outside /data/ — volume-mount safe)
COPY --from=builder /app/tiktoken-cache /app/tiktoken-cache

# Ensure uv uses the existing venv without attempting network operations
ENV UV_NO_SYNC=1
ENV VIRTUAL_ENV=/app/.venv
# Point the app at the pre-baked tiktoken encoding (see open_notebook/config.py)
ENV TIKTOKEN_CACHE_DIR=/app/tiktoken-cache

# Bind Next.js to all interfaces (required for Docker networking and reverse proxies)
ENV HOSTNAME=0.0.0.0

# Copy built frontend from builder stage
COPY --from=builder /app/frontend/.next/standalone /app/frontend/
COPY --from=builder /app/frontend/.next/static /app/frontend/.next/static
COPY --from=builder /app/frontend/public /app/frontend/public
COPY --from=builder /app/frontend/start-server.js /app/frontend/start-server.js

# Expose ports for Frontend and API
EXPOSE 8502 5055

RUN mkdir -p /app/data

# Copy and make executable the dependency wait scripts
COPY scripts/wait-for-api.sh /app/scripts/wait-for-api.sh
COPY scripts/wait-for-surreal.sh /app/scripts/wait-for-surreal.sh
RUN chmod +x /app/scripts/wait-for-api.sh \
    && chmod +x /app/scripts/wait-for-surreal.sh

# Copy supervisord configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create log directories
RUN mkdir -p /var/log/supervisor

# Runtime API URL Configuration
# The API_URL environment variable can be set at container runtime to configure
# where the frontend should connect to the API. This allows the same Docker image
# to work in different deployment scenarios without rebuilding.
#
# If not set, the system will auto-detect based on incoming requests.
# Set API_URL when using reverse proxies or custom domains.
#
# Example: docker run -e API_URL=https://your-domain.com/api ...

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
