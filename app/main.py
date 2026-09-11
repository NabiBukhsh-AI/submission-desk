"""The reviewer interface.

    streamlit run app/main.py

Zero business logic lives under app/. Every action calls an application use
case, and an architecture test walks the imports to prove it. That is not
tidiness: it is what makes the interface replaceable and the logic testable
without a browser.

Deps are built once and cached for the session. Building them per rerun would
re-run migrations on every click.
"""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from app.state import get, put
from application.deps import Deps
from application.recovery import reconcile
from infrastructure.factory import build_deps, settings_from_env

st.set_page_config(
    page_title="Submission Desk",
    page_icon="📋",
    layout="wide",
)


@st.cache_resource
def deps() -> Deps:
    """Everything the interface needs to reach, built once.

    Cached across reruns because Streamlit re-executes the whole script on every
    interaction, and running database migrations each time somebody clicks a
    button would be both slow and alarming.

    Startup is also when stalled runs are reconciled: a process that died
    mid-run left them in a processing state, and the queue should show them
    as resumable rather than as forever in progress.
    """
    built = build_deps(settings_from_env())
    reconcile(built)
    return built


def state():
    return get(st.session_state, dict(st.query_params))


def notice() -> None:
    """Show whatever the last action wanted to say, once."""
    current = state()
    if current.notice:
        st.success(current.notice)
        put(st.session_state, current.cleared())


def main() -> None:
    current = state()

    st.title("Submission Desk")
    st.caption(
        "Evidence-first screening. Every claim below is quoted from the candidate's "
        "own documents, and every quotation was checked against the source before "
        "you saw it."
    )

    if not current.reviewer_id:
        st.warning(
            "No reviewer is configured, so decisions cannot be attributed to "
            "anybody. Set REVIEWER_ID before reviewing.",
            icon="⚠️",
        )

    st.markdown(
        """
        **Start here**

        1. **Upload** — add a candidate's CV and any supporting documents.
        2. **Queue** — see what has been processed and what needs you.
        3. **Review** — read the evidence, correct anything wrong, and decide.
        4. **Rubric** — change what the role asks for.

        Nothing is sent to a candidate until you approve it.
        """
    )

    roles = _available_roles()
    if roles:
        chosen = st.selectbox(
            "Role",
            options=roles,
            index=roles.index(current.role_id) if current.role_id in roles else 0,
        )
        if chosen != current.role_id:
            put(st.session_state, _with_role(current, chosen))
    else:
        st.error(
            "No role definitions were found. Add a file to rubrics/ before processing anybody.",
            icon="🛑",
        )

    notice()


def _available_roles() -> list[str]:
    loader = deps().rubric_loader
    available = getattr(loader, "available", None)
    return list(available()) if callable(available) else []


def _with_role(current, role_id: str):
    return replace(current, role_id=role_id)


main()
