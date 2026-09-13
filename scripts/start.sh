#!/bin/sh
# What the container runs. Also fine on any Linux box with the image's
# dependencies installed.
#
# 1. The data directories, wherever the environment points them (a mounted
#    disk on Render, the working directory otherwise).
# 2. The synthetic corpus, so the sample candidates exist.
# 3. The sample candidates through the pipeline, in demo mode, with the
#    stand-in. Already-assessed ones are recognised, so a restart is quick.
# 4. The API, serving the built frontend from the same origin, with the log.
set -eu

DB_PATH="${DATABASE_PATH:-data/db/submission_desk.sqlite}"
mkdir -p "$(dirname "$DB_PATH")" "${BLOB_DIR:-data/blobs}" "${LOG_DIR:-data/logs}"

if [ ! -d data/samples/synthetic ]; then
  python -m scripts.make_synthetic_corpus
fi

# The samples come from the local corpus whatever the service's source is:
# demo mode refuses to start with SOURCE_ADAPTER=drive, and the seed must
# not be the thing that reads a real folder.
SOURCE_ADAPTER=local python -m app.cli.main --demo process --role ai-engineer || echo "seeding skipped: $?"

exec python -m app.cli.main api --host 0.0.0.0 --port "${PORT:-8000}" --logs
