import re
import sys

import pytest
from fastapi.testclient import TestClient

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]*)"')

# Pages that always render a csrf_token hidden field, tried in order until
# one doesn't 30x (anonymous sessions redirect off "/", logged-in sessions
# redirect off "/login").
_TOKEN_SOURCE_PATHS = ("/login", "/", "/settings")


class CsrfClient(TestClient):
    """A TestClient that transparently attaches a valid csrf_token to POSTs.

    Every state-changing route requires a csrf_token form field matching the
    one chesswhisper stashes in the session. Tests exercise those routes
    directly without ever rendering the page that would normally carry the
    token, so this client fetches one over a plain GET and merges it into
    the POST body unless the test already supplied its own.
    """

    def post(self, url, *args, **kwargs):
        data = dict(kwargs.get("data") or {})
        if "csrf_token" not in data:
            data["csrf_token"] = self._fetch_csrf_token()
        kwargs["data"] = data
        return super().post(url, *args, **kwargs)

    def _fetch_csrf_token(self) -> str:
        for path in _TOKEN_SOURCE_PATHS:
            response = super().get(path)
            match = _CSRF_RE.search(response.text)
            if match:
                return match.group(1)
        raise AssertionError("could not obtain a csrf_token from any known page")


@pytest.fixture(autouse=True)
def _run_background_jobs_inline(monkeypatch):
    """chesswhisper.web.app starts analysis jobs on a background thread;
    tests need deterministic, synchronous execution instead of racing a
    real thread. This patches that one seam -- but only if the module is
    already imported (checked via sys.modules, which never triggers an
    import itself), so a CLI-only test run never has to import or
    initialize the FastAPI app.
    """
    module = sys.modules.get("chesswhisper.web.app")
    if module is not None:
        monkeypatch.setattr(module, "_start_background", lambda target: target())


@pytest.fixture(autouse=True)
def _isolate_job_registry():
    """The job registry is process-wide and every test database's first user
    has id 1, so a job one test leaves "running" would otherwise count as
    that user's in-progress analysis in the next test.
    """
    module = sys.modules.get("chesswhisper.web.jobs")
    if module is not None:
        module._jobs.clear()
    yield
    module = sys.modules.get("chesswhisper.web.jobs")
    if module is not None:
        module._jobs.clear()
