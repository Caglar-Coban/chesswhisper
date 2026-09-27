import json
import threading

import pytest
from conftest import CsrfClient

from chesswhisper import game_sources
from chesswhisper.game_sources import FetchResult, GameSourceUnavailable
from chesswhisper.web import jobs, sync
from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db, session_scope
from chesswhisper.web.models import AnalysisJob, GameSync, User


def _game(n: int, link: bool = True) -> str:
    site = f'[Site "https://lichess.org/game{n}"]\n' if link else '[Site "?"]\n'
    return f'[White "Alice"]\n[Black "Bob{n}"]\n{site}[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'


class FakeSource:
    """Serves games newest first, like the real fetchers."""

    def __init__(self, *numbers):
        self.numbers = list(numbers)
        self.calls = []

    def add(self, *numbers):
        self.numbers = list(numbers) + self.numbers

    def __call__(self, platform, username, max_games):
        self.calls.append((platform, username, max_games))
        pgn = "\n".join(_game(n) for n in self.numbers[:max_games])
        return FetchResult(pgn_text=pgn, player="Alice")


@pytest.fixture
def user_id(tmp_path):
    init_db(tmp_path / "sync.db")
    with session_scope() as db:
        user = User(username="alice", password_hash="x")
        db.add(user)
        db.flush()
        return user.id


def _job_games(job_id):
    with session_scope() as db:
        return db.get(AnalysisJob, job_id).pgn_text.count("[White ")


def test_the_first_check_only_takes_note_of_the_games_already_there(user_id):
    source, queued = FakeSource(2, 1), []
    sync.link_account(user_id, "lichess", "alice", "tr")

    assert sync.check(user_id, queued.append, source) == "baseline"
    assert queued == []
    assert source.calls == [("lichess", "alice", sync.SYNC_FETCH_GAMES)]
    status = sync.get_sync(user_id)
    assert status.baseline_taken and status.last_checked_at is not None


def test_new_games_are_queued_once_without_llm_commentary(user_id):
    source, queued = FakeSource(2, 1), []
    sync.link_account(user_id, "lichess", "alice", "tr")
    sync.check(user_id, queued.append, source)

    source.add(4, 3)
    assert sync.check(user_id, queued.append, source) == "queued"

    [job_id] = queued
    inputs = jobs.job_inputs(job_id)
    assert (inputs.llm, inputs.lang, inputs.player) == ("none", "tr", "Alice")
    assert _job_games(job_id) == 2
    assert "Bob3" in inputs.pgn_text and "Bob1" not in inputs.pgn_text
    status = sync.get_sync(user_id)
    assert (status.last_new_games, status.last_job_id) == (2, job_id)

    jobs.mark_error(job_id, "stop")  # finish it so the next check isn't held back
    assert sync.check(user_id, queued.append, source) == "none"
    assert len(queued) == 1


def test_a_running_analysis_holds_the_check_back_without_losing_games(user_id):
    source, queued = FakeSource(1), []
    sync.link_account(user_id, "lichess", "alice", "en")
    sync.check(user_id, queued.append, source)
    source.add(2)
    running = jobs.create_job(user_id)

    assert sync.check(user_id, queued.append, source) == "busy"
    assert queued == []

    jobs.mark_error(running.id, "stop")
    assert sync.check(user_id, queued.append, source) == "queued"


def test_fetch_errors_are_kept_and_cleared_by_the_next_good_check(user_id):
    def broken(*args):
        raise GameSourceUnavailable("Lichess is rate limiting requests")

    sync.link_account(user_id, "lichess", "alice", "en")
    assert sync.check(user_id, [].append, broken) == "error"
    assert "rate limiting" in sync.get_sync(user_id).last_error
    assert not sync.get_sync(user_id).baseline_taken

    sync.check(user_id, [].append, FakeSource(1))
    assert sync.get_sync(user_id).last_error is None


def test_relinking_starts_over_and_unlinking_stops_the_checks(user_id):
    source = FakeSource(1)
    sync.link_account(user_id, "lichess", "alice", "en")
    sync.check(user_id, [].append, source)

    sync.link_account(user_id, "chesscom", "alice2", "en")
    assert not sync.get_sync(user_id).baseline_taken
    sync.unlink_account(user_id)

    assert sync.get_sync(user_id) is None
    assert sync.check(user_id, [].append, source) == "unlinked"


def test_seen_games_are_capped_newest_first(user_id, monkeypatch):
    monkeypatch.setattr(sync, "MAX_SEEN_KEYS", 3)
    source = FakeSource(2, 1)
    sync.link_account(user_id, "lichess", "alice", "en")
    sync.check(user_id, [].append, source)
    source.add(4, 3)
    sync.check(user_id, lambda job_id: jobs.mark_error(job_id, "stop"), source)

    with session_scope() as db:
        seen = json.loads(db.get(GameSync, user_id).seen_keys_json)
    assert seen == [f"https://lichess.org/game{n}" for n in (4, 3, 2)]


def test_games_without_a_link_are_told_apart_by_their_content():
    import io

    import chess.pgn

    first = chess.pgn.read_game(io.StringIO(_game(1, link=False)))
    same = chess.pgn.read_game(io.StringIO(_game(1, link=False)))
    other = chess.pgn.read_game(io.StringIO(_game(2, link=False)))

    assert sync.game_key(first) == sync.game_key(same) != sync.game_key(other)
    assert sync.game_key(first).startswith("sha1:")


@pytest.mark.parametrize(("value", "minutes"), [(None, 60), ("0", 0), ("-1", 0), ("2", 5), ("90", 90), ("x", 60)])
def test_sync_interval_comes_from_the_environment(monkeypatch, value, minutes):
    if value is None:
        monkeypatch.delenv(sync.SYNC_MINUTES_ENV, raising=False)
    else:
        monkeypatch.setenv(sync.SYNC_MINUTES_ENV, value)
    assert sync.sync_minutes_from_env() == minutes


def test_the_scheduler_checks_every_linked_account(user_id, monkeypatch):
    checked = threading.Event()
    monkeypatch.setattr(sync, "check", lambda uid, enqueue, fetch=None: checked.set() if uid == user_id else None)
    sync.link_account(user_id, "lichess", "alice", "en")
    scheduler = sync.SyncScheduler([].append, interval_seconds=60, first_delay=0)

    scheduler.start()
    try:
        assert checked.wait(5)
    finally:
        scheduler.stop()


def test_a_zero_interval_scheduler_never_starts():
    scheduler = sync.SyncScheduler([].append, interval_seconds=0, first_delay=0)
    scheduler.start()
    assert scheduler._thread is None


# --- settings page ----------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    init_db(tmp_path / "sync_web.db")
    monkeypatch.setattr("chesswhisper.web.app._enqueue", lambda job_id: None)
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def test_settings_offer_to_link_an_account(client):
    page = client.get("/settings").text

    assert 'action="/settings/sync"' in page
    assert "Analyse new games automatically" in page


def test_linking_checks_at_once_and_shows_the_account(client, monkeypatch):
    source = FakeSource(1)
    monkeypatch.setattr(game_sources, "fetch_pgn", source)

    page = client.post("/settings/sync", data={"platform": "lichess", "username": " alice ", "lang": "en"}).text

    assert source.calls == [("lichess", "alice", sync.SYNC_FETCH_GAMES)]
    assert "Account linked" in page
    assert "Linked: Lichess user alice" in page
    assert 'action="/settings/sync/check"' in page

    source.add(2)
    page = client.post("/settings/sync/check").text
    assert "their analysis is queued" in page
    assert "1 new game(s) found" in page


def test_linking_reports_an_unknown_account(client, monkeypatch):
    def unknown(*args):
        raise game_sources.GameFetchError("Lichess user 'nobody' not found.")

    monkeypatch.setattr(game_sources, "fetch_pgn", unknown)

    page = client.post("/settings/sync", data={"platform": "lichess", "username": "nobody", "lang": "en"}).text

    assert "Couldn&#39;t fetch the games: Lichess user &#39;nobody&#39; not found." in page


def test_linking_rejects_bad_input(client, monkeypatch):
    monkeypatch.setattr(game_sources, "fetch_pgn", FakeSource(1))

    assert client.post("/settings/sync", data={"platform": "fics", "username": "alice"}).status_code == 400
    assert client.post("/settings/sync", data={"platform": "lichess", "username": "alice", "lang": "de"}).status_code == 400
    page = client.post("/settings/sync", data={"platform": "lichess", "username": "a b"}).text
    assert "Invalid username" in page
    assert "Linked:" not in page


def test_unlinking_removes_the_account(client, monkeypatch):
    monkeypatch.setattr(game_sources, "fetch_pgn", FakeSource(1))
    client.post("/settings/sync", data={"platform": "lichess", "username": "alice", "lang": "en"})

    page = client.post("/settings/sync/delete").text

    assert "Account unlinked." in page
    assert 'action="/settings/sync"' in page


def test_deleting_a_user_deletes_their_link(client, monkeypatch):
    monkeypatch.setattr(game_sources, "fetch_pgn", FakeSource(1))
    client.post("/settings/sync", data={"platform": "lichess", "username": "alice", "lang": "en"})
    db = next(get_db())
    try:
        db.delete(db.query(User).filter_by(username="alice").one())
        db.commit()
        assert db.query(GameSync).count() == 0
    finally:
        db.close()
