"""Adding candidates.

Grouping several files into one candidate is a guess. Filenames are how people
name things, not how systems identify them, so the guess is shown as a table the
recruiter can correct before anything is processed. A system that grouped
silently and got it wrong would assess half a submission and call it whole.

The limits are stated before the upload rather than after it. "This file is too
large" is a worse message than "files up to 20 MB".
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import streamlit as st

from app.main import deps, state
from app.state import UploadDraft, put
from application.use_cases.process_batch import process_batch
from domain.ports.sources import CandidateRef, DocumentRef

#: What intake will accept. Stated here so the recruiter reads it before
#: choosing files, and read from intake rather than retyped.
MAX_FILE_MB = 20
MAX_DOCUMENTS_PER_CANDIDATE = 6

#: How a candidate id is guessed from a filename. Everything before the first
#: underscore, hyphen-space, or "cv"/"resume" marker.
SPLIT = re.compile(r"[_\-\s]+")


def guess_candidate(filename: str) -> str:
    """Which candidate a file probably belongs to.

    Deliberately simple, and deliberately shown to the recruiter afterwards.
    A cleverer heuristic would be wrong less often and harder to correct, which
    is the worse trade: this one is wrong visibly.
    """
    stem = Path(filename).stem.lower()
    parts = [part for part in SPLIT.split(stem) if part]
    meaningful = [
        part
        for part in parts
        if part
        not in ("cv", "resume", "resumé", "cover", "letter", "portfolio", "final", "v1", "v2")
    ]
    return "-".join(meaningful[:2]) or stem or "candidate"


def group(files: list) -> dict[str, list[tuple[str, bytes]]]:
    grouped: dict[str, list[tuple[str, bytes]]] = {}
    for uploaded in files:
        grouped.setdefault(guess_candidate(uploaded.name), []).append(
            (uploaded.name, uploaded.getvalue())
        )
    return grouped


def render() -> None:
    current = state()
    st.header("Add candidates")

    if deps().settings.demo_mode:
        # The one place a real document could enter a demo. Refused here, and
        # refused at the composition root for every other path in.
        st.info(
            "Demo mode is on, so uploads are switched off and only the synthetic "
            "candidates are read. Unset DEMO_MODE to process real documents.",
            icon="🔒",
        )
        return

    st.caption(
        f"PDF, Word, or plain text. Up to {MAX_FILE_MB} MB per file and "
        f"{MAX_DOCUMENTS_PER_CANDIDATE} documents per candidate. "
        "Files are stored by their contents, never by their filename."
    )

    files = st.file_uploader(
        "Choose files",
        accept_multiple_files=True,
        type=["pdf", "docx", "txt", "md"],
    )

    if not files:
        st.info(
            "Drag CVs here, or use the folder that is already configured as a source.",
            icon="📄",
        )
        return

    grouped = group(list(files))
    st.subheader("Check the grouping")
    st.caption(
        "One row per file. Change a candidate name if two files belong to the same "
        "person, or if a name was guessed wrongly."
    )

    corrected: dict[str, list[tuple[str, bytes]]] = {}
    for candidate_id, documents in grouped.items():
        for filename, payload in documents:
            name_column, file_column = st.columns([1, 2])
            with name_column:
                chosen = st.text_input(
                    "Candidate",
                    value=candidate_id,
                    key=f"group-{filename}",
                    label_visibility="collapsed",
                )
            with file_column:
                st.caption(f"{filename} · {len(payload) / 1024:.0f} KB")
            corrected.setdefault(chosen.strip() or candidate_id, []).append((filename, payload))

    draft = UploadDraft(grouped=corrected, role_id=current.role_id)
    st.markdown(f"**{draft.candidate_count} candidate(s), {draft.document_count} document(s).**")

    oversized = [
        filename
        for documents in corrected.values()
        for filename, payload in documents
        if len(payload) > MAX_FILE_MB * 1024 * 1024
    ]
    if oversized:
        st.error(
            "These files are larger than this system accepts: " + ", ".join(oversized),
            icon="🛑",
        )
        return

    if st.button("Process these candidates", type="primary"):
        _process(draft)


def _process(draft: UploadDraft) -> None:
    """Hand the files to the batch use case and report what happened."""
    wired = deps()
    inbox = Path(wired.settings.blob_dir).parent / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    candidates = []
    for candidate_id, documents in draft.grouped.items():
        refs: list[DocumentRef] = []
        for filename, payload in documents:
            # Written under the candidate id rather than the uploaded name: a
            # filename from outside never decides where bytes land.
            target = inbox / f"{candidate_id}-{len(refs)}{Path(filename).suffix}"
            target.write_bytes(payload)
            refs.append(
                DocumentRef(
                    candidate_id=candidate_id,
                    filename=filename,
                    external_ref=str(target),
                    size_bytes=len(payload),
                )
            )
        candidates.append(CandidateRef(candidate_id=candidate_id, documents=tuple(refs)))

    progress = st.progress(0.0, text="Starting")

    def tick(done: int, total: int, candidate_id: str) -> None:
        progress.progress(
            done / total if total else 1.0,
            text=f"{done} of {total} — {candidate_id}" if candidate_id else "Finishing",
        )

    summary = process_batch(candidates, draft.role_id, wired, on_progress=tick)
    progress.empty()

    st.success(summary.sentence())
    for candidate_id, reason in summary.failures:
        st.warning(f"{candidate_id}: {reason}", icon="⚠️")

    put(st.session_state, replace(state(), upload=UploadDraft()))
    st.page_link("pages/2_Queue.py", label="Go to the queue", icon="📋")


render()
