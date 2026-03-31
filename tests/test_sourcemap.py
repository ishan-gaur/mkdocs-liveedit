"""Tests for markdown block parsing and line-range mapping."""

from mkdocs_liveedit.sourcemap import Block, count_frontmatter_offset, parse_blocks


class TestParseBlocks:
    def test_simple_paragraphs(self):
        md = "Hello world\n\nSecond paragraph\n\nThird one"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[0] == Block(start_line=1, end_line=1, content="Hello world")
        assert blocks[1] == Block(start_line=3, end_line=3, content="Second paragraph")
        assert blocks[2] == Block(start_line=5, end_line=5, content="Third one")

    def test_multiline_paragraph(self):
        md = "Line one\nLine two\nLine three\n\nNext block"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0] == Block(start_line=1, end_line=3, content="Line one\nLine two\nLine three")
        assert blocks[1] == Block(start_line=5, end_line=5, content="Next block")

    def test_heading_and_paragraph(self):
        md = "# Title\n\nSome text here.\n\n## Subtitle\n\nMore text."
        blocks = parse_blocks(md)
        assert len(blocks) == 4
        assert blocks[0].content == "# Title"
        assert blocks[1].content == "Some text here."
        assert blocks[2].content == "## Subtitle"
        assert blocks[3].content == "More text."

    def test_fenced_code_block_preserves_blank_lines(self):
        md = "Before\n\n```python\ndef foo():\n    pass\n\n    return 42\n```\n\nAfter"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[0] == Block(start_line=1, end_line=1, content="Before")
        assert blocks[1] == Block(
            start_line=3,
            end_line=8,
            content="```python\ndef foo():\n    pass\n\n    return 42\n```",
        )
        assert blocks[2] == Block(start_line=10, end_line=10, content="After")

    def test_tilde_fenced_code_block(self):
        md = "Before\n\n~~~\ncode\n\nmore code\n~~~\n\nAfter"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[1].content == "~~~\ncode\n\nmore code\n~~~"

    def test_list_block(self):
        md = "# List\n\n- item 1\n- item 2\n- item 3\n\nDone"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[1] == Block(start_line=3, end_line=5, content="- item 1\n- item 2\n- item 3")

    def test_multiple_blank_lines(self):
        md = "Block A\n\n\n\nBlock B"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "Block A"
        assert blocks[1].content == "Block B"

    def test_empty_input(self):
        assert parse_blocks("") == []

    def test_only_blank_lines(self):
        assert parse_blocks("\n\n\n") == []

    def test_single_block(self):
        md = "Just one block"
        blocks = parse_blocks(md)
        assert len(blocks) == 1
        assert blocks[0] == Block(start_line=1, end_line=1, content="Just one block")

    def test_blockquote(self):
        md = "> Quote line 1\n> Quote line 2\n\nNormal text"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "> Quote line 1\n> Quote line 2"

    def test_nested_fence_markers(self):
        md = "Text\n\n````\n```\ninner\n```\n````\n\nEnd"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[1].content == "````\n```\ninner\n```\n````"

    def test_indented_code_in_list(self):
        """Indented content within a list is part of the same block (no blank-line split outside fence)."""
        md = "- item 1\n  continued\n- item 2\n\nParagraph"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].start_line == 1
        assert blocks[0].end_line == 3

    def test_trailing_newlines(self):
        md = "Block 1\n\nBlock 2\n\n"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "Block 1"
        assert blocks[1].content == "Block 2"


class TestFrontmatterOffset:
    def test_no_frontmatter(self):
        raw = "# Hello\n\nWorld"
        page_md = "# Hello\n\nWorld"
        assert count_frontmatter_offset(raw, page_md) == 0

    def test_simple_frontmatter(self):
        raw = "---\ntitle: Test\n---\n\n# Hello\n\nWorld"
        page_md = "# Hello\n\nWorld"
        offset = count_frontmatter_offset(raw, page_md)
        assert offset == 4  # lines 1-3 are frontmatter, line 4 is blank

    def test_frontmatter_no_blank_line_after(self):
        raw = "---\ntitle: Test\n---\n# Hello"
        page_md = "# Hello"
        offset = count_frontmatter_offset(raw, page_md)
        assert offset == 3

    def test_multiline_frontmatter(self):
        raw = "---\ntitle: Test\nauthor: Me\ntags:\n  - one\n  - two\n---\n\nContent"
        page_md = "Content"
        offset = count_frontmatter_offset(raw, page_md)
        assert offset == 8
