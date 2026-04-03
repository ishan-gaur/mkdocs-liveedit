"""MkDocs LiveEdit plugin — inline editing during mkdocs serve."""

from __future__ import annotations

import importlib.resources
import logging
import os
import shutil
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from mkdocs.plugins import BasePlugin

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


def _patch_livereload_server(docs_dir: str, config_file: str):
    """Monkey-patch LiveReloadServer._serve_request to intercept /liveedit/* API routes.

    We patch _serve_request (the inner method called by serve_request) at the
    class level. This works because serve_request calls self._serve_request()
    at request time, so the class-level patch is picked up.

    We can't patch serve_request itself because __init__ already stored the
    original bound method via set_app(). And we can't rely on on_serve because
    the MkDocs CLI passes livereload=False due to a Click flag interaction bug.
    """
    from mkdocs.livereload import LiveReloadServer

    if getattr(LiveReloadServer, "_liveedit_patched", False):
        return

    from .api import LiveEditAPI

    original_serve_request = LiveReloadServer._serve_request
    api = LiveEditAPI(None, docs_dir, config_file)

    def patched_serve_request(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith("/liveedit/"):
            # Give the API access to the server so it can trigger rebuilds
            api.server = self
            return api(environ, start_response)
        return original_serve_request(self, environ, start_response)

    LiveReloadServer._serve_request = patched_serve_request
    LiveReloadServer._liveedit_patched = True
    log.info("LiveEdit: patched LiveReloadServer for /liveedit/* API routes")


class LiveEditPlugin(BasePlugin):
    """MkDocs plugin that enables inline editing during serve."""

    def __init__(self):
        super().__init__()
        self._active = False
        self._cache_dir: str | None = None
        self._cache_primed = False

    def on_startup(self, *, command: str, dirty: bool) -> None:
        # Only activate during dirty serve — dirty builds only rebuild changed
        # pages, making the edit-save-rebuild cycle fast.  Use: mkdocs serve --dirty
        self._active = command == "serve" and dirty
        self._cache_primed = False

    def on_config(self, config: MkDocsConfig) -> MkDocsConfig:
        if not self._active:
            return config

        # Prime the temp site_dir with cached output so --dirty can skip unchanged pages.
        # Without this, mkdocs serve's temp dir is always empty and --dirty rebuilds everything.
        if not self._cache_primed:
            project_dir = os.path.dirname(config.config_file_path) if config.config_file_path else "."
            self._cache_dir = os.path.join(project_dir, ".cache", "liveedit", "site")
            site_dir = config["site_dir"]
            if os.path.isdir(self._cache_dir):
                shutil.copytree(self._cache_dir, site_dir, dirs_exist_ok=True, copy_function=shutil.copy2)
                log.info("LiveEdit: restored cached site for fast dirty build")
            self._cache_primed = True

        # Patch the LiveReloadServer class to intercept /liveedit/* routes.
        # Done in on_config (runs before the server is created) so the patch
        # is in place regardless of the livereload flag.
        docs_dir = config["docs_dir"]
        config_file = config.config_file_path or "mkdocs.yml"
        _patch_livereload_server(docs_dir, config_file)

        return config

    def on_post_build(self, *, config: MkDocsConfig) -> None:
        if not self._active or not self._cache_dir:
            return
        # Save built site to cache for next session's fast startup
        site_dir = config["site_dir"]
        if os.path.isdir(site_dir):
            if os.path.isdir(self._cache_dir):
                shutil.rmtree(self._cache_dir)
            shutil.copytree(site_dir, self._cache_dir, copy_function=shutil.copy2)
            log.info("LiveEdit: cached built site for next session")

    def on_page_markdown(self, markdown: str, *, page: Page, config: MkDocsConfig, files) -> str:
        if not self._active:
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
        if not self._active:
            return html

        blocks: list[Block] = getattr(page, "liveedit_blocks", [])
        offset: int = getattr(page, "liveedit_frontmatter_offset", 0)

        if not blocks:
            return html

        file_path = page.file.src_path
        annotator = _BlockAnnotator(blocks, file_path, offset)
        return annotator.feed_and_annotate(html)

    def on_post_page(self, output: str, *, page: Page, config: MkDocsConfig) -> str:
        if not self._active:
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
