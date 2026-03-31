# mkdocs-liveedit

An MkDocs plugin that adds inline editing to `mkdocs serve`. Double-click any content block to edit its raw markdown in a floating textarea, save, and the page rebuilds via livereload.

**Non-goals:** Full WYSIWYG, production use, replacing your text editor for heavy writing. This is for quick fixes and reordering while previewing.

## How it works

```
Browser (liveedit.js)                    Plugin (Python)
─────────────────────                    ───────────────
double-click a block
  → show textarea with raw md     ←──   source-line markers in HTML
  → user edits
  → POST /liveedit/save           ──→   line-range replacement in .md file
                                         watchdog detects change
                                         livereload rebuilds + reloads browser
```

## Architecture

MkDocs plugin that hooks into the serve pipeline. Does nothing during `mkdocs build`.

### Plugin hooks

| Hook | Priority | Purpose |
|---|---|---|
| `on_startup` | — | Record that we're in serve mode |
| `on_page_markdown` | high (early) | Parse markdown into blocks, record line ranges per block |
| `on_page_content` | — | Annotate HTML block elements with `data-liveedit-file` and `data-liveedit-lines` attributes |
| `on_post_page` | — | Inject `liveedit.js` and `liveedit.css` into the page |
| `on_serve` | — | Wrap the WSGI app to add `/liveedit/*` API routes |

### Source-line mapping

The core problem: map rendered HTML blocks back to line ranges in the source `.md` file.

Strategy in `on_page_markdown`:
1. Split markdown source by blank-line-delimited blocks (respecting fenced code blocks and frontmatter)
2. For each block, record `(file_path, start_line, end_line)`
3. Store this mapping on the `page` object (e.g. `page.liveedit_blocks`)

In `on_page_content`, the rendered HTML arrives as a string. Walk it with an HTML parser and attach `data-liveedit-block="N"` to top-level block elements (h1-h6, p, ul, ol, blockquote, pre, table, div.admonition, etc.). Block index N maps back to the line-range info stored earlier.

**Known limitation:** Plugins that rewrite markdown (macros, snippets, includes) may shift line numbers or generate content with no source mapping. Blocks that can't be mapped are simply not editable — they won't get the `data-liveedit-*` attributes.

### API routes (WSGI middleware)

Intercept requests to `/liveedit/*` before they hit the normal static file server.

#### `POST /liveedit/save`

```json
{
  "file": "docs/guide/setup.md",
  "start_line": 42,
  "end_line": 55,
  "content": "## New heading\n\nUpdated paragraph text.\n"
}
```

- Validate `file` is under `docs_dir` (path traversal protection)
- Read the file, replace lines `start_line..end_line` with `content`
- Write file back
- Watchdog + livereload handles the rest

#### `POST /liveedit/nav`

```json
{
  "nav": [
    {"Getting Started": "getting-started.md"},
    {"Guide": [
      {"Setup": "guide/setup.md"},
      {"Usage": "guide/usage.md"}
    ]}
  ]
}
```

- Rewrite the `nav:` section of `mkdocs.yml` using `ruamel.yaml` (preserves comments)
- If no explicit `nav:` exists, generate one from the current auto-detected structure first

### Frontend (liveedit.js)

Injected into every page via `on_post_page`, only during serve.

**Edit flow:**
1. On page load, find all elements with `data-liveedit-block` attributes
2. Add a subtle edit icon on hover (absolute-positioned pencil icon, top-right of block)
3. On double-click or icon click:
   - Fetch the raw markdown for that block (either embedded in a `data-liveedit-source` attribute, or via `GET /liveedit/source?file=...&start=N&end=M`)
   - Show a floating textarea overlay anchored to the block, pre-filled with the raw markdown
   - Textarea auto-sizes to content
4. Save button (or Cmd+Enter) → `POST /liveedit/save` → textarea closes, page rebuilds

**Sidebar reorder flow:**
1. Add drag handles to sidebar nav items
2. On drop, serialize the new nav structure from the DOM
3. `POST /liveedit/nav`

**Material theme integration:**
- Material's `navigation.instant` feature does SPA-style page loads. Need to re-initialize edit handlers after navigation. Hook into Material's `document$` RxJS observable if available, or fall back to MutationObserver on the content container.

### CSS (liveedit.css)

- `.liveedit-hover` — subtle highlight on hoverable blocks
- `.liveedit-icon` — pencil icon positioning
- `.liveedit-overlay` — the textarea container (floating, z-index above content)
- `.liveedit-textarea` — monospace, auto-resize
- `.liveedit-toolbar` — save/cancel buttons below textarea
- Drag handle styling for sidebar items
- All scoped under a `.liveedit-active` body class (so it's easy to toggle off)

## File structure

```
mkdocs-liveedit/
├── pyproject.toml
├── README.md
├── DESIGN.md              ← this file
├── src/
│   └── mkdocs_liveedit/
│       ├── __init__.py
│       ├── plugin.py      # MkDocs BasePlugin subclass, all hook implementations
│       ├── sourcemap.py   # Markdown block parser → line-range mapping
│       ├── api.py         # WSGI middleware for /liveedit/* routes
│       └── assets/
│           ├── liveedit.js
│           └── liveedit.css
└── tests/
    ├── test_sourcemap.py  # Unit tests for block parsing + line mapping
    └── test_api.py        # Test save endpoint (file writeback)
```

## Implementation order

1. **Scaffold** — pyproject.toml with entry point, empty plugin class, `mkdocs serve` runs with plugin enabled
2. **Source mapping** — `sourcemap.py`: parse markdown into blocks with line ranges. Unit test this heavily — it's the foundation.
3. **HTML annotation** — `on_page_content` adds `data-liveedit-*` attributes to block elements
4. **Save API** — WSGI middleware + file writeback endpoint
5. **Frontend basics** — inject JS, double-click → textarea → save flow (no drag-drop yet)
6. **Nav reordering** — sidebar drag-drop + `mkdocs.yml` rewrite
7. **Polish** — error handling, visual feedback, Material instant-nav compat

## Key dependencies

- `mkdocs >= 1.5` (plugin API, livereload server)
- `ruamel.yaml` (comment-preserving YAML for nav rewriting)
- No frontend build step — vanilla JS + CSS, injected as strings or read from assets/

## Gotchas to watch for

- **Frontmatter offset:** `page.markdown` has frontmatter stripped (stored in `page.meta`). Line numbers in the block map need to account for the frontmatter lines at the top of the file. Compute the offset from `len(raw_file) - len(page.markdown stripped)` or re-read the file.
- **Fenced code blocks:** A blank line inside a fenced code block is NOT a block separator. The parser must track fence state.
- **Indented content:** List items, blockquotes, and admonitions contain nested blocks. For v1, treat the entire top-level construct as one editable block (e.g., the whole list, not individual items).
- **`navigation.instant`:** Material intercepts link clicks and swaps page content without full reloads. The liveedit JS must reinitialize after each swap.
- **Concurrent edits:** Not a concern for v1 (single user, local dev server), but the save endpoint should re-read the file and verify the target lines haven't changed before writing.
- **Path traversal:** The save endpoint MUST validate that the file path resolves to something under `docs_dir`. Use `os.path.realpath` and check prefix.
