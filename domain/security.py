"""What a reviewer is told about a document's integrity.

The wording lives in the domain rather than in the interface, for the same
reason the rejection messages do: it is reviewable as a set. A second interface
cannot invent a gentler version of the red banner, and a change to what the
system says about a quarantined document is a change to a file somebody reviews
rather than a string in a template.
"""

from __future__ import annotations

from domain.contracts.enums import IntegrityTier

#: What the reviewer reads at the top of the page, per tier.
BANNERS: dict[IntegrityTier, str] = {
    IntegrityTier.CLEAN: "No integrity issues were found in these documents.",
    IntegrityTier.SUSPECT: (
        "This document contains content that a scanner flagged. The assessment "
        "continued and every quotation is verifiable below."
    ),
    IntegrityTier.QUARANTINE: (
        "This document contains content designed to manipulate an automated "
        "reader. No assessment was run and nothing was spent. The flagged text "
        "is quoted in full below."
    ),
}


def banner_for(tier: IntegrityTier) -> str:
    return BANNERS[tier]


def halts_the_run(tier: IntegrityTier) -> bool:
    """Whether the pipeline stops here.

    One function rather than an ``is QUARANTINE`` comparison at each call site,
    so adding a halting tier is one edit and not a search.
    """
    return tier is IntegrityTier.QUARANTINE


def requires_a_person(tier: IntegrityTier) -> bool:
    """Whether a reviewer must see this before anything is sent."""
    return tier is not IntegrityTier.CLEAN
