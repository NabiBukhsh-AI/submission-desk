-- Operator settings, written from the admin page.
--
-- One row per setting, by name. Values are text; a secret (an API key) is
-- stored sealed with the application secret and flagged, so a reader of this
-- table sees that a key is set and not what it is. The admin account lives
-- here too: one username and one password hash, never a password.
--
-- Environment variables remain the fallback for everything in this table, so
-- a fresh clone with no rows behaves exactly as before.

CREATE TABLE IF NOT EXISTS settings (
    name        TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    secret      INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);
