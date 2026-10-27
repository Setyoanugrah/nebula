# Nebula Cloud Storage

Private cloud drive for your own circle. One account, one storage area, zero clutter.

## Features

- Email-username accounts with hashed passwords and signed session cookies
- Upload with drag & drop, live progress and automatic resume-safe retries
- Preview for images, video, audio, PDF and plain text
- Share links with optional expiry, revocable at any time
- Instant search, rename, folder organisation and permanent delete
- Per-account upload cooldown and 1 TB quota
- Automatic database snapshots with restore on boot

## Anti-abuse

- Proof-of-work challenge on signup and login (no third-party captcha)
- Honeypot field and per-IP / per-account rate limits
- Optional invite code via `NEBULA_INVITE`

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install starlette uvicorn python-multipart
cp config.example.json config.json   # fill in your storage credentials
./run.sh start
```

## Deploy

The included workflow `.github/workflows/serve.yml` runs the app on a GitHub
Actions runner behind an edge tunnel. Set these repository secrets:

| Secret | Purpose |
| --- | --- |
| `NEBULA_STORE_ID` | storage backend identifier |
| `NEBULA_STORE_KEY` | storage backend key |
| `NEBULA_STORE_SESSION` | storage backend session string |
| `NEBULA_EDGE_TOKEN` | edge tunnel token |

Then run the `nebula` workflow. The app is live as soon as `/api/health` responds.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEBULA_DB` | `data.db` | SQLite database path |
| `NEBULA_TMP` | `tmp/` | scratch directory for uploads |
| `NEBULA_UPLOAD_DELAY` | `60` | seconds between uploads per account |
| `NEBULA_QUOTA` | `1 TB` | per-account storage quota |
| `NEBULA_BACKUP_EVERY` | `600` | database snapshot interval |
| `NEBULA_INVITE` | unset | require an invite code to sign up |
