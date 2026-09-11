# ADR-003: SQLite plus a content-addressed blob store

**Status:** accepted. **Applies to:** `infrastructure/storage/`, `domain/ports/repositories.py`.

## Context

Runs, evidence, decisions, costs, errors, deliveries and events need durable,
queryable, transactional storage. The load is one recruiter and tens of
candidates a day. The handoff test is that a stranger clones the repository
and runs three commands.

## Decision

SQLite in WAL mode with foreign keys on, behind repository protocols, and a
blob directory addressed by SHA-256 of the bytes. Five migrations applied at
startup and checked by the doctor in both directions. Every dependent table
cascades on the run's foreign key, so a purge is one statement.

## Alternatives

- **A client-server database.** Correct at scale and wrong now: it adds a
  service to a clone-and-run requirement and buys nothing at this
  concurrency.
- **JSON files.** No transactions, no queries, and the operations page becomes
  a parsing exercise.
- **An embedded analytics database.** Better for the metrics queries, worse on
  the transactional write path, which is the one that matters for a decision.

## Trade-offs

One writer. A second concurrent reviewer works until two of them write the
same run at the same moment; then optimistic concurrency refuses one of them
with a sentence rather than losing an update. Sustained write contention would
surface as timeouts. There is no network access to the store, which is a
feature for a pilot and a ceiling for anything else. The ceiling is a few
hundred candidates an hour, which the pilot will not approach.

## Consequences

Migration to another store is a second implementation of the repository
protocols plus one line in the factory, verified by running the same suite
against both. The blob store's only accepted address is sixty-four hex
characters, which is what makes path traversal structurally impossible rather
than merely unlikely; reads, writes and deletes all go through that guard.

Triggers for revisiting: a second concurrent reviewer as a matter of course,
a hosted or multi-tenant deployment, or sustained write contention in the
logs.
