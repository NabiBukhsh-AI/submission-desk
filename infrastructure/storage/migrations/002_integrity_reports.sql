-- One integrity report per document, with its findings.
--
-- Findings are stored as rows rather than as a JSON blob on the document,
-- because the questions asked of them are per-detector: how often does
-- D-INSTR-IMPERATIVE fire, and on what. A blob answers that with a table scan
-- and a JSON parse; a row answers it with a GROUP BY.
--
-- Nothing here is ever deleted when a document is re-scanned. A re-scan writes
-- a new report and supersedes the old one, so the history of what a scanner
-- thought about a document at a point in time survives a detector change.

CREATE TABLE IF NOT EXISTS integrity_reports (
    report_id             TEXT PRIMARY KEY,
    document_id           TEXT NOT NULL,
    run_id                TEXT REFERENCES runs(run_id) ON DELETE CASCADE,
    tier                  TEXT NOT NULL,
    classifier_used       INTEGER NOT NULL DEFAULT 0,
    classifier_unavailable INTEGER NOT NULL DEFAULT 0,
    sanitized_char_count  INTEGER NOT NULL DEFAULT 0,
    -- Set when a later scan replaces this one. The row stays.
    superseded_at         TEXT,
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integrity_findings (
    finding_id           TEXT PRIMARY KEY,
    report_id            TEXT NOT NULL REFERENCES integrity_reports(report_id) ON DELETE CASCADE,
    document_id          TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    severity             TEXT NOT NULL,
    detector             TEXT NOT NULL,
    detector_confidence  REAL NOT NULL,
    -- Capped at 240 characters by the contract. Stored as it will be shown.
    excerpt              TEXT NOT NULL,
    provenance           TEXT,
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_integrity_reports_document
    ON integrity_reports(document_id, superseded_at);

CREATE INDEX IF NOT EXISTS ix_integrity_reports_run
    ON integrity_reports(run_id);

-- The index the precision question is asked through.
CREATE INDEX IF NOT EXISTS ix_integrity_findings_detector
    ON integrity_findings(detector, severity);
