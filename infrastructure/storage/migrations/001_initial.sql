-- Initial schema.
--
-- Thirteen tables. Nine hold contracts that are queried; the rest hold caches,
-- the append-only event log, and the delivery ledger. Contracts that are only
-- ever read back whole travel as JSON columns rather than as tables of their
-- own, because a join nobody performs is a migration nobody needed.
--
-- Numbered SQL applied in order by scripts/migrate.py. No Alembic: a linear
-- list of files a person can read beats a generated one, at this size.

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    name        TEXT NOT NULL
);

-- One execution. content_key is the semantic identity and carries the unique
-- index; run_id names this particular attempt at it.
CREATE TABLE IF NOT EXISTS runs (
    run_id                    TEXT PRIMARY KEY,
    content_key               TEXT NOT NULL UNIQUE,
    candidate_id              TEXT NOT NULL,
    role_id                   TEXT NOT NULL,
    rubric_version            TEXT NOT NULL,
    rubric_hash               TEXT NOT NULL,
    prompt_bundle_hash        TEXT NOT NULL,
    prompt_versions           TEXT NOT NULL DEFAULT '{}',
    model_tier_bindings_hash  TEXT NOT NULL,
    routing_policy_id         TEXT NOT NULL,
    blind_mode                INTEGER NOT NULL,
    calibration_enabled       INTEGER NOT NULL,
    calibration_status        TEXT NOT NULL,
    pipeline_version          TEXT NOT NULL,
    status                    TEXT NOT NULL,
    completed_nodes           TEXT NOT NULL DEFAULT '[]',
    started_at                TEXT NOT NULL,
    finished_at               TEXT,
    total_input_tokens        INTEGER NOT NULL DEFAULT 0,
    total_output_tokens       INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens       INTEGER NOT NULL DEFAULT 0,
    -- NULL means pricing is not configured. Never zero: a zero reads as free.
    total_cost_usd            TEXT,
    llm_call_count            INTEGER NOT NULL DEFAULT 0,
    retry_count               INTEGER NOT NULL DEFAULT 0,
    repair_count              INTEGER NOT NULL DEFAULT 0,
    escalation_count          INTEGER NOT NULL DEFAULT 0,
    validation_failure_count  INTEGER NOT NULL DEFAULT 0,
    invalid_span_count        INTEGER NOT NULL DEFAULT 0,
    integrity_tier            TEXT NOT NULL DEFAULT 'clean',
    integrity_report          TEXT,
    final_band                TEXT,
    reviewer_action           TEXT,
    override_count            INTEGER NOT NULL DEFAULT 0,
    score                     REAL,
    coverage                  REAL,
    recommendation            TEXT,
    previous_run_id           TEXT,
    error_codes               TEXT NOT NULL DEFAULT '[]',
    -- Optimistic concurrency. A reviewer write carrying a stale version is
    -- refused rather than allowed to clobber a decision made elsewhere.
    version                   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS documents (
    document_id            TEXT PRIMARY KEY,
    run_id                 TEXT REFERENCES runs(run_id) ON DELETE CASCADE,
    candidate_id           TEXT NOT NULL,
    original_filename      TEXT NOT NULL,
    document_sha256        TEXT NOT NULL,
    mime_type              TEXT NOT NULL,
    size_bytes             INTEGER NOT NULL,
    page_count             INTEGER,
    blob_path              TEXT NOT NULL,
    doc_role               TEXT NOT NULL,
    extraction_method      TEXT,
    extraction_confidence  REAL,
    detected_languages     TEXT NOT NULL DEFAULT '[]',
    received_at            TEXT NOT NULL
);

-- Extraction cache. Keyed on the document hash and the normalisation profile,
-- not on the run, so changing a rubric never re-parses and never re-OCRs.
CREATE TABLE IF NOT EXISTS source_texts (
    document_sha256           TEXT NOT NULL,
    normalization_profile_id  TEXT NOT NULL,
    document_id               TEXT NOT NULL,
    raw_text                  TEXT NOT NULL,
    normalized_text           TEXT NOT NULL,
    offset_runs               TEXT NOT NULL,
    pages                     TEXT NOT NULL,
    block_boundaries          TEXT NOT NULL DEFAULT '[]',
    extraction_confidence     REAL NOT NULL,
    detected_languages        TEXT NOT NULL DEFAULT '[]',
    created_at                TEXT NOT NULL,
    PRIMARY KEY (document_sha256, normalization_profile_id)
);

-- Evidence, including the items that failed span validation. Rejected items are
-- kept because a quotation the validator could not find is what the reviewer
-- most needs to see and what the hallucination metric counts.
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id          TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    criterion_id         TEXT NOT NULL,
    state                TEXT NOT NULL,
    claim                TEXT NOT NULL,
    verbatim_span        TEXT,
    provenance           TEXT,
    confidence           REAL NOT NULL,
    model_tier           TEXT NOT NULL,
    prompt_version       TEXT NOT NULL,
    escalation_state     TEXT NOT NULL,
    span_validation      TEXT NOT NULL,
    span_match_ratio     REAL,
    validated_norm_start INTEGER,
    rejected             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS assessments (
    run_id                   TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    criterion_id             TEXT NOT NULL,
    resolved_state           TEXT NOT NULL,
    resolution_rule_id       TEXT NOT NULL,
    tier_used                TEXT NOT NULL,
    escalated                INTEGER NOT NULL DEFAULT 0,
    escalation_trigger       TEXT,
    pre_escalation_state     TEXT,
    escalation_changed_state INTEGER,
    chunks_shown             TEXT,
    unassessed_reason        TEXT,
    PRIMARY KEY (run_id, criterion_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    decision_id        TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    reviewer_id        TEXT NOT NULL,
    action             TEXT NOT NULL,
    comments           TEXT,
    elapsed_seconds    INTEGER NOT NULL,
    trust_rating       INTEGER,
    post_override_band TEXT,
    decided_at         TEXT NOT NULL,
    run_version        INTEGER NOT NULL
);

-- Overrides are their own table rather than a JSON column because they are
-- queried across runs: they are the raw material of the improvement loop.
CREATE TABLE IF NOT EXISTS overrides (
    override_id    TEXT PRIMARY KEY,
    decision_id    TEXT NOT NULL REFERENCES reviews(decision_id) ON DELETE CASCADE,
    run_id         TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    criterion_id   TEXT NOT NULL,
    previous_state TEXT NOT NULL,
    new_state      TEXT NOT NULL,
    reason_code    TEXT NOT NULL,
    reason_text    TEXT NOT NULL
);

-- One row per provider call, with real usage metadata. A call cannot happen
-- without accounting, because Usage is part of the result the caller receives.
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id             TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    call_site           TEXT NOT NULL,
    criterion_id        TEXT,
    model_tier          TEXT NOT NULL,
    routing             TEXT,
    input_tokens        INTEGER NOT NULL,
    output_tokens       INTEGER NOT NULL,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    unit_price_source   TEXT,
    cost_usd            TEXT,
    currency            TEXT NOT NULL DEFAULT 'USD',
    latency_ms          INTEGER NOT NULL,
    attempt             INTEGER NOT NULL DEFAULT 0,
    repaired            INTEGER NOT NULL DEFAULT 0,
    validation_error    TEXT,
    occurred_at         TEXT NOT NULL
);

-- One row per sink, so partial delivery is representable: the CSV was written,
-- the spreadsheet append failed, and the reviewer sees exactly that.
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id  TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    sink_id      TEXT NOT NULL,
    status       TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    external_ref TEXT,
    last_error   TEXT,
    delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS calibration_cards (
    card_id                       TEXT PRIMARY KEY,
    role_id                       TEXT NOT NULL,
    rubric_version                TEXT NOT NULL,
    anonymized_summary            TEXT NOT NULL,
    criterion_states              TEXT NOT NULL,
    final_band                    TEXT NOT NULL,
    reviewer_reason_codes         TEXT NOT NULL DEFAULT '[]',
    reviewer_reason_text_redacted TEXT,
    decided_at                    TEXT NOT NULL,
    embedding                     BLOB NOT NULL
);

-- Append-only and replayable, so a run's history survives its final state.
CREATE TABLE IF NOT EXISTS run_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    node        TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    node_status TEXT NOT NULL,
    payload     TEXT,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key     TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    usage_json    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- message_redacted has already passed the log redactor. Provider errors echo
-- their input, so an unredacted message is a route by which candidate text
-- reaches the database and, from there, a handover archive.
CREATE TABLE IF NOT EXISTS errors (
    error_id         TEXT PRIMARY KEY,
    run_id           TEXT REFERENCES runs(run_id) ON DELETE CASCADE,
    node             TEXT NOT NULL,
    error_code       TEXT NOT NULL,
    error_class      TEXT NOT NULL,
    message_redacted TEXT NOT NULL,
    retryable        INTEGER NOT NULL,
    attempt          INTEGER NOT NULL,
    resulting_state  TEXT,
    occurred_at      TEXT NOT NULL
);

-- Indices for the queries the queue, the scorecard, and the operations page
-- actually make. Nothing speculative: an index is a write cost paid on every
-- insert for a read that may never happen.
CREATE INDEX IF NOT EXISTS idx_runs_status         ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_role_started   ON runs(role_id, started_at);
CREATE INDEX IF NOT EXISTS idx_documents_run       ON documents(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_run_crit   ON evidence(run_id, criterion_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run       ON llm_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_calibration_lookup  ON calibration_cards(role_id, rubric_version, decided_at);
CREATE INDEX IF NOT EXISTS idx_run_events_run_seq  ON run_events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_overrides_criterion ON overrides(criterion_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_status   ON deliveries(status);
CREATE INDEX IF NOT EXISTS idx_errors_run          ON errors(run_id);
