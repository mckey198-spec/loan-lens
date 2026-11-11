"""Lexical markdown chunking by heading boundaries (Architecture §9; no embeddings)."""

from __future__ import annotations

from modules.policy.schemas import PolicyChunkDraft

_HEADING_PREFIXES: tuple[str, ...] = ("# ", "## ", "### ")


def chunk_policy_markdown(markdown_text: str) -> list[PolicyChunkDraft]:
    """
    Split markdown into lexical chunks on `# `, `## `, and `### ` headings.

    `section_label` is the heading text (without `#` markers).
    `content_text` is the body until the next heading (or end of document).
    Preamble text before the first heading is ignored.
    """
    if not markdown_text:
        return []

    sections: list[tuple[str, list[str]]] = []
    current_label: str | None = None
    current_body: list[str] = []

    for line in markdown_text.splitlines():
        heading_label = _heading_label(line)
        if heading_label is not None:
            if current_label is not None:
                sections.append((current_label, current_body))
            current_label = heading_label
            current_body = []
            continue
        if current_label is not None:
            current_body.append(line)

    if current_label is not None:
        sections.append((current_label, current_body))

    return [
        PolicyChunkDraft(
            section_label=label,
            content_text="\n".join(body).strip(),
        )
        for label, body in sections
    ]


def _heading_label(line: str) -> str | None:
    for prefix in _HEADING_PREFIXES:
        if line.startswith(prefix):
            label = line[len(prefix) :].strip()
            if label:
                return label
            return None
    return None
