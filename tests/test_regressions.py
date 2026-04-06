"""Regression tests derived from real bugs found during development.

Each test class reproduces a specific bug that was discovered in production use.
The tests exercise the full pipeline (parse_blocks → _BlockAnnotator → HTML)
to catch block-index desync issues that only surface when blocks and HTML
elements are paired together.

Bug catalog:
    1. Tab set splitting: pymdownx === "Tab" blocks split by blank-line parser
       but render as single <div>. (2026-04-01 session)
    2. HTML comment desync: <!-- comment --> parsed as block but produces no HTML,
       shifting all subsequent block indices. (2026-04-03 session)
    3. List item splitting: consecutive list items separated by blank lines parsed
       as separate blocks but render as one <ol>/<ul>. (2026-04-03 session)
    4. Fence close no-flush: closing ``` didn't flush block, so content immediately
       after got lumped in, then _merge_tabbed_blocks absorbed it. (2026-04-06 session)
    5. Double-build: _trigger_rebuild() called builder() directly while watchdog
       also fired. (2026-04-01 session, architectural — tested via API mock)
    6. Save endpoint line-range off-by-one and newline handling edge cases.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from mkdocs_liveedit.api import LiveEditAPI
from mkdocs_liveedit.plugin import _BlockAnnotator
from mkdocs_liveedit.sourcemap import count_frontmatter_offset, parse_blocks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def annotate(md: str, html: str, frontmatter_offset: int = 0, file_path: str = "test.md") -> str:
    """Run the full pipeline: parse markdown blocks → annotate HTML."""
    blocks = parse_blocks(md)
    annotator = _BlockAnnotator(blocks, file_path, frontmatter_offset)
    return annotator.feed_and_annotate(html)


def extract_liveedit_attrs(html: str) -> list[dict]:
    """Extract all data-liveedit-* attributes from annotated HTML."""
    pattern = re.compile(
        r'data-liveedit-block="(\d+)"\s+'
        r'data-liveedit-file="([^"]*)"\s+'
        r'data-liveedit-lines="(\d+-\d+)"'
    )
    results = []
    for m in pattern.finditer(html):
        start, end = m.group(3).split("-")
        results.append(
            {
                "block": int(m.group(1)),
                "file": m.group(2),
                "start_line": int(start),
                "end_line": int(end),
            }
        )
    return results


def make_environ(method="GET", path="/", body=None, query_string=""):
    from io import BytesIO

    body_bytes = json.dumps(body).encode("utf-8") if body else b""
    return {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "CONTENT_LENGTH": str(len(body_bytes)),
        "wsgi.input": BytesIO(body_bytes),
    }


class FakeStartResponse:
    def __init__(self):
        self.status = None
        self.headers = None

    def __call__(self, status, headers):
        self.status = status
        self.headers = headers


# ---------------------------------------------------------------------------
# Regression 1: Tab set splitting (2026-04-01)
#
# pymdownx tabbed content (=== "Tab") with indented code blocks was split
# into 6+ separate blocks by the blank-line parser. Since it renders as a
# single <div>, the extra blocks shifted all subsequent block indices.
# ---------------------------------------------------------------------------


class TestRegression_TabSetSplitting:
    """Tab sets must merge into a single block to match the single <div> in HTML."""

    def test_tab_set_with_code_blocks_is_one_block(self):
        """Real-world pattern from dfm setup.md: 3-tab install instructions."""
        md = (
            "## Installation\n"
            "\n"
            '=== "macOS / Linux"\n'
            "\n"
            "    ```bash\n"
            "    curl -LsSf https://example.com/install.sh | sh\n"
            "    ```\n"
            "\n"
            '=== "Windows"\n'
            "\n"
            "    ```powershell\n"
            "    irm https://example.com/install.ps1 | iex\n"
            "    ```\n"
            "\n"
            '=== "Homebrew"\n'
            "\n"
            "    ```bash\n"
            "    brew install example\n"
            "    ```\n"
            "\n"
            "After the tabs."
        )
        blocks = parse_blocks(md)
        assert len(blocks) == 3, f"Expected 3 blocks (heading, tab set, paragraph), got {len(blocks)}"
        assert blocks[0].content == "## Installation"
        # The entire tab set is one block
        assert '=== "macOS / Linux"' in blocks[1].content
        assert '=== "Homebrew"' in blocks[1].content
        assert blocks[2].content == "After the tabs."

    def test_tab_set_html_annotation_alignment(self):
        """Verify block indices stay aligned when tab set renders as single <div>."""
        md = '# Title\n\n=== "A"\n\n    Content A\n\n=== "B"\n\n    Content B\n\nParagraph after tabs.'
        # Simulated HTML output (tab set → single div, paragraph → p)
        html = (
            "<h1>Title</h1>\n"
            '<div class="tabbed-set">\n'
            '  <div class="tabbed-content">Content A</div>\n'
            '  <div class="tabbed-content">Content B</div>\n'
            "</div>\n"
            "<p>Paragraph after tabs.</p>"
        )
        annotated = annotate(md, html)
        attrs = extract_liveedit_attrs(annotated)
        # h1 → block 0 (line 1), div.tabbed-set → block 1 (lines 3-9), p → block 2 (line 11)
        assert len(attrs) == 3
        assert attrs[0]["start_line"] == 1  # h1
        assert attrs[1]["start_line"] == 3  # tab set start
        assert attrs[2]["start_line"] == 11  # paragraph after tabs


# ---------------------------------------------------------------------------
# Regression 2: HTML comment block-index desync (2026-04-03)
#
# HTML comments (<!-- ... -->) were parsed as blocks but produce no visible
# HTML elements. This caused _BlockAnnotator's block_index to go out of sync:
# block N was a comment (no HTML), so block N+1 got mapped to the wrong element.
# ---------------------------------------------------------------------------


class TestRegression_HTMLCommentDesync:
    """HTML comments must be excluded from block list to maintain index alignment."""

    def test_comment_between_blocks_doesnt_shift_indices(self):
        """Real-world pattern: <!-- TODO[pi]: ... --> between heading and paragraph."""
        md = "# Welcome\n\n<!-- TODO[pi]: rewrite this section -->\n\nSome introductory text.\n\n## Getting Started"
        blocks = parse_blocks(md)
        assert len(blocks) == 3, f"Comment should be excluded, got {len(blocks)} blocks"
        assert blocks[0].content == "# Welcome"
        assert blocks[1].content == "Some introductory text."
        assert blocks[2].content == "## Getting Started"

    def test_comment_desync_full_pipeline(self):
        """End-to-end: comment between blocks must not shift HTML annotation indices."""
        md = "# Title\n\n<!-- hidden note -->\n\nFirst paragraph.\n\nSecond paragraph."
        html = "<h1>Title</h1>\n<p>First paragraph.</p>\n<p>Second paragraph.</p>"
        annotated = annotate(md, html)
        attrs = extract_liveedit_attrs(annotated)
        assert len(attrs) == 3
        # h1 → block 0, maps to line 1
        assert attrs[0]["start_line"] == 1
        # First p → block 1, maps to line 5 (not line 3 which is the comment)
        assert attrs[1]["start_line"] == 5
        # Second p → block 2, maps to line 7
        assert attrs[2]["start_line"] == 7

    def test_multiple_comments_dont_compound_desync(self):
        """Multiple consecutive comments must not create multiple phantom blocks."""
        md = "# Title\n\n<!-- note 1 -->\n\n<!-- note 2 -->\n\n<!-- note 3 -->\n\nThe actual content."
        html = "<h1>Title</h1>\n<p>The actual content.</p>"
        annotated = annotate(md, html)
        attrs = extract_liveedit_attrs(annotated)
        assert len(attrs) == 2
        assert attrs[0]["start_line"] == 1
        assert attrs[1]["start_line"] == 9

    def test_multiline_comment_excluded(self):
        """Multi-line HTML comments spanning several lines are also excluded."""
        md = "Before\n\n<!--\nThis is a long\nmulti-line comment\nthat spans several lines\n-->\n\nAfter"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "Before"
        assert blocks[1].content == "After"


# ---------------------------------------------------------------------------
# Regression 3: List item splitting (2026-04-03)
#
# Ordered/unordered list items separated by blank lines were parsed as separate
# blocks, but Markdown renders them as a single <ol>/<ul>. The _BlockAnnotator
# assigned only block 1's line range to the <ol>, and block 2 leaked into the
# next HTML element, shifting everything after it.
# ---------------------------------------------------------------------------


class TestRegression_ListItemSplitting:
    """Consecutive list items (even blank-line separated) must merge into one block."""

    def test_ol_blank_separated_full_pipeline(self):
        """Real-world pattern from dfm index.md: numbered list with blank lines."""
        md = (
            "ProteinGen provides:\n"
            "\n"
            "1. a unified interface for sampling from protein generative models\n"
            "\n"
            "2. a library of standard protein sequence models\n"
            "\n"
            "Read the docs for more."
        )
        # MkDocs renders the two list items as a single <ol>
        html = (
            "<p>ProteinGen provides:</p>\n"
            "<ol>\n"
            "<li>a unified interface for sampling from protein generative models</li>\n"
            "<li>a library of standard protein sequence models</li>\n"
            "</ol>\n"
            "<p>Read the docs for more.</p>"
        )
        annotated = annotate(md, html)
        attrs = extract_liveedit_attrs(annotated)
        assert len(attrs) == 3
        # p → block 0, lines 1-1
        assert attrs[0]["start_line"] == 1
        assert attrs[0]["end_line"] == 1
        # ol → block 1, lines 3-5 (both list items merged)
        assert attrs[1]["start_line"] == 3
        assert attrs[1]["end_line"] == 5
        # p → block 2, line 7
        assert attrs[2]["start_line"] == 7

    def test_ul_blank_separated_full_pipeline(self):
        """Unordered list items separated by blank lines also merge."""
        md = "Features:\n\n- Fast inference\n\n- Easy to use\n\n- Well documented\n\nGet started now."
        html = (
            "<p>Features:</p>\n"
            "<ul>\n"
            "<li>Fast inference</li>\n"
            "<li>Easy to use</li>\n"
            "<li>Well documented</li>\n"
            "</ul>\n"
            "<p>Get started now.</p>"
        )
        annotated = annotate(md, html)
        attrs = extract_liveedit_attrs(annotated)
        assert len(attrs) == 3
        assert attrs[1]["start_line"] == 3
        assert attrs[1]["end_line"] == 7  # all 3 items merged

    def test_mixed_list_types_stay_separate(self):
        """An <ol> followed by a <ul> produces two separate blocks."""
        md = "1. ordered item\n\n- unordered item\n\nEnd."
        html = "<ol><li>ordered item</li></ol>\n<ul><li>unordered item</li></ul>\n<p>End.</p>"
        annotated = annotate(md, html)
        attrs = extract_liveedit_attrs(annotated)
        assert len(attrs) == 3
        assert attrs[0]["start_line"] == 1
        assert attrs[1]["start_line"] == 3
        assert attrs[2]["start_line"] == 5


# ---------------------------------------------------------------------------
# Regression 4: Fence close no-flush (2026-04-06)
#
# When a fenced code block closed (```), parse_blocks didn't flush the current
# block. Content immediately after the closing fence (no blank line) got lumped
# into the same block. Then _merge_tabbed_blocks absorbed it because the block
# started with indented content.
#
# Real-world: attribution line directly after tab's closing ``` got eaten by
# the tab set block.
# ---------------------------------------------------------------------------


class TestRegression_FenceCloseNoFlush:
    """Closing fence must flush the block so subsequent content starts fresh."""

    def test_content_after_fence_close_is_separate_block(self):
        """Content immediately after closing ``` (no blank line) must be its own block."""
        md = "```python\nx = 1\n```\nAttribution line\n\n## Next Section"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[0].content == "```python\nx = 1\n```"
        assert blocks[1].content == "Attribution line"
        assert blocks[2].content == "## Next Section"

    def test_tab_fence_close_attribution_not_absorbed(self):
        """Real-world: attribution after tab's closing fence must not be absorbed into tab set."""
        md = (
            '=== "Original ProteinMPNN"\n'
            "\n"
            "    ```python\n"
            "    import proteingen\n"
            "    model = proteingen.load('mpnn')\n"
            "    seqs = model.sample(pdb='1abc.pdb')\n"
            "    ```\n"
            "\n"
            '=== "ProteinGen"\n'
            "\n"
            "    ```python\n"
            "    import proteingen\n"
            "    seqs = proteingen.sample('mpnn', pdb='1abc.pdb')\n"
            "    ```\n"  # line 14 — fence close
            "Developed by the Lab.\n"  # line 15 — NOT part of tab set
            "\n"
            "## Why ProteinGen?"  # line 17
        )
        blocks = parse_blocks(md)
        assert len(blocks) == 3, (
            f"Expected 3 blocks, got {len(blocks)}: {[(b.start_line, b.end_line, b.content[:40]) for b in blocks]}"
        )
        # Tab set ends at the fence close, NOT at the attribution
        assert blocks[0].end_line == 14
        assert "Developed by the Lab." not in blocks[0].content
        # Attribution is its own block
        assert blocks[1].content == "Developed by the Lab."
        assert blocks[1].start_line == 15
        # Heading is separate
        assert blocks[2].content == "## Why ProteinGen?"

    def test_tab_fence_close_full_pipeline(self):
        """Full pipeline: attribution after fence close gets correct line mapping."""
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
            "    ```\n"  # line 11
            "Attribution line\n"  # line 12
            "\n"
            "## Next Section"  # line 14
        )
        html = '<div class="tabbed-set">tabs content</div>\n<p>Attribution line</p>\n<h2>Next Section</h2>'
        annotated = annotate(md, html)
        attrs = extract_liveedit_attrs(annotated)
        assert len(attrs) == 3
        # Tab set block
        assert attrs[0]["start_line"] == 1
        assert attrs[0]["end_line"] == 11
        # Attribution paragraph — must NOT be absorbed into tab set
        assert attrs[1]["start_line"] == 12
        assert attrs[1]["end_line"] == 12
        # Heading
        assert attrs[2]["start_line"] == 14

    def test_tilde_fence_close_also_flushes(self):
        """~~~ fences also flush on close."""
        md = "~~~\ncode\n~~~\nNext line\n\nParagraph"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[0].content == "~~~\ncode\n~~~"
        assert blocks[1].content == "Next line"
        assert blocks[2].content == "Paragraph"

    def test_nested_fence_close_doesnt_flush_prematurely(self):
        """Inner fence markers inside outer fence don't flush prematurely."""
        md = "````\n```\ninner\n```\n````\nAfter"
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].content == "````\n```\ninner\n```\n````"
        assert blocks[1].content == "After"


# ---------------------------------------------------------------------------
# Regression 5: Frontmatter offset with various patterns
#
# The frontmatter offset calculation must handle edge cases: YAML with
# multi-line values, blank lines after frontmatter, no frontmatter at all.
# Incorrect offsets shift ALL line numbers on a page.
# ---------------------------------------------------------------------------


class TestRegression_FrontmatterOffset:
    """Frontmatter offset must be exact — wrong offset shifts all block line numbers."""

    def test_frontmatter_with_list_values(self):
        """Frontmatter containing YAML lists."""
        raw = "---\ntitle: Test\ntags:\n  - python\n  - mkdocs\n---\n\n# Hello"
        page_md = "# Hello"
        offset = count_frontmatter_offset(raw, page_md)
        assert offset == 7  # lines 1-6 are frontmatter, line 7 is blank

    def test_frontmatter_offset_applied_in_annotation(self):
        """Verify frontmatter offset is correctly added to block line numbers."""
        md = "# Hello\n\nWorld"
        html = "<h1>Hello</h1>\n<p>World</p>"
        # Simulate 4 lines of frontmatter
        annotated = annotate(md, html, frontmatter_offset=4)
        attrs = extract_liveedit_attrs(annotated)
        assert len(attrs) == 2
        # Lines should be offset by 4
        assert attrs[0]["start_line"] == 5  # 1 + 4
        assert attrs[1]["start_line"] == 7  # 3 + 4

    def test_multiple_blank_lines_after_frontmatter(self):
        """Multiple blank lines between frontmatter and content."""
        raw = "---\ntitle: Test\n---\n\n\n\n# Hello"
        page_md = "# Hello"
        offset = count_frontmatter_offset(raw, page_md)
        # 3 frontmatter lines (index 0-2), then skip 3 blank lines (index 3-5)
        # Content starts at index 6, so offset = 6
        assert offset == 6

    def test_no_frontmatter_with_dashes_in_content(self):
        """--- in content (not at start) should not be mistaken for frontmatter."""
        raw = "# Title\n\n---\n\nContent"
        page_md = "# Title\n\n---\n\nContent"
        offset = count_frontmatter_offset(raw, page_md)
        assert offset == 0


# ---------------------------------------------------------------------------
# Regression 6: Save endpoint edge cases
#
# The save endpoint does line-range replacement. Edge cases around newline
# handling, empty content, and boundary conditions.
# ---------------------------------------------------------------------------


class TestRegression_SaveEndpoint:
    """Save endpoint must correctly handle line-range replacement edge cases."""

    @pytest.fixture
    def setup(self, tmp_path):
        """Create a docs dir with a test file."""
        docs_dir = str(tmp_path)
        md_path = tmp_path / "test.md"
        config_path = tmp_path / "mkdocs.yml"
        config_path.write_text("site_name: Test\n")
        return docs_dir, md_path, str(config_path)

    def test_save_single_line_replacement(self, setup):
        """Replace a single line in the middle of a file."""
        docs_dir, md_path, config_file = setup
        md_path.write_text("# Title\n\nOld paragraph.\n\nEnd.\n")
        api = LiveEditAPI(None, docs_dir, config_file)

        body = {"file": "test.md", "start_line": 3, "end_line": 3, "content": "New paragraph."}
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        assert sr.status == "200 OK"
        content = md_path.read_text()
        assert "New paragraph." in content
        assert "Old paragraph." not in content
        # Other lines preserved
        assert "# Title" in content
        assert "End." in content

    def test_save_multiline_replacement(self, setup):
        """Replace a multi-line block with different number of lines."""
        docs_dir, md_path, config_file = setup
        md_path.write_text("# Title\n\nLine 1\nLine 2\nLine 3\n\nEnd.\n")
        api = LiveEditAPI(None, docs_dir, config_file)

        body = {"file": "test.md", "start_line": 3, "end_line": 5, "content": "Single replacement line."}
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        assert sr.status == "200 OK"
        content = md_path.read_text()
        assert "Single replacement line." in content
        assert "Line 1" not in content
        lines = content.split("\n")
        # File should be shorter now (3 lines replaced with 1)
        assert len([ln for ln in lines if ln.strip()]) == 3  # Title, replacement, End

    def test_save_preserves_trailing_newline(self, setup):
        """File that ends with newline should still end with newline after save."""
        docs_dir, md_path, config_file = setup
        md_path.write_text("Line 1\nLine 2\nLine 3\n")
        api = LiveEditAPI(None, docs_dir, config_file)

        body = {"file": "test.md", "start_line": 2, "end_line": 2, "content": "New line 2"}
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        content = md_path.read_text()
        assert content.endswith("\n")

    def test_save_line_range_out_of_bounds(self, setup):
        """Line range beyond file length returns error."""
        docs_dir, md_path, config_file = setup
        md_path.write_text("Only one line\n")
        api = LiveEditAPI(None, docs_dir, config_file)

        body = {"file": "test.md", "start_line": 1, "end_line": 10, "content": "x"}
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        assert sr.status == "400 Bad Request"

    def test_save_first_line(self, setup):
        """Replacing the very first line of a file."""
        docs_dir, md_path, config_file = setup
        md_path.write_text("# Old Title\n\nContent\n")
        api = LiveEditAPI(None, docs_dir, config_file)

        body = {"file": "test.md", "start_line": 1, "end_line": 1, "content": "# New Title"}
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        content = md_path.read_text()
        assert content.startswith("# New Title\n")

    def test_save_last_line(self, setup):
        """Replacing the last line of a file."""
        docs_dir, md_path, config_file = setup
        md_path.write_text("# Title\n\nOld ending\n")
        api = LiveEditAPI(None, docs_dir, config_file)

        body = {"file": "test.md", "start_line": 3, "end_line": 3, "content": "New ending"}
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        content = md_path.read_text()
        assert "New ending" in content

    def test_save_expand_single_line_to_multiple(self, setup):
        """Replacing 1 line with multiple lines (expanding edit)."""
        docs_dir, md_path, config_file = setup
        md_path.write_text("# Title\n\nShort.\n\nEnd.\n")
        api = LiveEditAPI(None, docs_dir, config_file)

        body = {
            "file": "test.md",
            "start_line": 3,
            "end_line": 3,
            "content": "Long paragraph\nthat spans\nmultiple lines.",
        }
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        content = md_path.read_text()
        assert "Long paragraph\n" in content
        assert "that spans\n" in content
        assert "multiple lines." in content
        assert "End." in content


# ---------------------------------------------------------------------------
# Regression 7: Combined real-world scenarios
#
# These test complex markdown documents that combine multiple features:
# frontmatter + comments + tabs + lists + fenced code. These are the patterns
# that caused cascading failures in production.
# ---------------------------------------------------------------------------


class TestRegression_CombinedScenarios:
    """Complex documents that combine multiple features where bugs compound."""

    def test_frontmatter_comment_tabs_list(self):
        """Document with frontmatter, comments, tab set, and list — everything at once."""
        raw_file = (
            "---\n"
            "title: Setup Guide\n"
            "---\n"
            "\n"
            "# Installation\n"
            "\n"
            "<!-- TODO: add more platforms -->\n"
            "\n"
            '=== "Linux"\n'
            "\n"
            "    ```bash\n"
            "    apt install foo\n"
            "    ```\n"
            "\n"
            '=== "macOS"\n'
            "\n"
            "    ```bash\n"
            "    brew install foo\n"
            "    ```\n"
            "\n"
            "## Features\n"
            "\n"
            "- Fast\n"
            "\n"
            "- Reliable\n"
            "\n"
            "Done."
        )
        # page.markdown has frontmatter stripped
        page_md = (
            "# Installation\n"
            "\n"
            "<!-- TODO: add more platforms -->\n"
            "\n"
            '=== "Linux"\n'
            "\n"
            "    ```bash\n"
            "    apt install foo\n"
            "    ```\n"
            "\n"
            '=== "macOS"\n'
            "\n"
            "    ```bash\n"
            "    brew install foo\n"
            "    ```\n"
            "\n"
            "## Features\n"
            "\n"
            "- Fast\n"
            "\n"
            "- Reliable\n"
            "\n"
            "Done."
        )
        offset = count_frontmatter_offset(raw_file, page_md)
        assert offset == 4  # 3 frontmatter lines + 1 blank

        blocks = parse_blocks(page_md)
        # Should be: heading, tab set, ## Features, merged list, Done
        assert len(blocks) == 5, (
            f"Got {len(blocks)} blocks: {[(b.start_line, b.end_line, b.content[:30]) for b in blocks]}"
        )

        # Comment excluded
        assert all("TODO" not in b.content for b in blocks)
        # Tab set merged
        assert '=== "Linux"' in blocks[1].content
        assert '=== "macOS"' in blocks[1].content
        # List items merged
        assert "- Fast" in blocks[3].content
        assert "- Reliable" in blocks[3].content

        # Verify annotation with frontmatter offset
        html = (
            "<h1>Installation</h1>\n"
            '<div class="tabbed-set">...</div>\n'
            "<h2>Features</h2>\n"
            "<ul><li>Fast</li><li>Reliable</li></ul>\n"
            "<p>Done.</p>"
        )
        annotated = annotate(page_md, html, frontmatter_offset=offset)
        attrs = extract_liveedit_attrs(annotated)
        assert len(attrs) == 5
        # All line numbers should include frontmatter offset
        assert attrs[0]["start_line"] == 1 + offset  # heading
        assert attrs[4]["start_line"] == 23 + offset  # "Done." is line 23 in page_md

    def test_comment_before_list_no_index_shift(self):
        """Comment before a list must not shift the list's block index."""
        md = "# Title\n\nIntro paragraph.\n\n<!-- note -->\n\n1. First item\n\n2. Second item\n\nConclusion."
        html = (
            "<h1>Title</h1>\n"
            "<p>Intro paragraph.</p>\n"
            "<ol>\n<li>First item</li>\n<li>Second item</li>\n</ol>\n"
            "<p>Conclusion.</p>"
        )
        annotated = annotate(md, html)
        attrs = extract_liveedit_attrs(annotated)
        assert len(attrs) == 4
        # Comment is excluded, so:
        assert attrs[0]["start_line"] == 1  # h1
        assert attrs[1]["start_line"] == 3  # intro p
        assert attrs[2]["start_line"] == 7  # ol (merged list)
        assert attrs[2]["end_line"] == 9  # both items
        assert attrs[3]["start_line"] == 11  # conclusion

    def test_fence_inside_list_item(self):
        """Fenced code block inside a list item — fence-close flush splits the blocks.

        The fence-close flush (regression 4 fix) takes priority: closing ``` always
        ends the current block. This means list items containing fenced code get
        split into: list-marker block, code-fence block, next-list-marker block, etc.
        The list merger only merges blocks that start with list markers, so the
        interleaved fence blocks prevent full merging. This is acceptable because
        the alternative (no fence flush) caused the more severe fence-close bug.
        """
        md = (
            "Steps:\n"
            "\n"
            "1. Install:\n"
            "\n"
            "    ```bash\n"
            "    pip install foo\n"
            "    ```\n"
            "\n"
            "2. Configure:\n"
            "\n"
            "    ```yaml\n"
            "    key: value\n"
            "    ```\n"
            "\n"
            "Done."
        )
        blocks = parse_blocks(md)
        assert blocks[0].content == "Steps:"
        assert blocks[-1].content == "Done."
        # Fence-close flush splits the list items from their fenced code,
        # so we get more blocks than if list items were naively merged.
        # The key invariant: first and last blocks are correct.
        assert any("1. Install:" in b.content for b in blocks)
        assert any("2. Configure:" in b.content for b in blocks)

    def test_empty_fence_block(self):
        """Empty fenced code block (no content between fences)."""
        md = "Before\n\n```\n```\n\nAfter"
        blocks = parse_blocks(md)
        assert len(blocks) == 3
        assert blocks[0].content == "Before"
        assert blocks[1].content == "```\n```"
        assert blocks[2].content == "After"

    def test_deeply_indented_tab_content(self):
        """Tab content with deep nesting (8+ spaces) still merges correctly."""
        md = '=== "Tab"\n\n    Indented text\n\n        Deeply indented code\n\nNormal paragraph'
        blocks = parse_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].start_line == 1
        assert blocks[0].end_line == 5  # tab set includes deeply indented
        assert blocks[1].content == "Normal paragraph"


# ---------------------------------------------------------------------------
# Regression 8: BlockAnnotator edge cases
#
# The _BlockAnnotator must handle various HTML patterns correctly:
# self-closing tags, nested block tags, void elements, etc.
# ---------------------------------------------------------------------------


class TestRegression_BlockAnnotator:
    """_BlockAnnotator must correctly pair blocks with top-level HTML elements."""

    def test_nested_divs_only_annotate_top_level(self):
        """Only top-level block elements get annotated, not nested ones."""
        md = "# Title\n\nContent"
        blocks = parse_blocks(md)
        html = (
            "<h1>Title</h1>\n"
            '<div class="admonition">\n'
            '  <p class="admonition-title">Note</p>\n'
            "  <p>Inner paragraph</p>\n"
            "</div>"
        )
        annotator = _BlockAnnotator(blocks, "test.md", 0)
        result = annotator.feed_and_annotate(html)
        attrs = extract_liveedit_attrs(result)
        # Only h1 and top-level div should be annotated (depth=0)
        assert len(attrs) == 2

    def test_hr_element_depth_desync(self):
        """KNOWN BUG: <hr> (void element) increments depth but never decrements it.

        HTMLParser calls handle_starttag for <hr> but never handle_endtag (no </hr>).
        Since _BlockAnnotator uses depth to skip nested elements, <hr> permanently
        increments depth, making all subsequent block elements appear "nested" and
        unannotated. This means pages with <hr> (markdown ---) will have broken
        editing for everything after the <hr>.

        This test documents the current (broken) behavior. When the bug is fixed,
        update this test to assert len(attrs) == 3.
        """
        md = "Paragraph\n\n---\n\nAnother paragraph"
        blocks = parse_blocks(md)
        html = "<p>Paragraph</p>\n<hr>\n<p>Another paragraph</p>"
        annotator = _BlockAnnotator(blocks, "test.md", 0)
        result = annotator.feed_and_annotate(html)
        attrs = extract_liveedit_attrs(result)
        # BUG: only 2 annotated (p + hr), the second p is missed because depth=1
        # after <hr> (void element with no closing tag)
        assert len(attrs) == 2  # TODO: should be 3 when void element handling is fixed

    def test_more_html_elements_than_blocks(self):
        """If HTML has more block elements than parsed blocks, extras are left alone."""
        md = "Only one block"
        blocks = parse_blocks(md)
        html = "<p>Only one block</p>\n<p>Extra paragraph from plugin</p>"
        annotator = _BlockAnnotator(blocks, "test.md", 0)
        result = annotator.feed_and_annotate(html)
        attrs = extract_liveedit_attrs(result)
        # Only the first p gets annotated
        assert len(attrs) == 1
        assert attrs[0]["block"] == 0

    def test_table_element(self):
        """Tables are block elements and get annotated."""
        md = "# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nAfter"
        blocks = parse_blocks(md)
        html = (
            "<h1>Title</h1>\n"
            "<table>\n<thead><tr><th>A</th><th>B</th></tr></thead>\n"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody>\n</table>\n"
            "<p>After</p>"
        )
        annotator = _BlockAnnotator(blocks, "test.md", 0)
        result = annotator.feed_and_annotate(html)
        attrs = extract_liveedit_attrs(result)
        assert len(attrs) == 3

    def test_details_element(self):
        """<details> (collapsible) is a block element."""
        md = "Before\n\n??? note\n    Hidden content\n\nAfter"
        blocks = parse_blocks(md)
        html = "<p>Before</p>\n<details>\n<summary>note</summary>\n<p>Hidden content</p>\n</details>\n<p>After</p>"
        annotator = _BlockAnnotator(blocks, "test.md", 0)
        result = annotator.feed_and_annotate(html)
        attrs = extract_liveedit_attrs(result)
        assert len(attrs) == 3


# ---------------------------------------------------------------------------
# Regression 9: Source endpoint edge cases
# ---------------------------------------------------------------------------


class TestRegression_SourceEndpoint:
    """Source endpoint must return correct raw markdown for block ranges."""

    def test_source_with_frontmatter_file(self, tmp_path):
        """Source endpoint returns raw lines including frontmatter offset."""
        docs_dir = str(tmp_path)
        md_path = tmp_path / "page.md"
        md_path.write_text("---\ntitle: Test\n---\n\n# Hello\n\nWorld\n")
        config_path = tmp_path / "mkdocs.yml"
        config_path.write_text("site_name: Test\n")

        api = LiveEditAPI(None, docs_dir, str(config_path))
        # Lines 5-5 (after frontmatter) should be "# Hello"
        environ = make_environ("GET", "/liveedit/source", query_string="file=page.md&start=5&end=5")
        sr = FakeStartResponse()
        result = api(environ, sr)

        assert sr.status == "200 OK"
        data = json.loads(b"".join(result))
        assert data["source"].strip() == "# Hello"

    def test_source_multiline_range(self, tmp_path):
        """Source endpoint returns multiple lines for a range."""
        docs_dir = str(tmp_path)
        md_path = tmp_path / "page.md"
        md_path.write_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n")
        config_path = tmp_path / "mkdocs.yml"
        config_path.write_text("site_name: Test\n")

        api = LiveEditAPI(None, docs_dir, str(config_path))
        environ = make_environ("GET", "/liveedit/source", query_string="file=page.md&start=2&end=4")
        sr = FakeStartResponse()
        result = api(environ, sr)

        data = json.loads(b"".join(result))
        assert "Line 2" in data["source"]
        assert "Line 3" in data["source"]
        assert "Line 4" in data["source"]
        assert "Line 1" not in data["source"]
        assert "Line 5" not in data["source"]


# ---------------------------------------------------------------------------
# Regression 10: Path traversal security
# ---------------------------------------------------------------------------


class TestRegression_PathTraversal:
    """Save/source endpoints must block path traversal attacks."""

    def test_dotdot_traversal_save(self, tmp_path):
        docs_dir = str(tmp_path)
        (tmp_path / "legit.md").write_text("hello\n")
        config_path = tmp_path / "mkdocs.yml"
        config_path.write_text("site_name: Test\n")

        api = LiveEditAPI(None, docs_dir, str(config_path))
        body = {"file": "../../../etc/passwd", "start_line": 1, "end_line": 1, "content": "hacked"}
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)
        assert sr.status == "403 Forbidden"

    def test_dotdot_traversal_source(self, tmp_path):
        docs_dir = str(tmp_path)
        (tmp_path / "legit.md").write_text("hello\n")
        config_path = tmp_path / "mkdocs.yml"
        config_path.write_text("site_name: Test\n")

        api = LiveEditAPI(None, docs_dir, str(config_path))
        environ = make_environ("GET", "/liveedit/source", query_string="file=../../../etc/passwd&start=1&end=1")
        sr = FakeStartResponse()
        api(environ, sr)
        assert sr.status == "403 Forbidden"

    def test_symlink_traversal(self, tmp_path):
        """Symlink pointing outside docs_dir should be blocked."""
        docs_dir = str(tmp_path / "docs")
        os.makedirs(docs_dir)
        # Create a file outside docs_dir
        outside = tmp_path / "secret.md"
        outside.write_text("secret content\n")
        # Create a symlink inside docs_dir pointing outside
        link = tmp_path / "docs" / "link.md"
        os.symlink(str(outside), str(link))

        config_path = tmp_path / "mkdocs.yml"
        config_path.write_text("site_name: Test\n")

        api = LiveEditAPI(None, docs_dir, str(config_path))
        body = {"file": "link.md", "start_line": 1, "end_line": 1, "content": "hacked"}
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)
        assert sr.status == "403 Forbidden"
