# Gold labels

Nothing outside `eval/` may read this directory. A test walks the source tree
and fails if anything does, because a benchmark whose answers are reachable from
the system under test measures nothing — and the failure would be invisible,
since the numbers would look excellent.

## What belongs here

`manual_baseline.csv`, with the columns `case_id`, `band`, `minutes`,
`reviewer_id`. One row per case, recording what a recruiter decided and how long
it took them, collected before the system was run against the same documents.

## Why it is not committed

The decisions in it would be real people's judgements about real applications.
Inventing twelve of them and labelling the column "manual baseline" would be
fabricating the one number in this report that a reader has no way to check.

So the file is absent, and arm A is reported as **not measured** rather than as
zero or as a guess. The comparison against human judgement is therefore missing
from the report — which is the honest state of it, and is stated at the top of
every run rather than in a footnote.

## Filling it in

Have a recruiter screen the twelve documents in `eval/cases/`, with a timer,
before showing them anything this system produced. Record the band they chose
and the minutes it took. Then `make eval` reports the comparison.

The order matters: a person who has read the system's recommendation is not an
independent rater, and a baseline collected afterwards measures agreement with
an anchor rather than agreement with a judgement.
