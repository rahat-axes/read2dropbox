# Changelog

All notable changes to readbox are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Open-source polish: expanded README with a full `readbox auth` walkthrough
  (Dropbox app creation, scopes, and the Read AI + Dropbox authorization flows),
  usage and configuration reference.
- `CHANGELOG.md`.
- GitHub Actions CI running the test suite on every push and pull request.

## [0.1.0] - 2026-09-22

Initial development release.

### Added
- Read AI API client (`read_client.py`): list and fetch meetings with cursor
  pagination and retry/backoff.
- Meeting formatter (`formatter.py`): render a meeting to Markdown and JSON, with
  customizable filename/folder/date templates.
- OAuth for both providers (`auth.py`): Read AI client registration + code
  exchange with single-use refresh-token rotation, and Dropbox PKCE flow;
  owner-only local token storage.
- Dropbox client (`dropbox_client.py`): idempotent uploads and folder creation.
- Sync engine (`sync.py`): full and incremental export, timestamp-based change
  detection, per-meeting failure isolation, and local state tracking.
- Command-line interface (`cli.py`): `auth`, `sync` (`--full`, `--days`),
  `list`, `status`, and `config`.
- User settings (`config.py`): configurable Dropbox root folder and
  filename/date/folder templates, with zero-config defaults.
- Offline pytest suite built on fixtures.

[Unreleased]: https://github.com/rahat-axes/read2dropbox/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rahat-axes/read2dropbox/releases/tag/v0.1.0
