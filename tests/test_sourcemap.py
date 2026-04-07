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

    def test_tabbed_content_merged(self):
        """pymdownx tabbed sets are merged into a single block."""
        md = (
            "## Install\n"
            "\n"
            '=== "macOS"\n'
            "\n"
            "    ```bash\n"
            "    brew install foo\n"
            "    ```\n"
            "\n"
            '=== "Windows"\n'
            "\n"
            "    ```powershell\n"
            "    choco install foo\n"
            "    ```\n"
            "\n"
            "After the tabs."
        )
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[0] == Block(start_line=1, end_line=1, content="## Install")
        # The entire tab set is one block, content includes internal blank lines
        assert blocks[1].start_line == 3
        assert blocks[1].end_line == 13
        assert '=== "macOS"' in blocks[1].content
        assert '=== "Windows"' in blocks[1].content
        assert blocks[2] == Block(start_line=15, end_line=15, content="After the tabs.")

    def test_tabbed_content_at_end_of_doc(self):
        """Tab set at end of document (no trailing content) is still merged."""
        md = 'Intro\n\n=== "A"\n\n    Content A\n\n=== "B"\n\n    Content B'
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "Intro"
        assert blocks[1].start_line == 3
        assert blocks[1].end_line == 9

    def test_multiple_tab_sets(self):
        """Two separate tab sets produce two separate merged blocks."""
        md = '=== "X"\n\n    X content\n\nMiddle paragraph\n\n=== "Y"\n\n    Y content'
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[0].start_line == 1
        assert blocks[0].end_line == 3
        assert blocks[1].content == "Middle paragraph"
        assert blocks[2].start_line == 7
        assert blocks[2].end_line == 9

    def test_single_tab_no_merge(self):
        """A lone === header with no subsequent indented content is still one block."""
        md = '=== "Only"\n\nRegular paragraph'
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == '=== "Only"'
        assert blocks[1].content == "Regular paragraph"

    def test_tabbed_with_fenced_code_inside(self):
        """Fenced code blocks inside tabs don't break the merge."""
        md = (
            '=== "Tab 1"\n'
            "\n"
            "    ```python\n"
            "    def foo():\n"
            "        pass\n"
            "\n"
            "        return 42\n"
            "    ```\n"
            "\n"
            '=== "Tab 2"\n'
            "\n"
            "    plain text\n"
            "\n"
            "Done"
        )
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].start_line == 1
        assert blocks[0].end_line == 12
        assert blocks[1].content == "Done"

    def test_tabbed_fence_close_no_blank_before_next_content(self):
        """Content immediately after a tab's closing fence isn't absorbed into the tab block."""
        md = (
            '=== "Tab 1"\n'
            "\n"
            "    ```python\n"
            "    x = 1\n"
            "    ```\n"
            "\n"
            '=== "Tab 2"\n'
            "\n"
            "    ```python\n"
            "    y = 2\n"
            "    ```\n"  # line 11 — fence close, no blank line follows
            "Attribution line\n"  # line 12 — should NOT be in the tab block
            "\n"
            "## Next Section"  # line 14
        )
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        # Tab block ends at fence close (line 11), not at attribution
        assert blocks[0].start_line == 1
        assert blocks[0].end_line == 11
        # Attribution is its own block
        assert blocks[1].start_line == 12
        assert blocks[1].content == "Attribution line"
        # Heading is separate
        assert blocks[2].content == "## Next Section"

    def test_ol_items_separated_by_blank_lines_merged(self):
        """Ordered list items separated by blank lines render as one <ol>."""
        md = "Intro\n\n1. first\n\n\n2. second\n\nAfter"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[0].content == "Intro"
        assert blocks[1].start_line == 3
        assert blocks[1].end_line == 6
        assert "1. first" in blocks[1].content
        assert "2. second" in blocks[1].content
        assert blocks[2].content == "After"

    def test_ul_items_separated_by_blank_lines_merged(self):
        """Unordered list items separated by blank lines render as one <ul>."""
        md = "- apple\n\n- banana\n\n- cherry\n\nDone"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].start_line == 1
        assert blocks[0].end_line == 5
        assert "- apple" in blocks[0].content
        assert "- cherry" in blocks[0].content
        assert blocks[1].content == "Done"

    def test_mixed_ol_ul_not_merged(self):
        """An ordered list followed by an unordered list stays separate."""
        md = "1. ordered\n\n- unordered\n\nEnd"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[0].content == "1. ordered"
        assert blocks[1].content == "- unordered"
        assert blocks[2].content == "End"

    def test_contiguous_list_items_still_one_block(self):
        """List items without blank lines between them are already one block."""
        md = "- a\n- b\n- c\n\nEnd"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0] == Block(start_line=1, end_line=3, content="- a\n- b\n- c")

    def test_html_comment_block_excluded(self):
        """Standalone HTML comments don't produce blocks (no rendered HTML to map to)."""
        md = "# Title\n\n<!-- TODO: fix this -->\n\nSome text"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0] == Block(start_line=1, end_line=1, content="# Title")
        assert blocks[1] == Block(start_line=5, end_line=5, content="Some text")

    def test_multiline_html_comment_excluded(self):
        md = "Before\n\n<!--\nmulti-line\ncomment\n-->\n\nAfter"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "Before"
        assert blocks[1].content == "After"

    def test_consecutive_html_comments_excluded(self):
        md = "Before\n\n<!-- comment 1 -->\n<!-- comment 2 -->\n\nAfter"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "Before"
        assert blocks[1].content == "After"

    def test_inline_html_comment_preserved(self):
        """Comments mixed with real content should keep the block."""
        md = "# Title <!-- note -->\n\nText"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "# Title <!-- note -->"

    def test_html_comments_at_end_of_doc(self):
        md = "# Title\n\nContent\n\n<!-- TODO: something -->\n<!-- TODO: another -->"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "# Title"
        assert blocks[1].content == "Content"


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
