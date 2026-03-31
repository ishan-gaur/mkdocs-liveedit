"""MkDocs LiveEdit plugin — inline editing during mkdocs serve."""

from __future__ import annotations

import importlib.resources
import logging
import os
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from mkdocs.plugins import BasePlugin

from .api import LiveEditAPI
from .sourcemap import Block, count_frontmatter_offset, parse_blocks

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.pages import Page

log = logging.getLogger("mkdocs.plugins.liveedit")

# Block-level HTML tags we annotate for editing
BLOCK_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "ul",
        "ol",
        "dl",
        "blockquote",
        "pre",
        "table",
        "div",
        "details",
        "figure",
        "hr",
    }
)


class _BlockAnnotator(HTMLParser):
    """Walk HTML and annotate top-level block elements with liveedit data attributes."""

    def __init__(self, blocks: list[Block], file_path: str, frontmatter_offset: int):
        super().__init__(convert_charrefs=False)
        self.blocks = blocks
        self.file_path = file_path
        self.frontmatter_offset = frontmatter_offset
        self.output: list[str] = []
        self.depth = 0  # nesting depth for block tags
        self.block_index = 0
        self._raw = ""
        self._pos = 0

    def feed_and_annotate(self, html: str) -> str:
        self._raw = html
        self._pos = 0
        self.output = []
        self.depth = 0
        self.block_index = 0
        self.feed(html)
        # Flush remaining content
        if self._pos < len(self._raw):
            self.output.append(self._raw[self._pos :])
        return "".join(self.output)

    def _flush_to(self, offset: int):
        """Append raw HTML from current position up to offset."""
        if offset > self._pos:
            self.output.append(self._raw[self._pos : offset])
            self._pos = offset

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        line, col = self.getpos()
        offset = self._find_tag_offset(line, col)

        if tag in BLOCK_TAGS and self.depth == 0 and self.block_index < len(self.blocks):
            block = self.blocks[self.block_index]
            start = block.start_line + self.frontmatter_offset
            end = block.end_line + self.frontmatter_offset

            self._flush_to(offset)

            # Find the end of the opening tag (the first '>')
            tag_end = self._raw.index(">", offset)
            opening_tag = self._raw[offset:tag_end]

            attrs_str = (
                f' data-liveedit-block="{self.block_index}"'
                f' data-liveedit-file="{self.file_path}"'
                f' data-liveedit-lines="{start}-{end}"'
            )
            self.output.append(opening_tag + attrs_str + ">")
            self._pos = tag_end + 1
            self.block_index += 1

        if tag in BLOCK_TAGS:
            self.depth += 1

    def handle_endtag(self, tag: str):
        if tag in BLOCK_TAGS and self.depth > 0:
            self.depth -= 1

    def _find_tag_offset(self, line: int, col: int) -> int:
        """Convert parser (line, col) to string offset."""
        current_line = 1
        for i, ch in enumerate(self._raw):
            if current_line == line:
                return i + col
            if ch == "\n":
                current_line += 1
        return len(self._raw)


def _load_asset(name: str) -> str:
    """Load a bundled asset file."""
    assets = importlib.resources.files("mkdocs_liveedit") / "assets"
    return (assets / name).read_text(encoding="utf-8")


class LiveEditPlugin(BasePlugin):
    """MkDocs plugin that enables inline editing during serve."""

    def __init__(self):
        super().__init__()
        self._serve_mode = False

    def on_startup(self, *, command: str, dirty: bool) -> None:
        self._serve_mode = command == "serve"

    def on_page_markdown(self, markdown: str, *, page: Page, config: MkDocsConfig, files) -> str:
        if not self._serve_mode:
            return markdown

        blocks = parse_blocks(markdown)
        page.liveedit_blocks = blocks  # type: ignore[attr-defined]

        # Compute frontmatter offset by re-reading the source file
        src_path = os.path.join(config["docs_dir"], page.file.src_path)
        try:
            with open(src_path, "r", encoding="utf-8") as f:
                raw = f.read()
            page.liveedit_frontmatter_offset = count_frontmatter_offset(raw, markdown)  # type: ignore[attr-defined]
        except OSError:
            page.liveedit_frontmatter_offset = 0  # type: ignore[attr-defined]

        return markdown

    def on_page_content(self, html: str, *, page: Page, config: MkDocsConfig, files) -> str:
        if not self._serve_mode:
            return html

        blocks: list[Block] = getattr(page, "liveedit_blocks", [])
        offset: int = getattr(page, "liveedit_frontmatter_offset", 0)

        if not blocks:
            return html

        file_path = page.file.src_path
        annotator = _BlockAnnotator(blocks, file_path, offset)
        return annotator.feed_and_annotate(html)

    def on_post_page(self, output: str, *, page: Page, config: MkDocsConfig) -> str:
        if not self._serve_mode:
            return output

        css = _load_asset("liveedit.css")
        js = _load_asset("liveedit.js")

        injection = f'<style id="liveedit-css">{css}</style>\n<script id="liveedit-js">{js}</script>\n'

        # Inject before </body>
        if "</body>" in output:
            output = output.replace("</body>", injection + "</body>", 1)
        else:
            output += injection

        return output

    def on_serve(self, server, *, config: MkDocsConfig, builder):
        if not self._serve_mode:
            return server

        docs_dir = config["docs_dir"]
        config_file = config.config_file_path or "mkdocs.yml"

        # Wrap the livereload server's WSGI application with our API middleware
        if hasattr(server, "application"):
            server.application = LiveEditAPI(server.application, docs_dir, config_file)
        elif hasattr(server, "app"):
            server.app = LiveEditAPI(server.app, docs_dir, config_file)
        else:
            log.warning("LiveEdit: could not wrap server WSGI app — API routes won't work")

        return server
