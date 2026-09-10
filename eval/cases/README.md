# The benchmark

Twelve cases. Small enough that every one was chosen deliberately, and every
number computed over them carries its n so nobody mistakes twelve for a
population.

## The split

Eight in `dev/`, four in `holdout/`. Committed now, before any tuning, because a
split decided after looking at results is not a split.

Prompts are tuned against `dev/` only. `holdout/` is run once, at the end, and
the difference between the two is the honest estimate of how much of the dev
performance was overfitting.

## What each case is for

Every case asserts something specific, and cases deliberately leave fields null
where they assert nothing — a case testing abstention has no opinion about the
band, and scoring it on one would punish the benchmark for being precise.

The cases that matter most are the ones where the expected answer is "I cannot
tell". Any system can be graded on whether it agreed with a recruiter about a
strong candidate. The interesting question is whether it invents an answer where
a person could not read one.
