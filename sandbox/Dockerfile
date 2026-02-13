# ──────────────────────────────────────────────────────────────
# Agent Sandbox Image
#
# Minimal Python image with common scientific-computing packages.
# Runs as a non-root user with a writable /workspace directory.
# ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files & enable unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install pre-built packages
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Create non-root user
RUN groupadd -r sandbox && useradd -r -g sandbox -m -s /bin/bash sandbox

# Prepare workspace
RUN mkdir -p /workspace && chown sandbox:sandbox /workspace

WORKDIR /workspace
USER sandbox

# Default: execute the script injected by the manager
CMD ["python", "-u", "/workspace/main.py"]
