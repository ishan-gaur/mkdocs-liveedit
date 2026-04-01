"""WSGI middleware for /liveedit/* API routes."""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from ruamel.yaml import YAML

if TYPE_CHECKING:
    from typing import Any, Callable

log = logging.getLogger("mkdocs.plugins.liveedit")


class LiveEditAPI:
    """WSGI middleware that intercepts /liveedit/* requests."""

    def __init__(self, app: Callable, docs_dir: str, config_file: str):
        self.app = app
        self.docs_dir = os.path.realpath(docs_dir)
        self.config_file = config_file
        self.server = None  # Set per-request by the _serve_request patch

    def __call__(self, environ: dict, start_response: Callable) -> Any:
        path = environ.get("PATH_INFO", "")
        log.debug(f"LiveEditAPI: intercepting {environ.get('REQUEST_METHOD')} {path}")

        if path == "/liveedit/save":
            return self._handle_save(environ, start_response)
        elif path == "/liveedit/source":
            return self._handle_source(environ, start_response)
        elif path == "/liveedit/nav":
            return self._handle_nav(environ, start_response)

        if self.app is not None:
            return self.app(environ, start_response)
        return self._error_response(start_response, "Not found", "404 Not Found")

    def _read_json_body(self, environ: dict) -> dict:
        content_length = int(environ.get("CONTENT_LENGTH", 0))
        body = environ["wsgi.input"].read(content_length)
        return json.loads(body)

    def _json_response(self, start_response: Callable, data: dict, status: str = "200 OK") -> list[bytes]:
        body = json.dumps(data).encode("utf-8")
        start_response(
            status,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store, no-cache, must-revalidate"),
                ("Pragma", "no-cache"),
            ],
        )
        return [body]

    def _error_response(self, start_response: Callable, msg: str, status: str = "400 Bad Request") -> list[bytes]:
        return self._json_response(start_response, {"error": msg}, status)

    def _trigger_rebuild(self):
        """Notify the LiveReloadServer's build loop that a rebuild is needed.

        Goes through the server's normal _build_loop mechanism rather than calling
        builder() directly — this deduplicates with watchdog-triggered rebuilds
        (which also fire since we just wrote the file) and avoids concurrent builds.
        """
        server = self.server
        if server is None:
            return
        try:
            with server._rebuild_cond:
                server._want_rebuild = True
                server._rebuild_cond.notify_all()
        except Exception as e:
            log.error(f"LiveEdit: failed to trigger rebuild: {e}")

    def _validate_file_path(self, file_path: str) -> str | None:
        """Resolve file path and validate it's under docs_dir. Returns resolved path or None."""
        resolved = os.path.realpath(os.path.join(self.docs_dir, file_path))
        if not resolved.startswith(self.docs_dir + os.sep) and resolved != self.docs_dir:
            return None
        if not os.path.isfile(resolved):
            return None
        return resolved

    def _handle_save(self, environ: dict, start_response: Callable) -> list[bytes]:
        try:
            data = self._read_json_body(environ)
        except (json.JSONDecodeError, ValueError) as e:
            return self._error_response(start_response, f"Invalid JSON: {e}")

        file_path = data.get("file", "")
        start_line = data.get("start_line")
        end_line = data.get("end_line")
        content = data.get("content", "")

        if not file_path or start_line is None or end_line is None:
            return self._error_response(start_response, "Missing required fields: file, start_line, end_line")

        resolved = self._validate_file_path(file_path)
        if resolved is None:
            return self._error_response(start_response, "Invalid file path", "403 Forbidden")

        try:
            with open(resolved, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Convert 1-indexed inclusive range to 0-indexed
            start_idx = start_line - 1
            end_idx = end_line  # end_line is inclusive, so slice up to end_line

            if start_idx < 0 or end_idx > len(lines):
                return self._error_response(start_response, "Line range out of bounds")

            # Replace the line range with new content
            new_content_lines = content.split("\n")
            # Ensure each line ends with newline except possibly the last
            new_lines = [line + "\n" for line in new_content_lines[:-1]]
            if new_content_lines:
                last = new_content_lines[-1]
                # If the original last replaced line had a newline, keep it
                if end_idx <= len(lines) and end_idx > 0 and lines[end_idx - 1].endswith("\n"):
                    new_lines.append(last + "\n")
                else:
                    new_lines.append(last)

            lines[start_idx:end_idx] = new_lines

            with open(resolved, "w", encoding="utf-8") as f:
                f.writelines(lines)

            log.info(f"LiveEdit: saved {file_path} lines {start_line}-{end_line}")
            self._trigger_rebuild()
            return self._json_response(start_response, {"ok": True})

        except Exception as e:
            log.error(f"LiveEdit save error: {e}")
            return self._error_response(start_response, str(e), "500 Internal Server Error")

    def _handle_source(self, environ: dict, start_response: Callable) -> list[bytes]:
        """GET /liveedit/source?file=...&start=N&end=M — return raw markdown for a block."""
        from urllib.parse import parse_qs

        qs = parse_qs(environ.get("QUERY_STRING", ""))

        file_path = qs.get("file", [""])[0]
        try:
            start_line = int(qs.get("start", [0])[0])
            end_line = int(qs.get("end", [0])[0])
        except ValueError:
            return self._error_response(start_response, "Invalid start/end parameters")

        if not file_path or not start_line or not end_line:
            return self._error_response(start_response, "Missing required params: file, start, end")

        resolved = self._validate_file_path(file_path)
        if resolved is None:
            return self._error_response(start_response, "Invalid file path", "403 Forbidden")

        try:
            with open(resolved, "r", encoding="utf-8") as f:
                lines = f.readlines()

            start_idx = start_line - 1
            end_idx = end_line
            source = "".join(lines[start_idx:end_idx])

            return self._json_response(start_response, {"source": source})
        except Exception as e:
            return self._error_response(start_response, str(e), "500 Internal Server Error")

    def _handle_nav(self, environ: dict, start_response: Callable) -> list[bytes]:
        """POST /liveedit/nav — rewrite nav section in mkdocs.yml."""
        try:
            data = self._read_json_body(environ)
        except (json.JSONDecodeError, ValueError) as e:
            return self._error_response(start_response, f"Invalid JSON: {e}")

        nav = data.get("nav")
        if nav is None:
            return self._error_response(start_response, "Missing 'nav' field")

        try:
            yaml = YAML()
            yaml.preserve_quotes = True

            with open(self.config_file, "r", encoding="utf-8") as f:
                config = yaml.load(f)

            config["nav"] = nav

            with open(self.config_file, "w", encoding="utf-8") as f:
                yaml.dump(config, f)

            log.info("LiveEdit: updated nav in mkdocs.yml")
            self._trigger_rebuild()
            return self._json_response(start_response, {"ok": True})

        except Exception as e:
            log.error(f"LiveEdit nav error: {e}")
            return self._error_response(start_response, str(e), "500 Internal Server Error")
