# mkdocs-liveedit — Working Knowledge

## Architecture

- **Core problem**: map rendered HTML block elements back to source markdown line ranges for inline editing
- **Pipeline**: `parse_blocks(markdown)` → `_BlockAnnotator(blocks, html)` → annotated HTML with `data-liveedit-*` attributes
- The block parser and HTML annotator must stay in 1:1 correspondence — if they disagree on block count, every subsequent block gets the wrong line numbers

## Block-HTML Desync: The Recurring Bug Pattern

The #1 source of bugs is **block count mismatch**: `parse_blocks` produces N blocks, but MkDocs renders M HTML elements (M ≠ N). The annotator walks HTML sequentially, consuming one block per top-level element, so any mismatch shifts all subsequent mappings.

Causes discovered so far (each required a merge pass in `parse_blocks`):
- **Tab sets** (`=== "Tab"`) — blank-line parser splits into 6+ blocks, renders as 1 `<div>` → `_merge_tabbed_blocks`
- **List items** (`1. ...` / blank / `2. ...`) — split into separate blocks, renders as 1 `<ol>` → `_merge_list_blocks`
- **Admonitions** (`!!! type` / `??? type`) — indented content split by blank lines + fence-close flush, renders as 1 `<div>`/`<details>` → `_merge_admonition_blocks`
- **HTML comments** (`<!-- ... -->`) — parsed as blocks but produce no HTML element → filtered out via `_is_comment_only`
- **Fence-close no-flush** — closing ``` didn't flush, so content after fence got lumped into the fence block → added `flush()` after fence close

When adding new merge passes: order matters. Current chain: `comment filter → list merge → admonition merge → tab merge`.

## _BlockAnnotator Gotchas

- **Void elements** (`<hr>`, `<br>`, `<img>`, etc.) — HTMLParser calls `handle_starttag` but never `handle_endtag`. Must NOT increment `depth` for these or it stays permanently stuck, making all subsequent siblings appear "nested" and unannotated. Handled via `_VOID_ELEMENTS` frozenset.
- **Depth tracking** — only top-level (depth=0) block elements get annotated. Nested block tags (e.g. `<p>` inside `<div class="admonition">`) are skipped.
- **More HTML elements than blocks** — safe; extras just don't get annotated. But fewer HTML elements than blocks means wasted blocks and everything after shifts.

## Testing Strategy

- `tests/test_sourcemap.py` — unit tests for `parse_blocks` and `count_frontmatter_offset` in isolation
- `tests/test_api.py` — save/source/nav endpoint tests
- `tests/test_regressions.py` — **full-pipeline tests** (markdown → parse_blocks → _BlockAnnotator → HTML annotation). This is where block-index desync bugs actually manifest. Each test class documents which session/date the bug was found.

## Session File Mining

- Session files live at `~/.pi/agent/sessions/--home-ishan-mkdocs-liveedit--/`
- JSONL format: `obj.message.role` + `obj.message.content` (list of `{type: "text", text: "..."}`)
- Useful for extracting bug history, design decisions, and real-world markdown that triggered failures

## Key Files

- `src/mkdocs_liveedit/sourcemap.py` — block parser + all merge passes (most bug-prone code)
- `src/mkdocs_liveedit/plugin.py` — `_BlockAnnotator` HTML walker + MkDocs plugin hooks
- `src/mkdocs_liveedit/api.py` — WSGI middleware for `/liveedit/*` save/source/nav routes
- `~/dfm/docs/setup.md` — real-world test document that has triggered multiple bugs (tabs, admonitions, lists)

## Patterns That Trigger Bugs

When testing, use markdown that combines these features — they interact badly:
- Admonitions with fenced code blocks inside (fence-close flush + admonition merge)
- Tab sets at the end of admonitions (nested indentation)
- HTML comments between blocks (phantom block injection)
- Blank-line-separated list items (list merge needed)
- Content immediately after a closing fence with no blank line
- `---` horizontal rules (void element `<hr>`)
- Frontmatter with multi-line YAML values (offset calculation)
