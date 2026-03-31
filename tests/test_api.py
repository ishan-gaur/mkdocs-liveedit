"""Tests for the LiveEdit API middleware."""

import json
import os
import tempfile

import pytest

from mkdocs_liveedit.api import LiveEditAPI


def make_environ(method="GET", path="/", body=None, query_string=""):
    """Create a minimal WSGI environ dict."""
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


def passthrough_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"passthrough"]


@pytest.fixture
def docs_dir():
    with tempfile.TemporaryDirectory() as d:
        # Create a sample markdown file
        md_path = os.path.join(d, "test.md")
        with open(md_path, "w") as f:
            f.write("# Title\n\nFirst paragraph.\n\nSecond paragraph.\n")
        yield d


@pytest.fixture
def config_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write("site_name: Test\nnav:\n  - Home: index.md\n")
        f.flush()
        yield f.name
    os.unlink(f.name)


class TestSaveEndpoint:
    def test_save_replaces_lines(self, docs_dir, config_file):
        api = LiveEditAPI(passthrough_app, docs_dir, config_file)

        body = {
            "file": "test.md",
            "start_line": 3,
            "end_line": 3,
            "content": "Updated paragraph.",
        }
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        result = api(environ, sr)

        assert sr.status == "200 OK"
        data = json.loads(b"".join(result))
        assert data["ok"] is True

        with open(os.path.join(docs_dir, "test.md")) as f:
            content = f.read()
        assert "Updated paragraph." in content
        assert "First paragraph." not in content

    def test_save_path_traversal_blocked(self, docs_dir, config_file):
        api = LiveEditAPI(passthrough_app, docs_dir, config_file)

        body = {
            "file": "../../../etc/passwd",
            "start_line": 1,
            "end_line": 1,
            "content": "hacked",
        }
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        assert sr.status == "403 Forbidden"

    def test_save_missing_fields(self, docs_dir, config_file):
        api = LiveEditAPI(passthrough_app, docs_dir, config_file)

        body = {"file": "test.md"}
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        assert sr.status == "400 Bad Request"

    def test_save_nonexistent_file(self, docs_dir, config_file):
        api = LiveEditAPI(passthrough_app, docs_dir, config_file)

        body = {
            "file": "nonexistent.md",
            "start_line": 1,
            "end_line": 1,
            "content": "x",
        }
        environ = make_environ("POST", "/liveedit/save", body)
        sr = FakeStartResponse()
        api(environ, sr)

        assert sr.status == "403 Forbidden"


class TestSourceEndpoint:
    def test_get_source(self, docs_dir, config_file):
        api = LiveEditAPI(passthrough_app, docs_dir, config_file)

        environ = make_environ("GET", "/liveedit/source", query_string="file=test.md&start=1&end=1")
        sr = FakeStartResponse()
        result = api(environ, sr)

        assert sr.status == "200 OK"
        data = json.loads(b"".join(result))
        assert data["source"] == "# Title\n"

    def test_get_source_range(self, docs_dir, config_file):
        api = LiveEditAPI(passthrough_app, docs_dir, config_file)

        environ = make_environ("GET", "/liveedit/source", query_string="file=test.md&start=3&end=5")
        sr = FakeStartResponse()
        result = api(environ, sr)

        assert sr.status == "200 OK"
        data = json.loads(b"".join(result))
        assert "First paragraph." in data["source"]
        assert "Second paragraph." in data["source"]


class TestPassthrough:
    def test_non_liveedit_paths_pass_through(self, docs_dir, config_file):
        api = LiveEditAPI(passthrough_app, docs_dir, config_file)

        environ = make_environ("GET", "/some/other/path")
        sr = FakeStartResponse()
        result = api(environ, sr)

        assert sr.status == "200 OK"
        assert b"passthrough" in b"".join(result)


class TestNavEndpoint:
    def test_update_nav(self, docs_dir, config_file):
        api = LiveEditAPI(passthrough_app, docs_dir, config_file)

        body = {
            "nav": [
                {"Getting Started": "getting-started.md"},
                {"Guide": [{"Setup": "guide/setup.md"}]},
            ]
        }
        environ = make_environ("POST", "/liveedit/nav", body)
        sr = FakeStartResponse()
        api(environ, sr)

        assert sr.status == "200 OK"

        with open(config_file) as f:
            content = f.read()
        assert "Getting Started" in content
        assert "guide/setup.md" in content
