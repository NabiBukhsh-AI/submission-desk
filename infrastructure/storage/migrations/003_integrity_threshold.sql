-- The confidence threshold a report's tier was assigned under.
--
-- Without it a stored report cannot be re-checked: the tier is a function of
-- the findings and this number together, so a deployment that tuned the
-- threshold would have every historical report fail validation on the way back
-- out of the database.
--
-- Existing rows were written under the shipped default.

ALTER TABLE integrity_reports ADD COLUMN threshold REAL NOT NULL DEFAULT 0.75;
