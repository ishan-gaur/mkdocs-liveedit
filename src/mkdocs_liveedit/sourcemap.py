"""Parse markdown into blank-line-delimited blocks with line ranges.

Each block is a contiguous group of non-empty lines, separated by one or more
blank lines. Fenced code blocks and frontmatter are treated as single blocks
(blank lines inside them don't split).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Block:
    """A markdown block with its 1-indexed line range (inclusive on both ends)."""

    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive
    content: str  # raw markdown text of the block


def count_frontmatter_offset(raw_file_content: str, page_markdown: str) -> int:
    """Compute how many lines the frontmatter + separator occupy at the top of the file.

    MkDocs strips frontmatter from page.markdown, so line numbers in the block map
    need this offset added back to map to actual file lines.
    """
    raw_lines = raw_file_content.split("\n")

    # If file starts with ---, find the closing ---
    if raw_lines and raw_lines[0].strip() == "---":
        for i in range(1, len(raw_lines)):
            if raw_lines[i].strip() == "---":
                # offset = frontmatter lines (0..i inclusive) + possibly a blank line after
                offset = i + 1
                # Skip blank lines between frontmatter end and content start
                while offset < len(raw_lines) and raw_lines[offset].strip() == "":
                    offset += 1
                return offset
    return 0


_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def parse_blocks(markdown: str) -> list[Block]:
    """Parse markdown text into blocks separated by blank lines.

    Respects fenced code blocks (``` or ~~~) — blank lines inside fences
    don't split blocks. Returns blocks with 1-indexed line numbers.
    """
    lines = markdown.split("\n")
    blocks: list[Block] = []

    in_fence = False
    fence_char = ""
    fence_count = 0

    current_start: int | None = None  # 1-indexed
    current_lines: list[str] = []

    def flush():
        nonlocal current_start, current_lines
        if current_start is not None and current_lines:
            # Trim trailing empty lines from block
            while current_lines and current_lines[-1].strip() == "":
                current_lines.pop()
            if current_lines:
                content = "\n".join(current_lines)
                end_line = current_start + len(current_lines) - 1
                blocks.append(Block(start_line=current_start, end_line=end_line, content=content))
        current_start = None
        current_lines = []

    for i, line in enumerate(lines):
        line_num = i + 1  # 1-indexed

        # Check for fence open/close
        m = _FENCE_RE.match(line.strip())
        if m:
            char = m.group(1)[0]
            count = len(m.group(1))
            if not in_fence:
                in_fence = True
                fence_char = char
                fence_count = count
                # This fence line starts or continues a block
                if current_start is None:
                    current_start = line_num
                current_lines.append(line)
                continue
            elif char == fence_char and count >= fence_count:
                in_fence = False
                current_lines.append(line)
                continue

        if in_fence:
            if current_start is None:
                current_start = line_num
            current_lines.append(line)
            continue

        # Outside fence: blank line = block separator
        if line.strip() == "":
            flush()
        else:
            if current_start is None:
                current_start = line_num
            current_lines.append(line)

    flush()
    return blocks
