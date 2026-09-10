"""Changing what a role asks for.

The rubric is the product's real control surface. Weights, wording, cutoffs and
the number of quotations a point needs are all here, and changing any of them
changes how candidates are scored without anyone touching code.

Which is exactly why nothing is written until it has been validated and the
change has been shown as a diff. A rubric saved with a bad weight would score
every candidate wrongly and look like it worked.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import streamlit as st
import yaml

from app.main import deps, state
from domain.contracts.rubric_loader import RubricInvalid, load_rubric, rubric_hash


def render() -> None:
    current = state()
    st.header("Role definition")

    loader = deps().rubric_loader
    directory = Path(getattr(loader, "directory", "rubrics"))
    available = list(getattr(loader, "available", lambda: [])())

    if not available:
        st.error("No role definitions were found in rubrics/.", icon="🛑")
        return

    role_id = st.selectbox(
        "Role",
        options=available,
        index=available.index(current.role_id) if current.role_id in available else 0,
    )
    path = directory / f"{role_id}.yaml"
    original = path.read_text(encoding="utf-8")

    st.caption(
        "Edit the file below. Nothing is saved until it validates, and you will see "
        "exactly what changes before it is written."
    )

    _explain()

    edited = st.text_area("Definition", value=original, height=520, key=f"rubric-{role_id}")

    if edited == original:
        st.caption("No changes yet.")
        return

    parsed = _validate(edited, path.name)
    if parsed is None:
        return

    st.subheader("What changes")
    diff = list(
        difflib.unified_diff(
            original.splitlines(),
            edited.splitlines(),
            fromfile=f"{path.name} (now)",
            tofile=f"{path.name} (after)",
            lineterm="",
        )
    )
    st.code("\n".join(diff) or "No textual change.", language="diff")

    st.warning(
        "Saving changes how every candidate assessed against this role is scored "
        "from now on. Runs already completed keep the rubric they were scored "
        "under.",
        icon="⚠️",
    )

    if st.button("Save this definition", type="primary"):
        path.write_text(edited, encoding="utf-8")
        st.success(
            f"Saved. The definition is now version {parsed.version}, "
            f"fingerprint {rubric_hash(parsed)[:12]}."
        )


def _validate(text: str, source: str):
    """Parse and validate, naming every problem rather than the first one.

    A recruiter editing YAML gets one round trip per mistake if only the first
    is reported, and they will stop using the page.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        st.error(f"This is not valid YAML: {error}", icon="🛑")
        return None

    if not isinstance(data, dict):
        st.error("This file does not describe a role.", icon="🛑")
        return None

    try:
        parsed = load_rubric(data, source=source)
    except RubricInvalid as invalid:
        st.error("This definition cannot be used yet:", icon="🛑")
        for problem in invalid.problems:
            st.markdown(f"- {problem}")
        return None

    st.success(
        f"Valid. {len(parsed.criteria)} criteria, "
        f"{sum(item.weight for item in parsed.criteria)} total weight.",
        icon="✅",
    )
    return parsed


def _explain() -> None:
    """The two fields people get wrong, explained where they are edited."""
    with st.expander("What the fields mean", expanded=False):
        st.markdown(
            """
            **`min_supported`** is how many separate quotations the documents must
            contain before a point counts as met. Raising it makes the system
            harder to convince.

            **`min_coverage`** is how much of the rubric, by weight, has to be
            assessable before a score is reported at all. Below it, the system
            says it does not know enough rather than guessing low.

            **`state_points`** is what each outcome is worth. There is deliberately
            no entry for *not addressed*: a point the documents never mention is
            left out of the calculation rather than counted as a zero, so a
            candidate is never marked down for something their CV simply did not
            say.

            **`forbidden_attributes`** are never asked about and never mentioned.
            Adding one here protects against it without a code change.
            """
        )


render()
