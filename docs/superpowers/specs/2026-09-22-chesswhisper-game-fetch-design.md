# chesswhisper Game Fetching (Lichess / chess.com) — Design Spec

Date: 2026-09-22
Status: Draft (pending user review)

## 1. Overview

Today chesswhisper only analyzes games from a `.pgn` file the user has
already downloaded. This feature lets the user type a Lichess or chess.com
username instead, and chesswhisper fetches that player's most recent games
itself, then feeds them through the existing analysis pipeline unchanged.

The fetch layer has one job: `(platform, username, max_games)` -> PGN text.
Everything downstream (`run_analysis`, `stats_engine`, `report_writer`, the
history table) is untouched; fetched games reach it the same way uploaded
ones do, as a temporary `.pgn` file.

## 2. Goals

- A new module, `chesswhisper/game_sources.py`, exposing
  `fetch_pgn(platform, username, max_games=10)` for `"lichess"` and
  `"chesscom"`, using the already-declared `requests` dependency (no new
  dependencies).
- CLI: `chesswhisper analyze` accepts `--lichess USER` or `--chesscom USER`
  (plus `--max N`) as an alternative to the positional `PGN_PATH`.
- Web: the analyze form gains a source selector (PGN file / Lichess /
  chess.com) with a username field and a game-count field.
- The username doubles as the tracked player: matched case-insensitively
  against the fetched games' headers and passed on with its true spelling,
  so `caglar` finds games where the header says `Caglar`.
- Clean, non-traceback errors for: invalid username, unknown user, upstream
  rate limiting, network failure/timeouts, and "no eligible games found".

## 3. Non-goals (v1)

- No filters beyond "most recent N games" (no time-control, rated-only, or
  date-range filters).
- No authentication with either platform, so private or ongoing games are
  out of reach; public games only.
- No caching or de-duplication against earlier analyses of the same games.
- No platforms other than Lichess and chess.com.
- No background jobs or progress reporting. Analyzing N games is still one
  synchronous request in the web UI; that is a separate, follow-up feature.
- No chess variants: Chess960, Crazyhouse, etc. are skipped (see §5.1).

## 4. Architecture

```
username + platform + N
        |
        v
game_sources.fetch_pgn  --->  FetchResult(pgn_text, player, warnings)
        |
        v
temp .pgn file  --->  run_analysis (unchanged)  --->  report / history
```

`game_sources.py` imports nothing from `cli.py` or `web/`; both of those
import it. It is pure I/O plus light PGN header inspection (via
`python-chess`, already a dependency).

## 5. Components

### 5.1 `game_sources.py`

```python
class GameFetchError(Exception): ...            # bad input or "nothing found"
class GameSourceUnavailable(GameFetchError): ...  # upstream/network problem

@dataclass
class FetchResult:
    pgn_text: str
    player: str            # username as spelled in the games' headers
    warnings: list[str]

def fetch_pgn(platform: str, username: str, max_games: int = 10) -> FetchResult
```

**Input validation (before any network call).** `username` must match
`^[A-Za-z0-9_-]{2,30}$` and is URL-quoted when placed in the path.
`max_games` must be an integer in 1..50 (default 10). `platform` must be
`"lichess"` or `"chesscom"`. Violations raise `GameFetchError`. The
username regex is what keeps user-supplied web input from steering the
request anywhere other than the fixed API hosts.

**Requests.** Every request sends
`User-Agent: chesswhisper (github.com/Caglar-Coban/chesswhisper)` and uses
a 30 s timeout. Requests are strictly serial (chess.com's docs only
guarantee no rate limiting for serial access).

**Lichess.** One request:
`GET https://lichess.org/api/games/user/{username}?max={N}&perfType=ultraBullet,bullet,blitz,rapid,classical,correspondence`
with `Accept: application/x-chess-pgn`. Results come newest-first, no
token needed. The `perfType` filter restricts results to standard-chess
time controls, which excludes variants at the source. After parsing, any
game whose `Variant` header exists and is not `Standard` is dropped with a
warning, as a safety net.

**chess.com.**
1. `GET https://api.chess.com/pub/player/{username}/games/archives` returns
   monthly URLs oldest-first; walk them newest-first.
2. For each month, `GET` the archive URL (the JSON form, not `/pgn`). Each
   game object carries `pgn` and `rules`; keep only `rules == "chess"`,
   which is how variants are excluded. Games within a month are ordered by
   end time, so take from the end.
3. A 404 on an individual monthly URL is skipped with a warning rather
   than aborting the fetch. (Observed live: the archive list for a real
   account included the current month, whose URL returned a 404 with an
   "internal error" body while the previous month returned normally.) Only
   429, timeouts, connection errors, and other non-2xx statuses on a monthly
   URL raise `GameSourceUnavailable`.
4. Stop once N games are collected, or after 6 monthly archives, so an
   inactive account cannot cause an unbounded walk.

**Player normalization.** After collecting games, find the first
`White`/`Black` header that equals `username` ignoring case; that exact
spelling becomes `FetchResult.player`. If no game names the user (which
would indicate an API oddity), raise `GameFetchError`.

**Errors.** HTTP 404 on the Lichess request or on chess.com's archive
*list* -> `GameFetchError("user not found")` (a 404 on a single chess.com
*monthly* URL is handled as described above). HTTP 429, timeouts,
connection errors, other non-2xx responses -> `GameSourceUnavailable` with
a specific, user-facing message. Zero eligible games after filtering ->
`GameFetchError`. There is no automatic retry; the user can simply try
again.

**Output.** Games are joined into one PGN string, blank-line separated.
Game order does not matter to the pipeline. If fewer than N games were
found, or games were dropped or months skipped, `warnings` records it.

### 5.2 CLI (`cli.py`)

`analyze` changes from a required positional `PGN_PATH` to an optional one,
plus three new options:

```
chesswhisper analyze [PGN_PATH] [--lichess USER | --chesscom USER] [--max N] ...
```

- Exactly one of `PGN_PATH`, `--lichess`, `--chesscom` must be provided;
  anything else is a usage error with a clear message.
- `--max` is only valid with `--lichess`/`--chesscom` (10 when omitted
  there; the option itself defaults to `None` so that supplying it with a
  PGN file can be detected and rejected as a usage error).
- With a fetch source, the CLI calls `fetch_pgn`, writes `pgn_text` to a
  temporary file, and calls `run_analysis` on it. `--player`, if given,
  wins; otherwise `FetchResult.player` is used. The temp file is removed in
  a `finally`. `FetchResult.warnings` are printed like other warnings.
- The existing "refuse to overwrite the input PGN" check applies only when
  a `PGN_PATH` was given.
- `GameFetchError` (and its subclass) are reported as `Error: ...` with
  exit code 1, like the existing `ValueError`/`OSError` handling.

### 5.3 Web (`web/app.py`, `templates/index.html`)

The form gains a `source` selector (`file` default, `lichess`, `chesscom`),
a `username` field, and a `max_games` field (default 10, min 1, max 50).
A few lines of inline JS show the file input for `file` and the
username/count fields otherwise; the file input's `required` attribute is
toggled accordingly. Labels and hints are in Turkish like the rest of the
page, and the hint mentions that more games take longer.

`POST /analyze`:
- `source == "file"`: unchanged behavior, including the existing "please
  select a PGN file" error.
- Otherwise: the `username` is required; `fetch_pgn` is run via
  `run_in_threadpool` so a slow upstream doesn't stall the event loop. The
  resulting text takes the place of the uploaded file's bytes in the
  existing temp-file/`run_analysis`/history code path, with `player` from
  the form if provided, else `FetchResult.player`.
- `GameFetchError` -> 400 error page with the message;
  `GameSourceUnavailable` -> 502 error page. `FetchResult.warnings` are
  merged into the warnings shown on the result page.
- CSRF, login, per-user API key handling, and history saving are unchanged.

## 6. Dependencies

None added. `requests` and `python-chess` are already core dependencies.

## 7. Error Handling

| Situation | CLI | Web |
|---|---|---|
| Invalid username / platform / `--max` | usage error, exit 1 | 400 |
| User not found (404) | `Error: ...`, exit 1 | 400 |
| Rate limited (429), timeout, network error | `Error: ...`, exit 1 | 502 |
| No eligible games after filtering | `Error: ...`, exit 1 | 400 |
| Fewer than N games / variants dropped / a chess.com month skipped | `Warning: ...` | warning on result page |

Error messages never include raw response bodies or stack traces.

## 8. Testing Strategy

No live network in tests or CI: `requests.get` is monkeypatched.

- `tests/test_game_sources.py`:
  - Lichess: happy path; 404; 429; timeout/connection error; variant
    header dropped with a warning; request URL/headers contain the expected
    `max`, `perfType`, `Accept`, `User-Agent`.
  - chess.com: newest-month-first walk; N taken across a month boundary;
    `rules != "chess"` filtered; 6-month cap; 404 on the archive list ->
    user not found; 404 on one monthly URL -> skipped with a warning while
    other months still yield games; 429 on a monthly URL ->
    `GameSourceUnavailable`; empty archive list.
  - Validation: bad usernames (empty, too short/long, path characters
    like `../`, `a/b`, spaces), bad `max_games` (0, 51, non-int), bad
    platform. Assert no request is made in these cases.
  - Player normalization: `caglar` -> `Caglar`; no matching header ->
    `GameFetchError`.
- CLI tests (in `tests/test_cli.py`, following its existing style): source
  exclusivity and `--max` misuse; a fetch source passes the normalized
  player and the fetched text (via a temp file that is cleaned up) to
  `run_analysis`; `--player` overrides; `GameFetchError` -> exit 1 with a
  clean message.
- Web tests (new file alongside the other `test_web_*` files):
  `source=lichess`/`chesscom` with `fetch_pgn` monkeypatched; missing
  username -> 400; `GameFetchError` -> 400; `GameSourceUnavailable` ->
  502; `source=file` behavior unchanged (existing tests keep passing).

The existing `ruff`, `mypy`, and `pytest` CI checks must stay green.

Manual verification (not automated), done against the live APIs while
writing this spec:
- Lichess: PGN output with the `Accept` header, the `perfType` list above
  (HTTP 200, requested game count returned), `Variant "Standard"` header,
  and 404 for an unknown user.
- chess.com: archive list (oldest-first), a monthly JSON response with
  `pgn`/`rules`/`end_time` fields, `end_time` ascending within the month,
  404 for an unknown user, and the current-month 404 quirk described in
  §5.1.

Not verified live: 429 behavior on either platform (taken from the
platforms' documentation) and the chess.com `rules` values for actual
variant games (only `"chess"` was observed; `"chess960"` is per the docs).

## 9. Open Items for Implementation Planning

- Confirm how `tests/test_engine_provision.py` mocks `requests` and reuse
  that idiom in the new tests.
- Decide the exact wording of Turkish UI strings and error messages.
- README: document `--lichess`/`--chesscom`/`--max` and the new web form
  option, and note that analysis time scales with the number of games.
