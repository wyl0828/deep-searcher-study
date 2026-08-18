"""Shared table text rendering (aligned with ragent TableChunker).

- render_markdown_table: the display/citation text (positional markdown table).
- render_key_value_rows: the embedding text (``column: value`` rows), because
  markdown alignment is meaningless to embedding models.
"""

from __future__ import annotations

from typing import List


def one_line(value: str) -> str:
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def sanitize_cell(value: str) -> str:
    return (
        value.replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


def render_markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    def append_row(cells: List[str]) -> str:
        return "| " + " | ".join(sanitize_cell(cell) for cell in cells) + " |"

    lines = [append_row(headers)]
    lines.append("|" + "---|" * max(len(headers), 0))
    lines.extend(append_row(row) for row in rows)
    return "\n".join(lines)


def render_key_value_row(headers: List[str], row: List[str]) -> str:
    parts: List[str] = []
    for index, value in enumerate(row):
        if value is None or value == "":
            continue
        key = headers[index] if index < len(headers) else ""
        if key:
            parts.append(f"{one_line(key)}: {one_line(value)}")
        else:
            parts.append(one_line(value))
    return "; ".join(parts)


def render_key_value_rows(headers: List[str], rows: List[List[str]]) -> str:
    lines = [line for line in (render_key_value_row(headers, row) for row in rows) if line]
    return "\n".join(lines)
