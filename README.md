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

## API keys

Every account can mint up to 10 keys from the sidebar (`api` button). A key looks like
`nbla` + 32 random alphanumerics, e.g. `nblaK7fQ2mXp9RtLw4Zc8Hs6Vb3Ny1Dg5T`.

The plaintext is shown once at creation; only a SHA-256 hash is stored. Send it in any of
these ways:

```bash
curl -H "Authorization: Bearer nbla..." https://HOST/api/me
curl -H "X-API-Key: nbla..."           https://HOST/api/files
curl "https://HOST/api/files?key=nbla..."
```

Scopes: `full` (read + write) and `read` (read-only — every mutating endpoint returns 403).
Keys can only be created, renamed and revoked from a logged-in web session, never with a key.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/keys` | list keys (prefix, usage, last used) |
| `POST` | `/api/keys` | create — body `{"name":"bot","scope":"full"}`, returns the raw key once |
| `PATCH` | `/api/keys/{id}` | rename or change scope |
| `DELETE` | `/api/keys/{id}` | revoke immediately |

Everything else on the API works with a key exactly like with a session cookie:
`/api/me`, `/api/files`, `/api/upload/init`, `/api/upload/chunk`, `/api/upload/finish`,
`/api/file/{id}/download`, `/api/file/{id}` (PATCH/DELETE), `/api/file/{id}/share`,
`/api/folders`.

### Chunked upload from a script

```python
import json, os, requests

BASE = "https://HOST"
KEY = "nbla..."
H = {"X-API-Key": KEY}

def upload(path, folder=""):
    size = os.path.getsize(path)
    init = requests.post(f"{BASE}/api/upload/init", headers=H, json={
        "name": os.path.basename(path), "size": size, "folder": folder,
    }).json()
    uid, chunk = init["id"], init["chunk_size"]
    with open(path, "rb") as fh:
        off = 0
        while off < size:
            data = fh.read(chunk)
            requests.post(f"{BASE}/api/upload/chunk?id={uid}&offset={off}",
                          headers=H, data=data).raise_for_status()
            off += len(data)
    return requests.post(f"{BASE}/api/upload/finish", headers=H,
                         json={"id": uid}).json()
```

Small files can go through `POST /api/upload` as `multipart/form-data` with a `files` field
and an optional `folder` field — same header, no chunking.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEBULA_DB` | `data.db` | SQLite database path |
| `NEBULA_TMP` | `tmp/` | scratch directory for uploads |
| `NEBULA_UPLOAD_DELAY` | `60` | seconds between uploads per account |
| `NEBULA_QUOTA` | `1 TB` | per-account storage quota |
| `NEBULA_BACKUP_EVERY` | `600` | database snapshot interval |
| `NEBULA_INVITE` | unset | require an invite code to sign up |
