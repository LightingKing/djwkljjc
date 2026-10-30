# Server package boundary

The current implementation remains at the project root while the M3 server
refactor is staged. New server code belongs under this directory by feature:

- `core/` — configuration, storage, clock, ledger, codecs, backups, crash logs and activity scheduling
- `api/` — HTTP and WebSocket feature routes
- `proto/` — descriptor and protocol helpers
- `bots/` — local robot data and behaviour (the current compatibility layer is `social.py` at the project root)

Do not copy the runtime SQLite files into this tree. They are generated state
and are excluded by the repository `.gitignore`.
