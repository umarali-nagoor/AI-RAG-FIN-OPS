"""Unified diff parsing and application."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    new_start: int
    lines: list[tuple[str, str]] = field(default_factory=list)  # (' ' | '+' | '-', text)


@dataclass
class FileDiff:
    path: str
    old_path: str | None
    is_new: bool = False
    is_deleted: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added_lines(self) -> set[int]:
        out: set[int] = set()
        for h in self.hunks:
            n = h.new_start
            for op, _ in h.lines:
                if op == "+":
                    out.add(n)
                    n += 1
                elif op == " ":
                    n += 1
        return out

    @property
    def removed_lines(self) -> set[int]:
        out: set[int] = set()
        for h in self.hunks:
            o = h.old_start
            for op, _ in h.lines:
                if op == "-":
                    out.add(o)
                    o += 1
                elif op == " ":
                    o += 1
        return out


def _strip(path: str) -> str | None:
    path = path.strip().split("\t")[0]
    if path == "/dev/null":
        return None
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_path: str | None = None
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            current, hunk, old_path = None, None, None
            m = re.match(r"diff --git a/(.+?) b/(.+)$", raw)
            if m:
                old_path = m.group(1)
                current = FileDiff(path=m.group(2), old_path=m.group(1))
                files.append(current)
        elif raw.startswith("--- ") and hunk is None:
            old_path = _strip(raw[4:])
            if current is None:  # plain `diff -u` without a git header
                current = FileDiff(path=old_path or "", old_path=old_path)
                files.append(current)
            current.old_path = old_path
            current.is_new = old_path is None
        elif raw.startswith("+++ ") and hunk is None and current is not None:
            new_path = _strip(raw[4:])
            current.is_deleted = new_path is None
            if new_path:
                current.path = new_path
        elif raw.startswith("new file mode") and current is not None:
            current.is_new = True
        elif raw.startswith("deleted file mode") and current is not None:
            current.is_deleted = True
        elif (m := _HUNK.match(raw)) and current is not None:
            hunk = Hunk(old_start=int(m.group(1)), new_start=int(m.group(3)))
            current.hunks.append(hunk)
        elif hunk is not None and raw[:1] in ("+", "-", " "):
            hunk.lines.append((raw[0], raw[1:]))
        elif hunk is not None and raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        elif hunk is not None and raw == "":
            hunk.lines.append((" ", ""))
    return [f for f in files if f.path]


def apply_hunks(base: str, hunks: list[Hunk]) -> str | None:
    """Apply hunks to the base text. Returns None when the base does not match the diff context."""
    src = base.splitlines()
    out: list[str] = []
    pos = 0
    for h in hunks:
        start = max(h.old_start - 1, 0)
        if start < pos or start > len(src):
            return None
        out.extend(src[pos:start])
        pos = start
        for op, text in h.lines:
            if op in (" ", "-"):
                if pos >= len(src) or src[pos].rstrip() != text.rstrip():
                    return None
                if op == " ":
                    out.append(src[pos])
                pos += 1
            else:
                out.append(text)
    out.extend(src[pos:])
    return "\n".join(out) + "\n"


def reconstruct_from_hunks(hunks: list[Hunk]) -> str:
    """New-side text of hunks only, padded with blank lines so reported line numbers match the real file."""
    out: list[str] = []
    for h in hunks:
        while len(out) < h.new_start - 1:
            out.append("")
        out.extend(text for op, text in h.lines if op in (" ", "+"))
    return "\n".join(out) + "\n"


def reconstruct_old_from_hunks(hunks: list[Hunk]) -> str:
    """Old-side text of hunks only, padded so reported line numbers match the real file."""
    out: list[str] = []
    for h in hunks:
        while len(out) < h.old_start - 1:
            out.append("")
        out.extend(text for op, text in h.lines if op in (" ", "-"))
    return "\n".join(out) + "\n"
