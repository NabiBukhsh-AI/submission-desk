"""Reading order for pages that are not one column.

A two-column CV read left to right, line by line, interleaves two unrelated
sentences and produces text no quotation can be matched against. The fix is
geometric, not learned: cluster blocks by the x-centre of their bounding box,
call each cluster a column, and read columns left to right, each top to bottom.

No layout model. A heuristic a person can predict and debug beats one that is
right more often but wrong unpredictably, because a wrong reading order is
invisible until a span fails to validate three stages later.
"""

from __future__ import annotations

#: Fraction of page width that separates two columns. Blocks whose centres are
#: closer together than this belong to the same column. Wider, and a two-column
#: layout reads as one; narrower, and an indented paragraph becomes a column.
COLUMN_GAP_RATIO = 0.15

Block = tuple[float, float, float, float, str]


def detect_columns(blocks: tuple[Block, ...], page_width: float) -> list[list[Block]]:
    """Group blocks into columns by the x-centre of each.

    Returns one list per column, ordered left to right. A page whose blocks all
    sit at similar x-centres comes back as a single column, which is the common
    case and costs nothing.
    """
    if not blocks or page_width <= 0:
        return [list(blocks)]

    threshold = page_width * COLUMN_GAP_RATIO
    ordered = sorted(blocks, key=lambda block: (block[0] + block[2]) / 2)

    columns: list[list[Block]] = [[ordered[0]]]
    for block in ordered[1:]:
        centre = (block[0] + block[2]) / 2
        previous = columns[-1][-1]
        previous_centre = (previous[0] + previous[2]) / 2
        if centre - previous_centre > threshold:
            columns.append([block])
        else:
            columns[-1].append(block)
    return columns


def reading_order(blocks: tuple[Block, ...], page_width: float) -> list[Block]:
    """Blocks in the order a person would read them.

    Columns left to right, each top to bottom. Ties on the vertical axis break
    on the horizontal one, so two blocks on the same line keep their
    left-to-right order rather than being ordered arbitrarily.
    """
    columns = detect_columns(blocks, page_width)
    ordered: list[Block] = []
    for column in columns:
        ordered.extend(sorted(column, key=lambda block: (round(block[1], 1), block[0])))
    return ordered


def page_text(blocks: tuple[Block, ...], page_width: float) -> str:
    """The text of one page, in reading order."""
    return "\n".join(
        block[4].strip() for block in reading_order(blocks, page_width) if block[4].strip()
    )


def block_boundaries(
    blocks: tuple[Block, ...], page_width: float, offset: int
) -> list[tuple[int, int]]:
    """Where each block starts and ends in the assembled text.

    Persisted so the interface can highlight the paragraph around a quotation
    rather than the quotation alone, which gives the reviewer the context they
    need to judge it.
    """
    boundaries: list[tuple[int, int]] = []
    cursor = offset
    for block in reading_order(blocks, page_width):
        content = block[4].strip()
        if not content:
            continue
        boundaries.append((cursor, cursor + len(content)))
        cursor += len(content) + 1
    return boundaries
