# readbox

Export your [Read AI](https://www.read.ai) meeting reports and transcripts into
your Dropbox as Markdown + JSON files, on demand.

readbox is a free, open-source command-line tool. It runs entirely on your own
machine and authorizes **your own** Read AI and Dropbox accounts — no server, no
shared secret, no data held by anyone but you.

```
Your machine
   Read AI  ──API──▶  readbox  ──API──▶  Dropbox
                     (local process)
```

## What you get

```bash
pip install readbox
readbox auth      # authorize your Read AI and Dropbox accounts, once
readbox sync      # export new/updated meetings to Dropbox
```

Files land in a dated folder tree in your Dropbox:

```
Read AI/
└── 2026/
    └── 08/
        ├── 2026-08-21 Engineering Sync.md
        └── 2026-08-21 Engineering Sync.json
```

Each `.md` contains Summary, Participants, Action Items, Decisions, Topics, and
Transcript. Each `.json` is the raw API response, so nothing is lost.

## Why it exists

Read AI already ships an official MCP server and Claude/ChatGPT connectors for
*live querying*. readbox does the thing those don't: **durable, foldered,
incremental archival to Dropbox** — a copy of your meetings that lives in your
own storage and keeps working after a connector session ends.

## Install

```bash
pip install readbox
```

Requires Python 3.11+. This installs the `readbox` command.

## Setup (`readbox auth`)

Authorization is a one-time step. Run it once and readbox stores refresh tokens
locally so future syncs are non-interactive.

### 1. Create a Dropbox app

readbox uploads to Dropbox through *your own* Dropbox app, so you control the
scopes and can revoke access at any time.

1. Go to the [Dropbox App Console](https://www.dropbox.com/developers/apps) and
   click **Create app**.
2. Choose **Scoped access**, then **App folder** or **Full Dropbox** (App folder
   keeps readbox confined to a single folder — recommended).
3. On the app's **Permissions** tab, enable these scopes and click **Submit**:
   - `files.content.write` — upload the exported files
   - `files.metadata.read` — check what already exists
   - `account_info.read` — confirm the authorized account
4. Copy the app's **App key** from the **Settings** tab.

readbox deliberately does **not** request `files.content.read`: it only writes
your meetings out, it never reads your Dropbox back.

Provide the app key to readbox either with `--app-key` or via an environment
variable:

```bash
export READBOX_DROPBOX_APP_KEY=your_app_key
```

### 2. Run auth

```bash
readbox auth
```

This walks through two flows:

- **Read AI** — readbox registers an OAuth client, prints an authorization URL
  and a `client_id`. Open the URL, sign in, click **Allow Access**, and paste
  the authorization code back into the prompt.
- **Dropbox** — readbox runs Dropbox's PKCE console flow: open the printed URL,
  approve, and paste the resulting code. (Uses the App key from step 1.)

Tokens are written to a local credentials file (owner-readable only). You can
re-run `readbox auth --reset` to start over, or authorize one side at a time
with `--read` / `--dropbox`.

## Usage

```bash
readbox sync              # export new or changed meetings (incremental)
readbox sync --days 30    # only consider meetings from the last 30 days
readbox sync --full       # re-upload everything, ignoring saved state
readbox list              # list your meetings (read-only, nothing uploaded)
readbox list --days 7
readbox status            # show what's authorized and what was last synced
```

Sync is **incremental**: readbox tracks each meeting's last-updated timestamp in
a local state file and skips meetings that haven't changed — without even
fetching the full report — so re-running `readbox sync` is cheap. Use `--full`
to force a complete re-export.

## Configuration

readbox works with zero configuration, but you can customize the output layout:

```bash
readbox config                                   # show current settings
readbox config root_folder "Meetings"            # change the Dropbox root folder
readbox config filename_template "{date} {title}"
readbox config folder_template "{year}/{month}"
readbox config date_format "%Y-%m-%d"
```

| Setting             | Default           | Meaning                                            |
| ------------------- | ----------------- | -------------------------------------------------- |
| `root_folder`       | `Read AI`         | Top-level Dropbox folder for all exports           |
| `folder_template`   | `{year}/{month}`  | Sub-folder layout per meeting                      |
| `filename_template` | `{date} {title}`  | File name (extension added automatically)          |
| `date_format`       | `%Y-%m-%d`        | How `{date}` is rendered (Python `strftime`)       |

Available template tokens: `{year}`, `{month}`, `{day}`, `{date}`, `{title}`.
Settings are validated before they are saved, and stored in your platform config
directory (see `readbox config` output for the exact path).

## Development

```bash
git clone https://github.com/rahat-axes/read2dropbox
cd readbox
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

The test suite is fully offline — it runs against fixtures, so no Read AI or
Dropbox credentials are needed to develop or contribute.

## Status

Early development (v0.1.0). This tool depends on Read AI's API, which is
currently an open beta and may change before general availability. See
[CHANGELOG.md](CHANGELOG.md) for release notes.

## License

MIT — see [LICENSE](LICENSE).
