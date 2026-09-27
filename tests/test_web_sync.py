import io
import json
import threading

import chess.pgn
import pytest
from conftest import CsrfClient

from chesswhisper import game_sources
from chesswhisper.game_sources import FetchResult, GameSourceUnavailable
from chesswhisper.web import jobs, sync
from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db, session_scope
from chesswhisper.web.models import AnalysisJob, AnalysisRecord, LinkedAccount, User


def _game(n: int, link: bool = True, player: str = "Alice") -> str:
    site = f'[Site "https://lichess.org/game{n}"]\n' if link else '[Site "?"]\n'
    return f'[White "{player}"]\n[Black "Bob{n}"]\n{site}[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'


class FakeSource:
    """Serves games newest first, like the real fetchers; one list per username."""

    def __init__(self, *numbers, **per_user):
        self.games = {"alice": list(numbers), **{name: list(n) for name, n in per_user.items()}}
        self.calls = []

    def add(self, *numbers, username="alice"):
        self.games[username] = list(numbers) + self.games.get(username, [])

    def __call__(self, platform, username, max_games):
        self.calls.append((platform, username, max_games))
        player = username.capitalize()
        pgn = "\n".join(_game(n, player=player) for n in self.games.get(username, [])[:max_games])
        return FetchResult(pgn_text=pgn, player=player)


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


def _finish(job_id):
    jobs.mark_error(job_id, "stop")  # so the next check isn't held back


def test_the_first_check_only_takes_note_of_the_games_already_there(user_id):
    source, queued = FakeSource(2, 1), []
    account = sync.link_account(user_id, "lichess", "alice", "tr")

    assert sync.check(account, queued.append, source) == "baseline"
    assert queued == []
    assert source.calls == [("lichess", "alice", sync.SYNC_FETCH_GAMES)]
    status = sync.get_account(account)
    assert status.baseline_taken and status.last_checked_at is not None


def test_new_games_are_queued_once_without_llm_commentary(user_id):
    source, queued = FakeSource(2, 1), []
    account = sync.link_account(user_id, "lichess", "alice", "tr")
    sync.check(account, queued.append, source)

    source.add(4, 3)
    assert sync.check(account, queued.append, source) == "queued"

    [job_id] = queued
    inputs = jobs.job_inputs(job_id)
    assert (inputs.llm, inputs.lang, inputs.player, inputs.origin) == ("none", "tr", "Alice", "sync")
    assert _job_games(job_id) == 2
    assert "Bob3" in inputs.pgn_text and "Bob1" not in inputs.pgn_text
    status = sync.get_account(account)
    assert (status.last_new_games, status.last_job_id) == (2, job_id)

    _finish(job_id)
    assert sync.check(account, queued.append, source) == "none"
    assert len(queued) == 1


def test_a_running_analysis_holds_the_check_back_without_losing_games(user_id):
    source, queued = FakeSource(1), []
    account = sync.link_account(user_id, "lichess", "alice", "en")
    sync.check(account, queued.append, source)
    source.add(2)
    running = jobs.create_job(user_id)

    assert sync.check(account, queued.append, source) == "busy"
    assert queued == []

    _finish(running.id)
    assert sync.check(account, queued.append, source) == "queued"


def test_fetch_errors_are_kept_and_cleared_by_the_next_good_check(user_id):
    def broken(*args):
        raise GameSourceUnavailable("Lichess is rate limiting requests")

    account = sync.link_account(user_id, "lichess", "alice", "en")
    assert sync.check(account, [].append, broken) == "error"
    assert "rate limiting" in sync.get_account(account).last_error
    assert not sync.get_account(account).baseline_taken

    sync.check(account, [].append, FakeSource(1))
    assert sync.get_account(account).last_error is None


def test_several_accounts_are_checked_separately(user_id):
    source, queued = FakeSource(1, bob=[10]), []
    alice = sync.link_account(user_id, "lichess", "alice", "en")
    bob = sync.link_account(user_id, "chesscom", "bob", "tr")

    assert sync.check_user(user_id, queued.append, source) == {alice: "baseline", bob: "baseline"}
    source.add(2)
    source.add(11, username="bob")
    outcomes = sync.check_user(user_id, lambda job_id: (queued.append(job_id), _finish(job_id)), source)

    # Each account's new games make their own analysis, under that account's player.
    assert outcomes == {alice: "queued", bob: "queued"}
    assert [jobs.job_inputs(j).player for j in queued] == ["Alice", "Bob"]
    assert [a.username for a in sync.accounts(user_id)] == ["alice", "bob"]


def test_the_same_account_twice_and_too_many_accounts_are_refused(user_id):
    sync.link_account(user_id, "lichess", "alice", "en")
    with pytest.raises(sync.LinkError) as duplicate:
        sync.link_account(user_id, "lichess", "ALICE", "en")
    assert duplicate.value.reason == "duplicate"
    sync.link_account(user_id, "chesscom", "alice", "en")  # another site is another account

    for n in range(sync.MAX_ACCOUNTS - 2):
        sync.link_account(user_id, "lichess", f"extra{n}", "en")
    with pytest.raises(sync.LinkError) as limit:
        sync.link_account(user_id, "lichess", "onemore", "en")
    assert limit.value.reason == "limit"


def test_unlinking_removes_only_your_own_account(user_id):
    account = sync.link_account(user_id, "lichess", "alice", "en")

    assert not sync.unlink_account(user_id + 1, account)
    assert sync.unlink_account(user_id, account)
    assert sync.accounts(user_id) == []
    assert sync.check(account, [].append, FakeSource(1)) == "unlinked"


def test_seen_games_are_capped_newest_first(user_id, monkeypatch):
    monkeypatch.setattr(sync, "MAX_SEEN_KEYS", 3)
    source = FakeSource(2, 1)
    account = sync.link_account(user_id, "lichess", "alice", "en")
    sync.check(account, [].append, source)
    source.add(4, 3)
    sync.check(account, _finish, source)

    with session_scope() as db:
        seen = json.loads(db.get(LinkedAccount, account).seen_keys_json)
    assert seen == [f"https://lichess.org/game{n}" for n in (4, 3, 2)]


def test_games_without_a_link_are_told_apart_by_their_content():
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
    checked = set()
    done = threading.Event()
    first = sync.link_account(user_id, "lichess", "alice", "en")
    second = sync.link_account(user_id, "chesscom", "alice", "en")

    def fake_check(account_id, enqueue, fetch=None):
        checked.add(account_id)
        if checked == {first, second}:
            done.set()

    monkeypatch.setattr(sync, "check", fake_check)
    scheduler = sync.SyncScheduler([].append, interval_seconds=60, first_delay=0)
    scheduler.start()
    try:
        assert done.wait(5)
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


def _link(client, username="alice", platform="lichess"):
    return client.post("/settings/sync", data={"platform": platform, "username": username, "lang": "en"})


def _account_ids():
    with session_scope() as db:
        return [row.id for row in db.query(LinkedAccount).order_by(LinkedAccount.id)]


def test_settings_offer_to_link_an_account(client):
    page = client.get("/settings").text

    assert 'action="/settings/sync"' in page
    assert "Analyse new games automatically" in page


def test_linking_checks_at_once_and_lists_the_account(client, monkeypatch):
    source = FakeSource(1)
    monkeypatch.setattr(game_sources, "fetch_pgn", source)

    page = _link(client, " alice ").text

    assert source.calls == [("lichess", "alice", sync.SYNC_FETCH_GAMES)]
    assert "Account linked" in page
    assert "<strong>Lichess user alice</strong>" in page
    [account] = _account_ids()
    assert f'action="/settings/sync/{account}/check"' in page
    assert "Link another account" in page

    source.add(2)
    page = client.post(f"/settings/sync/{account}/check").text
    assert "their analysis is queued" in page
    assert "1 new game(s) found" not in page  # the fresh outcome shows instead of the stored one
    assert "1 new game(s) found" in client.get("/settings").text


def test_check_all_reports_beside_each_account(client, monkeypatch):
    source = FakeSource(1, bob=[5])
    monkeypatch.setattr(game_sources, "fetch_pgn", source)
    _link(client, "alice")
    _link(client, "bob", "chesscom")

    page = client.post("/settings/sync/check").text

    assert page.count("No new games.") == 2
    assert "Check all now" in page


def test_linking_reports_an_unknown_account(client, monkeypatch):
    def unknown(*args):
        raise game_sources.GameFetchError("Lichess user 'nobody' not found.")

    monkeypatch.setattr(game_sources, "fetch_pgn", unknown)

    page = _link(client, "nobody").text

    assert "Couldn&#39;t fetch the games: Lichess user &#39;nobody&#39; not found." in page


def test_linking_rejects_bad_input_and_duplicates(client, monkeypatch):
    monkeypatch.setattr(game_sources, "fetch_pgn", FakeSource(1))

    assert client.post("/settings/sync", data={"platform": "fics", "username": "alice"}).status_code == 400
    assert client.post("/settings/sync", data={"platform": "lichess", "username": "alice", "lang": "de"}).status_code == 400
    assert "Invalid username" in client.post("/settings/sync", data={"platform": "lichess", "username": "a b"}).text
    _link(client, "alice")
    assert "That account is already linked." in _link(client, "Alice").text
    assert len(_account_ids()) == 1


def test_at_the_limit_the_form_is_replaced_by_a_note(client, monkeypatch):
    monkeypatch.setattr(game_sources, "fetch_pgn", FakeSource())
    for n in range(sync.MAX_ACCOUNTS):
        _link(client, f"player{n}")

    page = client.get("/settings").text

    assert 'action="/settings/sync"' not in page
    assert f"You have {sync.MAX_ACCOUNTS} accounts linked" in page
    assert "You can link at most" in _link(client, "sixth").text


def test_unlinking_removes_that_account_only(client, monkeypatch):
    monkeypatch.setattr(game_sources, "fetch_pgn", FakeSource(1, bob=[2]))
    _link(client, "alice")
    _link(client, "bob")
    first, second = _account_ids()

    page = client.post(f"/settings/sync/{first}/delete").text

    assert "Account unlinked." in page
    assert _account_ids() == [second]


def test_someone_elses_account_cant_be_checked_or_unlinked(client, monkeypatch, tmp_path):
    monkeypatch.setattr(game_sources, "fetch_pgn", FakeSource(1))
    _link(client, "alice")
    [account] = _account_ids()
    other = CsrfClient(app, follow_redirects=False)
    other.post("/register", data={"username": "mallory", "password": "correct-horse"})

    assert other.post(f"/settings/sync/{account}/check").status_code == 404
    assert other.post(f"/settings/sync/{account}/delete").status_code == 404
    assert _account_ids() == [account]


def test_deleting_a_user_deletes_their_accounts(client, monkeypatch):
    monkeypatch.setattr(game_sources, "fetch_pgn", FakeSource(1))
    _link(client, "alice")
    db = next(get_db())
    try:
        db.delete(db.query(User).filter_by(username="alice").one())
        db.commit()
        assert db.query(LinkedAccount).count() == 0
    finally:
        db.close()


# --- new analyses badge -------------------------------------------------------


class _Engine:
    def analyse(self, board, limit, **kwargs):
        import chess.engine

        return {"score": chess.engine.PovScore(chess.engine.Cp(20), chess.WHITE)}


def test_analyses_from_the_sync_are_new_until_history_is_visited(tmp_path, monkeypatch):
    from chesswhisper.web import app as web_app

    init_db(tmp_path / "badge.db")
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: _Engine())
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    source = FakeSource(1)
    monkeypatch.setattr(game_sources, "fetch_pgn", source)
    _link(c, "alice")

    # A manual analysis doesn't count; the sync's does, once it has run.
    web_app._run_analysis_job(jobs.create_job(1, jobs.JobInputs(
        pgn_text=_game(9), player="Alice", lang="en", llm="none", ui_language="en")).id)
    source.add(2)
    c.post("/settings/sync/check")  # conftest runs queued jobs at once

    page = c.get("/settings").text
    assert '<span class="nav-badge" title="1 new analyses from automatic sync">1</span>' in page

    history = c.get("/history").text
    assert history.count('class="new-tag"') == 1
    assert 'class="nav-badge"' not in c.get("/history").text


def test_opening_a_new_analysis_counts_as_seeing_it(client):
    with session_scope() as db:
        user = db.query(User).filter_by(username="alice").one()
        record = AnalysisRecord(
            user_id=user.id, player_name="Alice", lang="en", llm_used="none", games_count=1,
            overall_acpl=10.0, overall_accuracy=90.0, report_markdown="# Report", unseen=True,
        )
        db.add(record)
        db.flush()
        record_id = record.id

    assert 'class="nav-badge"' in client.get("/settings").text
    client.get(f"/history/{record_id}")
    assert 'class="nav-badge"' not in client.get("/settings").text
