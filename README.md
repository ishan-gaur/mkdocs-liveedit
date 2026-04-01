# mkdocs-liveedit

An MkDocs plugin that adds inline editing to `mkdocs serve`. Double-click any content block to edit its raw markdown in a floating textarea, save, and the page rebuilds automatically.

**Non-goals:** Full WYSIWYG, production use, replacing your text editor for heavy writing. This is for quick fixes and reordering while previewing.

## Installation

Install directly from GitHub:

```bash
pip install git+https://github.com/ishan-gaur/mkdocs-liveedit.git
```

Or with uv:

```bash
uv add git+https://github.com/ishan-gaur/mkdocs-liveedit.git
```

Or for development (editable install from a local clone):

```bash
git clone https://github.com/ishan-gaur/mkdocs-liveedit.git
cd mkdocs-liveedit
pip install -e .
```

## Setup

Add `liveedit` to your `mkdocs.yml`:

```yaml
plugins:
  - search
  - liveedit
```

Then run `mkdocs serve` as usual. The plugin only activates during `mkdocs serve` — it does nothing during `mkdocs build`.

## Usage

1. **Hover** over any content block (paragraph, heading, list, code block, table, etc.) — a dashed outline and ✎ pencil icon appear
2. **Double-click** the block or click the pencil icon to open the editor
3. **Edit** the raw markdown in the textarea
4. **Save** with `⌘+Enter` (Mac) / `Ctrl+Enter` (other), or click the Save button
5. **Cancel** with `Escape` or click Cancel

The file is written to disk, the site rebuilds, and the page reloads automatically.

## Features

- **Inline editing** — edit any markdown block without leaving the browser
- **Live rebuild** — saves trigger an automatic site rebuild + page reload
- **Source mapping** — correctly maps rendered HTML blocks back to line ranges in `.md` files, respecting frontmatter offsets and fenced code blocks
- **Path traversal protection** — the save API validates all file paths are under `docs_dir`
- **No frontend build step** — vanilla JS + CSS, injected automatically during serve

## Limitations

- Plugins that rewrite markdown (macros, snippets, includes) may shift line numbers. Blocks that can't be mapped back to source lines simply won't be editable.
- For v1, nested constructs (lists, blockquotes, admonitions) are treated as single editable blocks — you edit the whole list, not individual items.
- Nav sidebar drag-and-drop reordering is implemented for the Material theme's DOM structure. It may not work with other themes.

## Development

```bash
git clone https://github.com/ishan-gaur/mkdocs-liveedit.git
cd mkdocs-liveedit
uv sync
uv run pytest tests/ -v
uv run mkdocs serve -f example/mkdocs.yml  # test with the example site
```
