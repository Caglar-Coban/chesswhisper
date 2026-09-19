import re

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
