-- Which run a calibration card came from.
--
-- Without it the documented "never anchor a run against itself" filter cannot
-- be applied: a card carries its own id and the run's id appears nowhere, so
-- the comparison had nothing to compare. A re-run of a candidate would find its
-- own earlier decision and treat it as independent corroboration.
--
-- Nullable because cards written before this column exists have no run to name,
-- and discarding them would throw away a recruiter's history to fix a filter.

ALTER TABLE calibration_cards ADD COLUMN source_run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_calibration_source ON calibration_cards(source_run_id);
