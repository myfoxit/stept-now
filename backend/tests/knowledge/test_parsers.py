"""Parser unit tests: pdf, docx, html, markdown, csv, txt, unsupported."""

from __future__ import annotations

import io

import pytest

from app.rag.parsers import ParseError, extract


def build_pdf(text: str) -> bytes:
    """Hand-rolled minimal single-page PDF with a correct xref table."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n",
        b"4 0 obj << /Length %d >> stream\n%s\nendstream endobj\n" % (len(stream), stream),
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in objects:
        offsets.append(len(out))
        out += obj
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_pos}\n%%EOF".encode()
    return bytes(out)


def test_pdf_extraction():
    parsed = extract("refund-policy.pdf", build_pdf("Refunds are issued within 30 days."))
    assert "Refunds are issued within 30 days." in parsed.text
    assert parsed.title == "refund policy"
    assert parsed.meta["pages"] == 1
    assert parsed.meta["mime"] == "application/pdf"


def test_pdf_corrupt_raises():
    with pytest.raises(ParseError):
        extract("broken.pdf", b"%PDF-1.4 not really a pdf")


def test_docx_extraction_with_headings():
    import docx

    document = docx.Document()
    document.add_heading("Billing guide", level=1)
    document.add_paragraph("We accept all major credit cards.")
    document.add_heading("Refunds", level=2)
    document.add_paragraph("Full refund within 30 days.")
    buffer = io.BytesIO()
    document.save(buffer)

    parsed = extract("billing.docx", buffer.getvalue())
    assert parsed.title == "Billing guide"
    assert "# Billing guide" in parsed.text
    assert "## Refunds" in parsed.text
    assert "Full refund within 30 days." in parsed.text


def test_html_headings_preserved_as_markdown():
    html = b"""
    <html><head><title>Widget install guide</title></head><body>
      <nav>Skip me not required</nav>
      <h1>Install the widget</h1>
      <p>Copy the embed snippet.</p>
      <h2>Verify identity</h2>
      <p>Sign the user id with HMAC.</p>
      <ul><li>Step one</li><li>Step two</li></ul>
      <script>var ignored = true;</script>
    </body></html>
    """
    parsed = extract("guide.html", html, "text/html")
    assert parsed.title == "Widget install guide"
    assert "# Install the widget" in parsed.text
    assert "## Verify identity" in parsed.text
    assert "- Step one" in parsed.text
    assert "ignored" not in parsed.text


def test_markdown_title_from_first_heading():
    parsed = extract("notes.md", b"intro line\n\n# Real Title\n\nBody text here.")
    assert parsed.title == "Real Title"
    assert "Body text here." in parsed.text


def test_txt_title_from_filename():
    parsed = extract("release_notes.txt", b"Version 2 ships tomorrow.")
    assert parsed.title == "release notes"
    assert parsed.text == "Version 2 ships tomorrow."


def test_csv_becomes_markdown_table_capped():
    rows = ["name,plan"] + [f"user{i},pro" for i in range(250)]
    parsed = extract("customers.csv", "\n".join(rows).encode())
    assert parsed.text.startswith("| name | plan |")
    assert "| user0 | pro |" in parsed.text
    assert "| user199 | pro |" in parsed.text
    assert "| user200 | pro |" not in parsed.text  # capped at 200 data rows
    assert parsed.meta["truncated"] is True
    assert parsed.meta["rows"] == 250


def test_unsupported_type_raises():
    with pytest.raises(ParseError):
        extract("binary.bin", b"\x00\x01\x02", "application/octet-stream")
