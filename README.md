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
incremental archival to Dropbox.**

## Status

Early development. This tool depends on Read AI's API, which is currently an open
beta and may change before general availability.

## License

MIT — see [LICENSE](LICENSE).
