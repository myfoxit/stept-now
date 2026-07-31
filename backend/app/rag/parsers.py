"""Content extraction: bytes → ParsedDoc(title, text, meta).

Supported: pdf (pypdf), docx (python-docx), html (bs4 — headings preserved as
"## " markdown lines), md/txt passthrough, csv → markdown table (capped 200
rows). Unsupported or corrupt input raises `ParseError`; callers surface it as
a 400 (uploads) or a failed document (background ingestion) — never a crash.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

CSV_MAX_ROWS = 200

_EXTENSION_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
}

SUPPORTED_MIMES = frozenset(_EXTENSION_MIME.values())


class ParseError(Exception):
    """Raised when content cannot be extracted (unsupported or corrupt)."""


@dataclass
class ParsedDoc:
    title: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def _title_from_filename(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").strip() or "Untitled"


def _decode(content: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("Could not decode text content")


def normalize_mime(filename: str, mime: str | None) -> str | None:
    """Best-effort mime resolution: declared mime first, else extension."""
    if mime:
        mime = mime.split(";")[0].strip().lower()
        if mime in SUPPORTED_MIMES:
            return mime
        # Some browsers send text/x-markdown or application/octet-stream.
        if mime in ("text/x-markdown", "application/x-markdown"):
            return "text/markdown"
    lowered = filename.lower()
    for extension, extension_mime in _EXTENSION_MIME.items():
        if lowered.endswith(extension):
            return extension_mime
    return None


def _extract_pdf(filename: str, content: bytes) -> ParsedDoc:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise ParseError(f"Could not read PDF: {exc}") from exc
    title = None
    if reader.metadata is not None and reader.metadata.title:
        title = str(reader.metadata.title).strip() or None
    text = "\n\n".join(page.strip() for page in pages if page.strip())
    return ParsedDoc(
        title=title or _title_from_filename(filename),
        text=text,
        meta={"pages": len(reader.pages)},
    )


def _extract_docx(filename: str, content: bytes) -> ParsedDoc:
    import docx

    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:
        raise ParseError(f"Could not read DOCX: {exc}") from exc
    lines: list[str] = []
    title: str | None = None
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        match = re.match(r"^Heading (\d)$", style)
        if style == "Title" or match:
            level = 1 if style == "Title" else min(int(match.group(1)), 6)  # type: ignore[union-attr]
            if title is None and level == 1:
                title = text
            lines.append(f"{'#' * level} {text}")
        else:
            lines.append(text)
    return ParsedDoc(
        title=title or _title_from_filename(filename), text="\n\n".join(lines), meta={}
    )


def _extract_html(filename: str, content: bytes) -> ParsedDoc:
    from bs4 import BeautifulSoup
    from bs4.element import Tag

    try:
        soup = BeautifulSoup(content, "lxml")
    except Exception as exc:
        raise ParseError(f"Could not parse HTML: {exc}") from exc
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()

    title = None
    if soup.title is not None and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True)

    lines: list[str] = []
    root = soup.body if soup.body is not None else soup
    for element in root.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "td", "th"]
    ):
        if not isinstance(element, Tag):
            continue
        # Skip containers whose text is fully covered by nested matched elements.
        if element.find(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]) is not None:
            continue
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name and element.name.startswith("h") and len(element.name) == 2:
            level = int(element.name[1])
            if title is None and level == 1:
                title = text
            lines.append(f"{'#' * level} {text}")
        elif element.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    if not lines:  # e.g. text-only html
        text = soup.get_text(" ", strip=True)
        if text:
            lines.append(text)
    return ParsedDoc(
        title=title or _title_from_filename(filename), text="\n\n".join(lines), meta={}
    )


def _extract_markdown(filename: str, content: bytes) -> ParsedDoc:
    text = _decode(content).strip()
    title = None
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            title = match.group(1).strip()
            break
    return ParsedDoc(title=title or _title_from_filename(filename), text=text, meta={})


def _extract_txt(filename: str, content: bytes) -> ParsedDoc:
    return ParsedDoc(title=_title_from_filename(filename), text=_decode(content).strip(), meta={})


def _extract_csv(filename: str, content: bytes) -> ParsedDoc:
    try:
        rows = list(csv.reader(io.StringIO(_decode(content))))
    except csv.Error as exc:
        raise ParseError(f"Could not parse CSV: {exc}") from exc
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ParsedDoc(title=_title_from_filename(filename), text="", meta={"rows": 0})
    truncated = len(rows) > CSV_MAX_ROWS + 1
    header, body = rows[0], rows[1 : CSV_MAX_ROWS + 1]

    def _md_row(row: list[str]) -> str:
        return "| " + " | ".join(cell.strip().replace("|", "\\|") for cell in row) + " |"

    lines = [_md_row(header), "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend(_md_row(row) for row in body)
    if truncated:
        lines.append(f"\n… truncated to the first {CSV_MAX_ROWS} rows of {len(rows) - 1}.")
    return ParsedDoc(
        title=_title_from_filename(filename),
        text="\n".join(lines),
        meta={"rows": len(rows) - 1, "truncated": truncated},
    )


_EXTRACTORS = {
    "application/pdf": _extract_pdf,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _extract_docx,
    "text/html": _extract_html,
    "text/markdown": _extract_markdown,
    "text/plain": _extract_txt,
    "text/csv": _extract_csv,
}


def extract(filename: str, content: bytes, mime: str | None = None) -> ParsedDoc:
    """Extract title + plain-ish markdown text from raw file bytes."""
    resolved = normalize_mime(filename, mime)
    if resolved is None:
        raise ParseError(f"Unsupported file type: {mime or filename}")
    parsed = _EXTRACTORS[resolved](filename, content)
    parsed.meta["mime"] = resolved
    return parsed
