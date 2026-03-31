# mkdocs-liveedit

An MkDocs plugin that adds inline editing to `mkdocs serve`. Double-click any content block to edit its raw markdown in a floating textarea, save, and the page rebuilds via livereload.

## Installation

```bash
pip install mkdocs-liveedit
```

## Usage

Add to your `mkdocs.yml`:

```yaml
plugins:
  - liveedit
```

Then run `mkdocs serve` as usual. Double-click any content block to edit it inline.

## Features

- **Inline editing** — Double-click any block (paragraph, heading, list, code block, table, etc.) to edit its raw markdown
- **Live reload** — Save your edit and the page rebuilds automatically
- **Nav reordering** — Drag-and-drop sidebar items to reorder navigation (rewrites `mkdocs.yml`)
- **Material theme compatible** — Works with `navigation.instant` SPA-style navigation

## Non-goals

- Full WYSIWYG editing
- Production use
- Replacing your text editor for heavy writing

This is for quick fixes and reordering while previewing.
