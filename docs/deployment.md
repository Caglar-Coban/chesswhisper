# Deploying the chesswhisper web UI

This guide covers running the web UI as a long-lived service for yourself
or a few people you trust: on a home server, a spare machine on your LAN,
or a small VPS. It does not turn chesswhisper into a public web service.
Sign-up is open unless you close it, there's no email verification, and
background jobs live in memory, so read [Reaching it from other
machines](#reaching-it-from-other-machines) before exposing it anywhere.

The quickest route is [Docker](#docker); the rest of the guide sets it up
directly on a machine.

## What you need

- Python 3.11, 3.12 or 3.13 (or just Docker).
- A few hundred MB of disk for the virtualenv and Stockfish (about 100 MB on
  its own), plus whatever the database grows to.
- Outbound internet access if you want Stockfish downloaded automatically,
  games fetched from Lichess/chess.com, or LLM commentary. Each user's
  Anthropic/OpenAI key is entered in the UI, so the server needs no keys of
  its own.

## Install

Create a dedicated user and install into a virtualenv (Linux example):

```
sudo useradd --system --create-home --home-dir /var/lib/chesswhisper chesswhisper
sudo -u chesswhisper -i
git clone https://github.com/Caglar-Coban/chesswhisper.git ~/app
python3 -m venv ~/venv
~/venv/bin/pip install "$HOME/app[web]"
```

Stockfish is downloaded into the data directory the first time an analysis
runs. To use a system package instead (for example `apt install stockfish`,
which installs `/usr/games/stockfish` on Debian/Ubuntu), point
`CHESSWHISPER_STOCKFISH_PATH` at the binary; one on `PATH` is also picked up.

## Where the data lives

Everything the web UI keeps is in one per-user data directory:

| OS | Path |
|----|------|
| Linux | `~/.local/share/chesswhisper` (or `$XDG_DATA_HOME/chesswhisper`) |
| macOS | `~/Library/Application Support/chesswhisper` |
| Windows | `%LOCALAPPDATA%\chesswhisper\chesswhisper` |

It holds:

- `chesswhisper.db`: the SQLite database (users, encrypted API keys, history).
- `session_secret.key`: signs login cookies. Deleting it logs everyone out.
- `api_key_encryption.key`: encrypts stored API keys. **Losing it makes
  every saved API key unreadable**; users would have to enter them again.
- `sf_*/`: the downloaded Stockfish binary (safe to delete; it is re-downloaded).

Set `CHESSWHISPER_DATA_DIR` to keep all of this somewhere else, for example
`/var/lib/chesswhisper/data` or a mounted volume.

Without that variable the directory belongs to the OS user the server runs
as. Run every command (`create-admin`, `serve`) as that same user, with the
same `CHESSWHISPER_DATA_DIR` if you set one, or they will each see their own
empty database. With the setup above that means `sudo -u chesswhisper ...`.

## First start

```
sudo -u chesswhisper /var/lib/chesswhisper/venv/bin/chesswhisper create-admin
sudo -u chesswhisper /var/lib/chesswhisper/venv/bin/chesswhisper serve --host 127.0.0.1 --port 8000
```

Database migrations run automatically at startup; there is no separate
migrate step. Open `http://127.0.0.1:8000`, log in as the admin, then stop
the server with Ctrl+C and set it up as a service.

## Running as a service

### Linux (systemd)

`/etc/systemd/system/chesswhisper.service`:

```ini
[Unit]
Description=chesswhisper web UI
After=network-online.target
Wants=network-online.target

[Service]
User=chesswhisper
Group=chesswhisper
WorkingDirectory=/var/lib/chesswhisper
ExecStart=/var/lib/chesswhisper/venv/bin/chesswhisper serve --host 127.0.0.1 --port 8000
# Optional: pin the data directory instead of relying on the user's home.
# Environment=CHESSWHISPER_DATA_DIR=/var/lib/chesswhisper/data
# Uncomment to use a system Stockfish instead of the auto-downloaded one:
# Environment=CHESSWHISPER_STOCKFISH_PATH=/usr/games/stockfish
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```
sudo systemctl daemon-reload
sudo systemctl enable --now chesswhisper
journalctl -u chesswhisper -f     # logs
```

### Windows

Run it under your own account with Task Scheduler: create a task triggered
**At log on**, action *Start a program*, program
`C:\path\to\venv\Scripts\chesswhisper.exe`, arguments
`serve --host 127.0.0.1 --port 8000`. Create the admin from the same account
so both use the same data directory.

### Docker

The repository's `Dockerfile` builds an image with Stockfish included, runs
as an unprivileged user and keeps everything in a `/data` volume;
`docker-compose.yml` publishes it on `127.0.0.1:8000`:

```
docker compose up -d
docker compose exec chesswhisper chesswhisper create-admin
docker compose logs -f                      # logs
```

To upgrade, back up the volume (see [Backups](#backups)), then
`git pull && docker compose up -d --build`; migrations run when the new
container starts. Set `CHESSWHISPER_ALLOW_REGISTRATION: "0"` in
`docker-compose.yml` once your accounts exist.

### A public demo on Hugging Face Spaces

`deploy/huggingface/` holds a Space version of the image: it listens on
7860, runs with `CHESSWHISPER_DEMO=1` (a "public demo" banner, a one-click
demo login, no LLM commentary or stored API keys, at most 10 games per
analysis) and seeds a `demo` account at build time with `chesswhisper
seed-demo`. Free Spaces have no persistent disk, so each restart returns to
that seeded state.

The **Deploy demo** workflow publishes it on every push to `master`:

1. Create a Hugging Face account and a token with write access
   (Settings → Access Tokens).
2. In the GitHub repository, add the token as the secret `HF_TOKEN` and the
   Space name as the variable `HF_SPACE`, for example
   `your-hf-name/chesswhisper`:

   ```
   gh secret set HF_TOKEN
   gh variable set HF_SPACE --body "your-hf-name/chesswhisper"
   ```

3. Run the workflow once (Actions → Deploy demo → Run workflow). It creates
   the Space if needed and uploads the app; the first build takes about ten
   minutes because it analyzes the demo games.

### Keep it to one process

`chesswhisper serve` starts a single uvicorn process, and it has to stay
that way. Analysis progress lives in that process's memory, so do not run
it under gunicorn, with `--workers` or with `WEB_CONCURRENCY` set, and do not start two copies against
the same data directory. A restart drops any analysis still running;
already-finished ones are in the database and survive.

## Reaching it from other machines

By default the server only listens on `127.0.0.1`. Pick the least exposed
option that works for you:

1. **SSH tunnel.** Nothing changes on the server:
   `ssh -L 8000:127.0.0.1:8000 you@server`, then browse
   `http://127.0.0.1:8000` locally.
2. **A private network (Tailscale, WireGuard, home LAN).** Bind to that
   interface's address, for example `--host 100.64.0.5`, rather than
   `0.0.0.0`. Traffic is plain HTTP, so only do this on a network you trust.
3. **Reverse proxy with HTTPS.** Only if the people using it can't join a
   private network. Unless you set `CHESSWHISPER_ALLOW_REGISTRATION=0`,
   anyone who reaches the login page can register an account and spend CPU
   on analyses, so close sign-up or put a second gate in front of the app. With [Caddy](https://caddyserver.com/), which handles the
   certificate itself:

   ```
   chess.example.com {
       basic_auth {
           # generate the hash with: caddy hash-password
           friend $2a$14$...
       }
       reverse_proxy 127.0.0.1:8000
   }
   ```

   Keep chesswhisper itself on `127.0.0.1` so the proxy is the only way in,
   and allow only ports 80/443 through the firewall.

   The login rate limit counts attempts per client address. Uvicorn takes
   the real address from the proxy's `X-Forwarded-For` header only when the
   proxy connects from `127.0.0.1`; if it connects from elsewhere (for
   example into a Docker container), set `FORWARDED_ALLOW_IPS` to the
   proxy's address, or every visitor shares one limit.

## Backups

Back up the data directory. The database can be copied safely while the
server runs with SQLite's online backup:

```
sqlite3 ~/.local/share/chesswhisper/chesswhisper.db ".backup /backups/chesswhisper-$(date +%F).db"
```

Copy `api_key_encryption.key` and `session_secret.key` alongside it once;
they don't change. Keep the backups private: whoever has the key file and
the database can decrypt users' API keys.

## Upgrading

```
# 1. back up the database (see above)
sudo -u chesswhisper git -C /var/lib/chesswhisper/app pull
sudo -u chesswhisper /var/lib/chesswhisper/venv/bin/pip install "/var/lib/chesswhisper/app[web]"
sudo systemctl restart chesswhisper
```

New migrations apply on restart. If the service fails to start after an
upgrade, `journalctl -u chesswhisper` shows the migration traceback. To
roll back, restore the previous code and the database backup taken in
step 1, since a migrated database isn't guaranteed to work with older code.

## Troubleshooting

- **"the web UI requires extra dependencies"**: the `web` extra isn't
  installed in the virtualenv the service uses. Reinstall with `"[web]"`.
- **Admin login fails right after `create-admin`**: the command ran as a
  different OS user (or with a different `CHESSWHISPER_DATA_DIR`) than the
  service, so it wrote to another data directory. Re-run it with
  `sudo -u chesswhisper` and the same environment.
- **Analyses fail with a Stockfish download error**: the server has no
  outbound access to GitHub. Install Stockfish from your package manager
  and set `CHESSWHISPER_STOCKFISH_PATH`.
- **Everyone got logged out**: `session_secret.key` was deleted or the data
  directory changed. Restore the file from backup.
- **"Hesap kilitlendi" (account locked)**: five failed logins lock an account for 15 minutes.
  Wait it out, or have the admin reset the password.
