# Submission Desk, demo parity only.
#
# This image exists so that "clone and run three commands" has an answer for
# somebody without Python 3.11 on their machine. It runs the same offline demo
# the Makefile runs: synthetic candidates, the deterministic stand-in, demo
# mode on, nothing sent anywhere. It is not a deployment. There is no
# authentication, no TLS, and no persistent volume declared, because a pilot
# runs on one recruiter's machine (RUNBOOK.md) and a hosted deployment is a
# decision this repository deliberately does not make (ADR-003, ADR-009).
#
#   docker build -t submission-desk .
#   docker run --rm -p 8501:8501 submission-desk
#
# Tesseract is not installed. The synthetic corpus is plain text, so the demo
# does not need it, and the doctor reports its absence as a warning rather
# than a failure. Install it here if the image is ever used for scanned
# documents.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEMO_MODE=true \
    MODEL_PROVIDER=fake \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# Dependencies first, so a change to the source does not reinstall them.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install .

COPY . .
RUN pip install --no-deps .

# The database and the synthetic corpus are built at start rather than at
# build time, so a fresh container always starts from a clean queue.
EXPOSE 8501
CMD ["sh", "-c", "python -m scripts.make_synthetic_corpus && python -m app.cli.main process --role ai-engineer && python -m streamlit run app/main.py --server.port 8501 --server.address 0.0.0.0"]
