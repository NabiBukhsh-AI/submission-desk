"""Real PDFs, built at test time.

Generated rather than committed, so the repository carries no binary nobody can
read and no document anybody could mistake for a real CV. Every one of these is
invented.
"""

from __future__ import annotations

CLEAN_CV = """Ana Ferreira
Senior Backend Engineer

Experience
Acme Payments, Lead Engineer, 2021 to present.
Owned a multi-service payments backend and its on-call rotation for two years.
Introduced a nightly evaluation suite; releases were blocked when agreement fell
below 0.8 against human labels.

Northwind Logistics, Backend Engineer, 2018 to 2021.
Built the routing service in Python. Reduced p95 latency from 900ms to 210ms.

Education
BSc Computer Science, University of Porto, 2018.
"""

LEFT_COLUMN = """Skills
Python
PostgreSQL
Kubernetes
Observability
Incident response
"""

RIGHT_COLUMN = """Experience
Acme Payments, Lead Engineer.
Owned the payments backend and its on-call rotation.
Shipped a language-model feature used by the support team.
"""

KOREAN_CV = """홍길동
백엔드 엔지니어

경력
에이콤 결제팀, 리드 엔지니어, 2021년부터 현재까지.
결제 백엔드 서비스를 운영하고 온콜 당번을 담당했습니다.
야간 평가 스위트를 도입하여 품질이 기준 아래로 떨어지면 배포를 중단했습니다.
"""


def text_pdf(body: str = CLEAN_CV, *, pages: int = 1) -> bytes:
    """A PDF with a real, extractable text layer."""
    import pymupdf

    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        heading = body if pages == 1 else f"Page {index + 1}\n\n{body}"
        page.insert_textbox(pymupdf.Rect(50, 50, 545, 780), heading, fontsize=10)
    data: bytes = document.tobytes()
    document.close()
    return data


def two_column_pdf() -> bytes:
    """A PDF whose text sits in two visually separated columns.

    Read left to right line by line, the two columns interleave and produce
    sentences that exist in no document. This is the fixture that proves the
    column heuristic is doing something.
    """
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 250, 700), LEFT_COLUMN, fontsize=10)
    page.insert_textbox(pymupdf.Rect(330, 50, 545, 700), RIGHT_COLUMN, fontsize=10)
    data: bytes = document.tobytes()
    document.close()
    return data


def scanned_pdf(*, pages: int = 1) -> bytes:
    """A PDF whose pages carry an image and no text layer.

    Built by drawing a filled rectangle, so the page has content but nothing a
    text extractor can read. This is what triggers the OCR path.
    """
    import pymupdf

    document = pymupdf.open()
    for _ in range(pages):
        page = document.new_page()
        page.draw_rect(pymupdf.Rect(50, 50, 545, 780), color=(0.2, 0.2, 0.2), fill=(0.9, 0.9, 0.9))
    data: bytes = document.tobytes()
    document.close()
    return data


def mixed_pdf() -> bytes:
    """Digital on pages one and two, an image on page three.

    The realistic case: someone appends a scanned certificate to a typed CV.
    Re-reading the whole file through OCR to fix page three would throw away
    good text from pages one and two.
    """
    import pymupdf

    document = pymupdf.open()
    for index in range(2):
        page = document.new_page()
        page.insert_textbox(
            pymupdf.Rect(50, 50, 545, 780), f"Page {index + 1}\n\n{CLEAN_CV}", fontsize=10
        )
    scanned = document.new_page()
    scanned.draw_rect(
        pymupdf.Rect(50, 50, 545, 780), color=(0.2, 0.2, 0.2), fill=(0.85, 0.85, 0.85)
    )
    data: bytes = document.tobytes()
    document.close()
    return data


def long_pdf(pages: int = 40) -> bytes:
    """An academic CV. Long, but within the cap."""
    return text_pdf(pages=pages)


def korean_pdf() -> bytes:
    """A CV in Korean.

    MUST is Korean-headquartered, so this case will be looked at. The system
    processes it, flags it, and reports the assessment quality as unmeasured,
    which is the honest posture when nothing has measured it.
    """
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 545, 780), KOREAN_CV, fontsize=10, fontname="korea")
    data: bytes = document.tobytes()
    document.close()
    return data


def docx_with_tables() -> bytes:
    """A Word CV whose employment history is a table.

    Common, and it reads badly if cells are concatenated without a separator:
    "Senior EngineerAcme2021" is not a sentence anyone can quote.
    """
    import io

    import docx

    document = docx.Document()
    document.add_paragraph("Ana Ferreira")
    document.add_paragraph("Senior Backend Engineer")

    table = document.add_table(rows=3, cols=3)
    rows = [
        ("Employer", "Role", "Dates"),
        ("Acme Payments", "Lead Engineer", "2021 to present"),
        ("Northwind Logistics", "Backend Engineer", "2018 to 2021"),
    ]
    for row, values in zip(table.rows, rows, strict=False):
        for cell, value in zip(row.cells, values, strict=False):
            cell.text = value

    document.add_paragraph(
        "Introduced a nightly evaluation suite; releases were blocked below 0.8 agreement."
    )

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
