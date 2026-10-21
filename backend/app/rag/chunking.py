"""Chunking for markdown / text documents (heading-aware, size-bounded)."""

from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")


def chunk_markdown(text: str, max_chars: int = 1400, overlap: int = 150) -> list[tuple[str, str]]:
    """Return [(section_path, chunk_text)]. Splits on headings first, then by paragraph to max_chars."""
    sections: list[tuple[list[str], list[str]]] = []
    path: list[str] = []
    buf: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            if any(b.strip() for b in buf):
                sections.append((list(path), buf))
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2).strip()]
            buf = []
        else:
            buf.append(line)
    if any(b.strip() for b in buf):
        sections.append((list(path), buf))

    chunks: list[tuple[str, str]] = []
    for p, lines in sections:
        body = "\n".join(lines).strip()
        title = " > ".join(p) or "document"
        if len(body) <= max_chars:
            chunks.append((title, body))
            continue
        cur = ""
        for para in re.split(r"\n\s*\n", body):
            if cur and len(cur) + len(para) + 2 > max_chars:
                chunks.append((title, cur.strip()))
                cur = cur[-overlap:] if overlap else ""
            cur += para + "\n\n"
            while len(cur) > max_chars * 1.5:  # a single huge paragraph / code block
                chunks.append((title, cur[:max_chars].strip()))
                cur = cur[max_chars - overlap :]
        if cur.strip():
            chunks.append((title, cur.strip()))
    return chunks
