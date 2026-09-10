-- Structured log events, alongside the JSONL file.
--
-- Two sinks because they answer different questions. The file is what an
-- engineer greps; this table is what the operations page queries and what the
-- evaluation aggregates. Deriving one from the other would let the number in a
-- report and the line in a file disagree.
--
-- Payload is the whole redacted event as JSON. Querying it costs a json_extract
-- and saves a migration every time somebody logs a new field, which at this
-- stage is the right trade.

CREATE TABLE IF NOT EXISTS log_events (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    level       TEXT NOT NULL,
    event       TEXT NOT NULL,
    run_id      TEXT,
    payload     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_log_events_run ON log_events(run_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_log_events_name ON log_events(event, occurred_at);
