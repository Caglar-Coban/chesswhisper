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
    """chesswhisper.web.app hands analysis jobs to a worker pool; tests need
    deterministic, synchronous execution instead of racing its threads.
    This patches that one seam so a job runs as soon as it is queued -- but
    only if the module is already imported (checked via sys.modules, which
    never triggers an import itself), so a CLI-only test run never has to
    import or initialize the FastAPI app.
    """
    module = sys.modules.get("chesswhisper.web.app")
    if module is not None:
        monkeypatch.setattr(module, "_enqueue", module._run_analysis_job)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    """Keep every test away from the real per-user data directory, where
    the evaluation cache, keys and database would otherwise leak between
    tests and into the developer's own install.
    """
    monkeypatch.setenv("CHESSWHISPER_DATA_DIR", str(tmp_path_factory.mktemp("data")))


@pytest.fixture(autouse=True)
def _reset_login_rate_limit():
    """The login rate limiter counts attempts per client address, and every
    TestClient comes from the same one."""
    module = sys.modules.get("chesswhisper.web.app")
    if module is not None:
        module.login_limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _no_database_url_from_the_environment(monkeypatch):
    """A developer's own CHESSWHISPER_DATABASE_URL must not point tests at their database."""
    monkeypatch.delenv("CHESSWHISPER_DATABASE_URL", raising=False)


# Set to a PostgreSQL URL to run the web tests against PostgreSQL instead of
# SQLite files. Every test starts from an empty schema in that database, so
# never point it at one holding data. Tests marked sqlite_only are skipped.
TEST_DATABASE_URL_ENV = "CHESSWHISPER_TEST_DATABASE_URL"


def pytest_configure(config):
    config.addinivalue_line("markers", "sqlite_only: the test inspects SQLite files directly")


@pytest.fixture(autouse=True)
def _postgres_instead_of_sqlite(request, monkeypatch):
    import os

    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        yield
        return
    if request.node.get_closest_marker("sqlite_only"):
        pytest.skip("inspects SQLite files directly")

    from sqlalchemy import make_url

    from chesswhisper.web import db as web_db

    url = make_url(url).set(drivername="postgresql+psycopg")
    if web_db._engine is not None:
        web_db._engine.dispose()
    reset = web_db._create_engine(url)
    with reset.begin() as connection:
        # A session an earlier test left open would hold its locks and block the drop forever.
        connection.exec_driver_sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
        connection.exec_driver_sql("SET lock_timeout = '10s'")
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")
    reset.dispose()

    engines = []

    def pg_engine(db_path=None):
        engine = web_db._create_engine(url)
        engines.append(engine)
        return engine

    monkeypatch.setattr(web_db, "get_engine", pg_engine)
    yield
    for engine in engines:
        engine.dispose()
