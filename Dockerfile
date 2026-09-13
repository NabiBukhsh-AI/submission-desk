# Submission Desk, as one container: the API, the built React interface
# served from the same origin, Tesseract for scanned pages, and the sample
# candidates assessed at start. What Render runs; also `docker run` locally.
#
#   docker build -t submission-desk .
#   docker run --rm -p 8000:8000 -e APP_SECRET=change-me submission-desk
#   open http://localhost:8000
#
# The first visit creates the admin account. A model key is entered on the
# Settings page; without one the deterministic stand-in answers. Data lives
# under DATABASE_PATH / BLOB_DIR — a mounted disk in a deployment, the
# container's own filesystem otherwise (lost on restart).

# --- stage 1: the interface ----------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- stage 2: the system ---------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Tesseract reads scanned pages. Without it a scan is recorded as unreadable
# rather than guessed, so the image ships it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a change to the source does not reinstall them.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install .

COPY . .
RUN pip install --no-deps .
COPY --from=web /web/dist ./frontend/dist

EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
