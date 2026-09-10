"""Call sites the evaluation makes, declared with the same discipline.

Separate from ``infrastructure/models/registry.py`` on purpose. That registry is
the entire probabilistic surface of the *system*, and a test asserts it has
exactly five entries: adding a sixth needs a decision record, which is friction
on purpose. Arm B is not part of the system — it is the thing the system is
being compared against — so putting it there would inflate a number that means
something.

Declared here anyway, because the rule is that every call to a model is written
down with a sentence saying why it exists, and an evaluation arm is no exception.
"""

from __future__ import annotations

#: call site -> why a model is used there.
EVAL_CALL_REGISTRY: dict[str, str] = {
    "eval.arm_b": (
        "The comparison arm. One call, the whole document and the whole rubric, "
        "asked for a band — the approach this architecture is an argument "
        "against, run properly so the argument has something to be measured "
        "against. It exists only in the evaluation and is never reached from a "
        "run."
    ),
}
