/**
 * mkdocs-liveedit — inline editing for mkdocs serve
 *
 * Injected into every page during serve mode. Handles:
 * - Double-click / icon click to edit blocks
 * - Save via Cmd+Enter or button
 * - Sidebar drag-and-drop nav reordering
 */
(function () {
  "use strict";

  // Avoid double-init (Material instant nav may re-run scripts)
  if (window.__liveedit_initialized) return;
  window.__liveedit_initialized = true;

  // ── Helpers ──────────────────────────────────────────────

  function showStatus(message, type) {
    let el = document.querySelector(".liveedit-status");
    if (!el) {
      el = document.createElement("div");
      el.className = "liveedit-status";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.className = "liveedit-status visible " + type;
    clearTimeout(el._timeout);
    el._timeout = setTimeout(function () {
      el.classList.remove("visible");
    }, 3000);
  }

  async function fetchSource(file, start, end) {
    const params = new URLSearchParams({
      file: file,
      start: start,
      end: end,
      _t: Date.now(),
    });
    const resp = await fetch("/liveedit/source?" + params.toString());
    if (!resp.ok) throw new Error("Failed to fetch source: " + resp.status);
    const data = await resp.json();
    return data.source;
  }

  async function saveBlock(file, startLine, endLine, content) {
    const resp = await fetch("/liveedit/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file: file,
        start_line: startLine,
        end_line: endLine,
        content: content,
      }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(function () {
        return {};
      });
      throw new Error(data.error || "Save failed: " + resp.status);
    }
    return resp.json();
  }

  async function saveNav(nav) {
    const resp = await fetch("/liveedit/nav", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nav: nav }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(function () {
        return {};
      });
      throw new Error(data.error || "Nav save failed: " + resp.status);
    }
    return resp.json();
  }

  function autoResize(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = textarea.scrollHeight + 4 + "px";
  }

  // ── Edit overlay ─────────────────────────────────────────

  let activeOverlay = null;

  function closeOverlay() {
    if (activeOverlay) {
      const block = activeOverlay._block;
      block.classList.remove("liveedit-editing");
      activeOverlay.remove();
      activeOverlay = null;
    }
  }

  async function openEditor(blockEl) {
    // Close any existing editor
    closeOverlay();

    const file = blockEl.getAttribute("data-liveedit-file");
    const lines = blockEl.getAttribute("data-liveedit-lines");
    if (!file || !lines) return;

    const parts = lines.split("-");
    const startLine = parseInt(parts[0], 10);
    const endLine = parseInt(parts[1], 10);

    // Fetch source
    let source;
    try {
      source = await fetchSource(file, startLine, endLine);
    } catch (e) {
      showStatus("Failed to load source: " + e.message, "error");
      return;
    }

    blockEl.classList.add("liveedit-editing");

    // Create overlay
    const overlay = document.createElement("div");
    overlay.className = "liveedit-overlay";
    overlay._block = blockEl;
    activeOverlay = overlay;

    // Textarea
    const textarea = document.createElement("textarea");
    textarea.className = "liveedit-textarea";
    textarea.value = source;
    textarea.spellcheck = false;
    overlay.appendChild(textarea);

    // Toolbar
    const toolbar = document.createElement("div");
    toolbar.className = "liveedit-toolbar";

    const hint = document.createElement("span");
    hint.className = "liveedit-hint";
    const isMac = navigator.platform.toUpperCase().indexOf("MAC") >= 0;
    hint.textContent = (isMac ? "⌘" : "Ctrl") + "+Enter to save";
    toolbar.appendChild(hint);

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "liveedit-cancel";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", function () {
      closeOverlay();
    });
    toolbar.appendChild(cancelBtn);

    const saveBtn = document.createElement("button");
    saveBtn.className = "liveedit-save";
    saveBtn.textContent = "Save";
    saveBtn.addEventListener("click", function () {
      doSave();
    });
    toolbar.appendChild(saveBtn);

    overlay.appendChild(toolbar);
    blockEl.appendChild(overlay);

    // Auto-resize textarea
    setTimeout(function () {
      autoResize(textarea);
      textarea.focus();
    }, 0);

    textarea.addEventListener("input", function () {
      autoResize(textarea);
    });

    // Cmd+Enter to save, Escape to cancel
    textarea.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        doSave();
      } else if (e.key === "Escape") {
        e.preventDefault();
        closeOverlay();
      }
    });

    async function doSave() {
      const newContent = textarea.value;
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving...";

      try {
        await saveBlock(file, startLine, endLine, newContent);
        showStatus("Saved! Rebuilding...", "success");
        closeOverlay();
        // Reload after a short delay to let the rebuild finish.
        // The server triggers an async rebuild on save; we wait for it.
        setTimeout(function () {
          window.location.reload();
        }, 1500);
      } catch (e) {
        showStatus("Save failed: " + e.message, "error");
        saveBtn.disabled = false;
        saveBtn.textContent = "Save";
      }
    }
  }

  // ── Initialize editable blocks ───────────────────────────

  function initBlocks() {
    const blocks = document.querySelectorAll("[data-liveedit-block]");
    blocks.forEach(function (block) {
      if (block._liveeditInit) return;
      block._liveeditInit = true;

      // Add edit icon
      const icon = document.createElement("span");
      icon.className = "liveedit-icon";
      icon.innerHTML = "&#9998;"; // ✎ pencil
      icon.title = "Edit this block";
      icon.addEventListener("click", function (e) {
        e.stopPropagation();
        openEditor(block);
      });
      block.appendChild(icon);

      // Double-click to edit
      block.addEventListener("dblclick", function (e) {
        // Don't trigger if user is selecting text
        if (window.getSelection().toString().length > 0) return;
        e.preventDefault();
        openEditor(block);
      });
    });
  }

  // ── Sidebar drag-and-drop ────────────────────────────────

  function initNavDragDrop() {
    // Find sidebar nav items (Material theme structure)
    const navItems = document.querySelectorAll(
      ".md-nav--primary > .md-nav__list > .md-nav__item"
    );
    if (navItems.length === 0) return;

    navItems.forEach(function (item) {
      if (item._liveeditDrag) return;
      item._liveeditDrag = true;

      const handle = document.createElement("span");
      handle.className = "liveedit-drag-handle";
      handle.innerHTML = "⠿";
      handle.title = "Drag to reorder";

      item.setAttribute("draggable", "true");
      item.insertBefore(handle, item.firstChild);

      item.addEventListener("dragstart", function (e) {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", "");
        item.classList.add("liveedit-dragging");
      });

      item.addEventListener("dragend", function () {
        item.classList.remove("liveedit-dragging");
        document
          .querySelectorAll(".liveedit-drop-target")
          .forEach(function (el) {
            el.classList.remove("liveedit-drop-target");
          });
      });

      item.addEventListener("dragover", function (e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        item.classList.add("liveedit-drop-target");
      });

      item.addEventListener("dragleave", function () {
        item.classList.remove("liveedit-drop-target");
      });

      item.addEventListener("drop", function (e) {
        e.preventDefault();
        item.classList.remove("liveedit-drop-target");

        const dragging = document.querySelector(".liveedit-dragging");
        if (!dragging || dragging === item) return;

        const list = item.parentNode;
        const rect = item.getBoundingClientRect();
        const midY = rect.top + rect.height / 2;

        if (e.clientY < midY) {
          list.insertBefore(dragging, item);
        } else {
          list.insertBefore(dragging, item.nextSibling);
        }

        // Serialize nav from DOM and save
        const nav = serializeNav(list);
        if (nav) {
          saveNav(nav)
            .then(function () {
              showStatus("Nav updated! Rebuilding...", "success");
            })
            .catch(function (err) {
              showStatus("Nav save failed: " + err.message, "error");
            });
        }
      });
    });
  }

  function serializeNav(list) {
    const items = list.querySelectorAll(":scope > .md-nav__item");
    const nav = [];

    items.forEach(function (item) {
      const link = item.querySelector(":scope > .md-nav__link");
      if (!link) return;

      const title = link.textContent.trim();
      const href = link.getAttribute("href");

      // Check for sub-nav
      const subList = item.querySelector(":scope > .md-nav > .md-nav__list");
      if (subList) {
        const children = serializeNav(subList);
        const entry = {};
        entry[title] = children;
        nav.push(entry);
      } else if (href) {
        // Convert href to relative md path
        let mdPath = href.replace(/^\//, "").replace(/\/$/, "");
        if (mdPath === "" || mdPath === ".") mdPath = "index.md";
        else if (!mdPath.endsWith(".md")) mdPath += ".md";

        const entry = {};
        entry[title] = mdPath;
        nav.push(entry);
      }
    });

    return nav.length > 0 ? nav : null;
  }

  // ── Escape key closes overlay globally ───────────────────

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && activeOverlay) {
      closeOverlay();
    }
  });

  // Click outside overlay to close
  document.addEventListener("mousedown", function (e) {
    if (activeOverlay && !activeOverlay.contains(e.target)) {
      const block = activeOverlay._block;
      if (!block.contains(e.target)) {
        closeOverlay();
      }
    }
  });

  // ── Init ─────────────────────────────────────────────────

  function init() {
    initBlocks();
    initNavDragDrop();
  }

  // Run on DOMContentLoaded
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Material theme instant navigation support:
  // Hook into Material's document$ observable if available
  if (typeof document$ !== "undefined" && document$.subscribe) {
    document$.subscribe(function () {
      // Re-init after Material SPA navigation
      setTimeout(init, 100);
    });
  } else {
    // Fallback: MutationObserver on content container
    var observer = new MutationObserver(function (mutations) {
      var shouldReinit = mutations.some(function (m) {
        return m.addedNodes.length > 0;
      });
      if (shouldReinit) {
        setTimeout(init, 50);
      }
    });

    var contentEl =
      document.querySelector(".md-content") ||
      document.querySelector("[role='main']") ||
      document.querySelector("main");
    if (contentEl) {
      observer.observe(contentEl, { childList: true, subtree: true });
    }
  }
})();
