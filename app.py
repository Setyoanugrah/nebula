import asyncio
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from store import (
    Client,
    InputFileBig,
    SaveBigFilePartRequest,
    CreateForumTopicRequest,
    DocumentAttributeFilename,
    MessageActionTopicCreate,
    StringSession,
    functions,
)

BASE = Path(__file__).resolve().parent
CFG = json.loads((BASE / "config.json").read_text())
DB_PATH = os.environ.get("NEBULA_DB", str(BASE / "data.db"))
TMP = Path(os.environ.get("NEBULA_TMP", str(BASE / "tmp")))
TMP.mkdir(parents=True, exist_ok=True)
DELAY = int(os.environ.get("NEBULA_UPLOAD_DELAY", CFG.get("upload_delay", 60)))
QUOTA = int(os.environ.get("NEBULA_QUOTA", CFG.get("quota", 2 * 1024**3)))
MAX_FILE = 2 * 1024**3 - 1024
POW_BITS = int(os.environ.get("NEBULA_POW_BITS", "20"))
INVITE = os.environ.get("NEBULA_INVITE", "")
CHALLENGES = {}
HITS = defaultdict(deque)
COOKIE = "nd"
BACKUP_EVERY = int(os.environ.get("NEBULA_BACKUP_EVERY", "300"))
BACKUP_NAME = "nebula.db"

SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        salt TEXT NOT NULL,
        hash TEXT NOT NULL,
        topic_id INTEGER,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions(
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        expires INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS files(
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        size INTEGER NOT NULL,
        mime TEXT NOT NULL,
        msg_id INTEGER NOT NULL,
        folder TEXT NOT NULL DEFAULT '',
        share_token TEXT UNIQUE,
        created_at INTEGER NOT NULL,
        hash TEXT NOT NULL DEFAULT '',
        trashed_at INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS folders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        UNIQUE(user_id, name)
    );
    CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS apikeys(
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        prefix TEXT NOT NULL,
        hash TEXT UNIQUE NOT NULL,
        scope TEXT NOT NULL DEFAULT 'full',
        created_at INTEGER NOT NULL,
        last_used INTEGER NOT NULL DEFAULT 0,
        last_ip TEXT NOT NULL DEFAULT '',
        uses INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_id, folder);
    CREATE INDEX IF NOT EXISTS idx_keys_user ON apikeys(user_id);
    """

db = None


def open_db():
    global db
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)


def reopen_db():
    global db
    try:
        db.close()
    except Exception:  # noqa: BLE001
        pass
    open_db()


open_db()


def kv_get(k, default=None):
    row = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return row["v"] if row else default


def kv_set(k, v):
    db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
    db.commit()


def hash_pw(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    return salt, h.hex()


def check_pw(password, salt, expected):
    _, h = hash_pw(password, salt)
    return hmac.compare_digest(h, expected)


def user_used(uid):
    """Pemakaian kuota dihitung per-pesan storage, bukan per-file.

    File hasil dedup berbagi satu pesan storage, jadi tidak boleh ditagih dua kali.
    File yang masih pending (msg_id=0) tetap dihitung sendiri-sendiri.
    """
    shared = db.execute(
        "SELECT COALESCE(SUM(size),0) AS s FROM ("
        "  SELECT MAX(size) AS size FROM files"
        "  WHERE user_id=? AND trashed_at=0 AND msg_id>0 GROUP BY msg_id"
        ")",
        (uid,),
    ).fetchone()["s"]
    pending = db.execute(
        "SELECT COALESCE(SUM(size),0) AS s FROM files"
        " WHERE user_id=? AND trashed_at=0 AND (msg_id IS NULL OR msg_id=0)",
        (uid,),
    ).fetchone()["s"]
    return shared + pending


def user_trashed(uid):
    row = db.execute(
        "SELECT COALESCE(SUM(size),0) AS s, COUNT(*) AS c FROM files WHERE user_id=? AND trashed_at>0",
        (uid,),
    ).fetchone()
    return row["s"], row["c"]


def file_hash(path, chunk=1024 * 1024):
    """SHA-256 isi file, dipakai untuk dedup."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def clean_folder(name):
    name = (name or "").replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    return name[:180]


def clean_name(name):
    name = (name or "file").replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    return (name or "file")[:180]


def cookies(request):
    raw = request.headers.get("cookie", "")
    out = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def current_user(request):
    key_user, _key = key_auth(request)
    if key_user:
        return key_user
    token = cookies(request).get(COOKIE)
    if not token:
        return None
    row = db.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?",
        (token, int(time.time())),
    ).fetchone()
    return dict(row) if row else None


# ---------- api keys ----------

KEY_PREFIX = "nbla"
KEY_BODY_LEN = 32
KEY_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
KEY_SCOPES = ("full", "read")
MAX_KEYS = 10


def key_hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def new_key():
    return KEY_PREFIX + "".join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_BODY_LEN))


def key_raw(request):
    auth = (request.headers.get("authorization") or "").strip()
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    for h in ("x-api-key", "api-key", "x-nebula-key"):
        v = (request.headers.get(h) or "").strip()
        if v:
            return v
    return (request.query_params.get("key") or "").strip()


def key_auth(request):
    cached = request.scope.get("nebula_key")
    if cached is not None:
        return cached if cached != False else (None, None)
    raw = key_raw(request)
    if not raw or not raw.startswith(KEY_PREFIX) or len(raw) != len(KEY_PREFIX) + KEY_BODY_LEN:
        request.scope["nebula_key"] = False
        return None, None
    row = db.execute("SELECT * FROM apikeys WHERE hash=?", (key_hash(raw),)).fetchone()
    if not row:
        request.scope["nebula_key"] = False
        return None, None
    user = db.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
    if not user:
        request.scope["nebula_key"] = False
        return None, None
    now = int(time.time())
    if now - int(row["last_used"] or 0) > 30:
        try:
            db.execute(
                "UPDATE apikeys SET last_used=?, uses=uses+1, last_ip=? WHERE id=?",
                (now, client_ip(request), row["id"]),
            )
            db.commit()
        except Exception:  # noqa: BLE001
            pass
    out = (dict(user), row)
    request.scope["nebula_key"] = out
    return out


def key_public(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "scope": row["scope"],
        "created_at": row["created_at"],
        "last_used": row["last_used"],
        "uses": row["uses"],
        "last_ip": row["last_ip"],
    }


async def keys_endpoint(request):
    if key_auth(request)[1]:
        return json_err("kelola api key hanya dari sesi web, bukan dari api key", 403)
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if request.method == "GET":
        rows = db.execute(
            "SELECT * FROM apikeys WHERE user_id=? ORDER BY created_at DESC", (user["id"],)
        ).fetchall()
        return JSONResponse({"keys": [key_public(r) for r in rows], "max": MAX_KEYS})
    body = await request.json()
    name = clean_name(body.get("name") or "").strip() or "api key"
    scope = (body.get("scope") or "full").strip().lower()
    if scope not in KEY_SCOPES:
        return json_err("scope harus full atau read")
    total = db.execute("SELECT COUNT(*) c FROM apikeys WHERE user_id=?", (user["id"],)).fetchone()["c"]
    if total >= MAX_KEYS:
        return json_err(f"maksimal {MAX_KEYS} api key, cabut dulu yang lama", 409)
    raw = new_key()
    kid = secrets.token_hex(8)
    db.execute(
        "INSERT INTO apikeys(id,user_id,name,prefix,hash,scope,created_at) VALUES(?,?,?,?,?,?,?)",
        (kid, user["id"], name[:60], raw[:8], key_hash(raw), scope, int(time.time())),
    )
    db.commit()
    row = db.execute("SELECT * FROM apikeys WHERE id=?", (kid,)).fetchone()
    return JSONResponse({"key": raw, "info": key_public(row)})


async def key_detail(request):
    if key_auth(request)[1]:
        return json_err("kelola api key hanya dari sesi web, bukan dari api key", 403)
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    kid = request.path_params["kid"]
    row = db.execute("SELECT * FROM apikeys WHERE id=? AND user_id=?", (kid, user["id"])).fetchone()
    if not row:
        return json_err("not found", 404)
    if request.method == "DELETE":
        db.execute("DELETE FROM apikeys WHERE id=?", (kid,))
        db.commit()
        return JSONResponse({"ok": True})
    body = await request.json()
    name = row["name"]
    scope = row["scope"]
    if body.get("name") is not None:
        name = (clean_name(body.get("name")) or row["name"])[:60]
    if body.get("scope") is not None:
        s = str(body.get("scope")).strip().lower()
        if s not in KEY_SCOPES:
            return json_err("scope harus full atau read")
        scope = s
    db.execute("UPDATE apikeys SET name=?, scope=? WHERE id=?", (name, scope, kid))
    db.commit()
    row = db.execute("SELECT * FROM apikeys WHERE id=?", (kid,)).fetchone()
    return JSONResponse({"ok": True, "info": key_public(row)})


def key_scope(request):
    _user, key = key_auth(request)
    return key["scope"] if key else "session"


def readonly_blocked(request):
    return JSONResponse(
        {"error": "api key ini read-only, pakai key scope full untuk menulis"}, status_code=403
    )




def fnv1a(text):
    h = 0x811C9DC5
    for b in text.encode():
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def issue_challenge():
    now = time.time()
    for seed in [k for k, v in CHALLENGES.items() if v[0] < now]:
        CHALLENGES.pop(seed, None)
    seed = secrets.token_hex(8)
    CHALLENGES[seed] = (now + 600, POW_BITS)
    return seed, POW_BITS


def check_pow(token):
    try:
        seed, nonce = str(token).split(":", 1)
    except ValueError:
        return False
    ent = CHALLENGES.pop(seed, None)
    if not ent or ent[0] < time.time():
        return False
    mask = (1 << ent[1]) - 1
    return (fnv1a(seed + nonce) & mask) == 0


def client_ip(request):
    return (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (request.client.host if request.client else "?")
    )


def rate_limited(key, limit, window):
    now = time.time()
    q = HITS[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def json_err(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


class Tg:
    def __init__(self):
        self.client = None
        self.vault = None
        self.forum = False
        self.ready = False
        self.error = None
        self.mode = "slow"

    async def start(self):
        try:
            session = open(CFG["session_file"]).read().strip()
            self.client = Client(
                StringSession(session), CFG["api_id"], CFG["api_hash"], device_model="Nebula", system_version="1.0"
            )
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.error = "storage node not authorized"
                return
            await self.ensure_vault()
            if await self.restore_db():
                reopen_db()
            await self.rebuild_index()
            repair_folders()
            self.ready = True
            print("[store] ready vault=", self.vault.id, flush=True)
        except Exception as exc:  # noqa: BLE001
            self.error = "storage node unavailable"
            print("[store] start failed:", type(exc).__name__, flush=True)

    async def rebuild_index(self):
        added = 0
        try:
            msgs = await self.client.get_messages(self.vault, limit=1000)
        except Exception as exc:  # noqa: BLE001
            print("[db] reindex skipped:", type(exc).__name__, flush=True)
            return
        for m in msgs:
            f = getattr(m, "file", None)
            cap = getattr(m, "message", "") or ""
            if not f or not cap:
                continue
            mm = re.match(r"uid:(\d+) \| (.+)", cap)
            if not mm:
                continue
            uid = int(mm.group(1))
            name = clean_name(mm.group(2))
            if not name:
                continue
            if db.execute("SELECT 1 FROM files WHERE msg_id=?", (m.id,)).fetchone():
                continue
            if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                continue
            size = int(getattr(f, "size", 0) or 0)
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            db.execute(
                "INSERT INTO files(id,user_id,name,size,mime,msg_id,folder,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (secrets.token_hex(8), uid, name, size, mime, m.id, "", int(getattr(m, "date", None).timestamp()) if getattr(m, "date", None) else int(time.time())),
            )
            added += 1
        if added:
            db.commit()
            print("[db] reindex added", added, flush=True)

    async def restore_db(self):
        try:
            msgs = await self.client.get_messages(self.vault, limit=100)
        except Exception as exc:  # noqa: BLE001
            print("[db] restore scan failed:", type(exc).__name__, flush=True)
            return False
        cands = [
            m
            for m in msgs
            if getattr(getattr(m, "file", None), "name", None) == BACKUP_NAME
        ]
        for m in cands:
            tmp = TMP / ("restore_%s.db" % m.id)
            try:
                await self.client.download_media(m, file=str(tmp))
                con = sqlite3.connect(str(tmp))
                try:
                    ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                finally:
                    con.close()
                if not ok:
                    raise RuntimeError("integrity")
                for suffix in ("-wal", "-shm"):
                    Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
                os.replace(tmp, DB_PATH)
                print("[db] restored from msg", m.id, flush=True)
                return True
            except Exception as exc:  # noqa: BLE001
                print("[db] restore skip", m.id, type(exc).__name__, flush=True)
                tmp.unlink(missing_ok=True)
        return False

    async def backup_db(self):
        if not self.vault:
            return None
        snap = TMP / "snap.db"
        snap.unlink(missing_ok=True)
        src = sqlite3.connect(DB_PATH)
        out = sqlite3.connect(str(snap))
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
        msg = await self.client.send_file(
            self.vault,
            str(snap),
            caption="nebula db",
            force_document=True,
            attributes=[DocumentAttributeFilename(file_name=BACKUP_NAME)],
            parse_mode=None,
        )
        snap.unlink(missing_ok=True)
        await self.prune_backups(keep=3, current=msg.id)
        print("[db] backup msg", msg.id, flush=True)
        return msg.id

    async def prune_backups(self, keep=3, current=None):
        msgs = await self.client.get_messages(self.vault, limit=100)
        ids = [
            m.id
            for m in msgs
            if getattr(getattr(m, "file", None), "name", None) == BACKUP_NAME
        ]
        old = [i for i in ids[keep:] if i != current]
        if old:
            await self.client.delete_messages(self.vault, old)

    async def ensure_vault(self):
        chat_id = kv_get("vault_id")
        title = CFG.get("archive_title") or CFG.get("vault_title") or "Nebula Archive"
        if chat_id:
            try:
                self.vault = await self.client.get_entity(int(chat_id))
                await self.enable_forum()
                return
            except Exception:  # noqa: BLE001
                pass
        async for d in self.client.iter_dialogs():
            if d.is_group and d.name == title:
                self.vault = d.entity
                kv_set("vault_id", self.vault.id)
                await self.enable_forum()
                return
        res = await self.client(
            functions.channels.CreateChannelRequest(
                title=title,
                about="private storage for nebula",
                megagroup=True,
            )
        )
        self.vault = res.chats[0]
        kv_set("vault_id", self.vault.id)
        await self.enable_forum()
        print("[store] vault created", self.vault.id, flush=True)

    async def enable_forum(self):
        try:
            await self.client(
                functions.channels.ToggleForumRequest(channel=self.vault, enabled=True, tabs=True)
            )
            self.forum = True
        except Exception as exc:  # noqa: BLE001
            print("[store] forum toggle failed:", type(exc).__name__, flush=True)
            self.forum = False
        return self.forum

    async def ensure_topic(self, user):
        if user.get("topic_id"):
            return user["topic_id"]
        if not self.vault:
            return None
        try:
            res = await self.client(
                CreateForumTopicRequest(
                    peer=self.vault,
                    title=f"{user['username']} #{user['id']}"[:100],
                    random_id=secrets.randbits(62),
                )
            )
            topic = None
            for upd in getattr(res, "updates", []) or []:
                msg = getattr(upd, "message", None)
                if msg is not None and isinstance(getattr(msg, "action", None), MessageActionTopicCreate):
                    topic = msg.id
                    break
            if topic is None:
                msgs = await self.client.get_messages(self.vault, limit=1)
                topic = msgs[0].id if msgs else None
            db.execute("UPDATE users SET topic_id=? WHERE id=?", (topic, user["id"]))
            db.commit()
            return topic
        except Exception as exc:  # noqa: BLE001
            print("[store] topic failed:", type(exc).__name__, flush=True)
            try:
                if await self.enable_forum():
                    return await self.ensure_topic(user)
            except Exception:  # noqa: BLE001
                pass
            return None

    async def upload(self, user, path, name, mime):
        topic = await self.ensure_topic(user)
        caption = f"uid:{user['id']} | {name}"
        kwargs = dict(
            caption=caption,
            force_document=True,
            attributes=[DocumentAttributeFilename(file_name=name)],
            supports_streaming=True,
            parse_mode=None,
        )
        if topic:
            kwargs["reply_to"] = topic
        size = os.path.getsize(path)
        msg = None
        if size >= 10 * 1024 * 1024:
            try:
                msg = await self.pipelined_upload(path, name, size, kwargs)
                self.mode = "fast"
            except Exception:  # noqa: BLE001
                msg = None
        if msg is None:
            self.mode = "slow"
            msg = await self.client.send_file(self.vault, str(path), **kwargs)
        return msg.id

    async def pipelined_upload(self, path, name, size, kwargs, workers=12):
        part = 512 * 1024
        parts = (size + part - 1) // part
        file_id = secrets.randbits(62) + 1
        sem = asyncio.Semaphore(workers)
        fh = open(path, "rb")

        async def one(i):
            async with sem:
                for attempt in range(4):
                    try:
                        fh.seek(i * part)
                        data = fh.read(part)
                        if not data:
                            raise RuntimeError("short read")
                        await self.client(
                            SaveBigFilePartRequest(
                                file_id=file_id, file_part=i, file_total_parts=parts, bytes=data
                            )
                        )
                        return
                    except Exception:
                        if attempt == 3:
                            raise
                        await asyncio.sleep(0.6 * (attempt + 1))

        try:
            await asyncio.gather(*(one(i) for i in range(parts)))
        finally:
            fh.close()
        handle = InputFileBig(id=file_id, parts=parts, name=name)
        return await self.client.send_file(self.vault, handle, **kwargs)

    async def media(self, msg_id):
        msg = await self.client.get_messages(self.vault, ids=int(msg_id))
        if not msg or not msg.media:
            return None
        return msg

    def download_iter(self, msg, start=0, length=None):
        aligned = start - (start % 4096)
        skip = start - aligned
        return self.client.iter_download(
            msg.media,
            offset=aligned,
            limit=None if length is None else length + skip,
            request_size=512 * 1024,
            chunk_size=512 * 1024,
        )


tg = Tg()


user_locks = defaultdict(asyncio.Lock)
user_next = {}
tg_slots = asyncio.Semaphore(3)


async def do_upload(user, path, name, mime):
    if not tg.ready:
        raise RuntimeError("storage worker belum siap")
    uid = user["id"]
    async with user_locks[uid]:
        async with tg_slots:
            try:
                msg_id = await asyncio.wait_for(tg.upload(user, path, name, mime), timeout=300)
            except asyncio.TimeoutError:
                raise RuntimeError("storage timeout")
        user_next[uid] = time.time() + DELAY
    return msg_id


INDEX_HTML = (BASE / "public" / "index.html").read_text()


def asset_ver():
    h = hashlib.md5()
    for name in ("app.js", "style.css"):
        h.update(str((BASE / "public" / name).stat().st_mtime_ns).encode())
    return h.hexdigest()[:8]


async def index(request):
    resp = HTMLResponse(INDEX_HTML.replace("__V__", asset_ver()))
    resp.headers["cache-control"] = "no-cache"
    return resp


async def service_worker(request):
    body = (BASE / "public" / "sw.js").read_bytes()
    return Response(
        body,
        media_type="application/javascript",
        headers={
            "cache-control": "no-cache",
            "service-worker-allowed": "/",
        },
    )


async def health(request):
    return JSONResponse(
        {
            "ok": tg.ready,
            "error": tg.error,
            "delay": DELAY,
            "queue": sum(1 for l in user_locks.values() if l.locked()),
            "last": STORE_STATS,
        }
    )


async def challenge_endpoint(request):
    seed, bits = issue_challenge()
    return JSONResponse({"seed": seed, "bits": bits, "invite": bool(INVITE)})


def repair_folders():
    cur = db.execute(
        "UPDATE files SET folder='' WHERE folder<>'' AND NOT EXISTS "
        "(SELECT 1 FROM folders f WHERE f.user_id=files.user_id AND f.name=files.folder)"
    )
    if cur.rowcount:
        db.commit()
        print("[db] folder repair", cur.rowcount, flush=True)


async def safe_backup():
    try:
        await tg.backup_db()
    except Exception as exc:  # noqa: BLE001
        print("[db] backup failed:", type(exc).__name__, flush=True)


async def register(request):
    body = await request.json()
    if (body.get("nickname") or "").strip():
        return json_err("ditolak", 400)
    ip = client_ip(request)
    if rate_limited("reg:" + ip, 6, 3600):
        return json_err("terlalu banyak percobaan, coba lagi nanti", 429)
    if not check_pow(body.get("pow") or ""):
        return json_err("verifikasi gagal, muat ulang halaman", 400)
    if INVITE and (body.get("invite") or "").strip() != INVITE:
        return json_err("kode undangan salah", 403)
    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""
    if not re.fullmatch(r"[a-z0-9_.-]{3,24}", username):
        return json_err("username 3-24 huruf/angka/._-")
    if len(password) < 6:
        return json_err("password minimal 6 karakter")
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        return json_err("username sudah dipakai", 409)
    salt, h = hash_pw(password)
    cur = db.execute(
        "INSERT INTO users(username,salt,hash,created_at) VALUES(?,?,?,?)",
        (username, salt, h, int(time.time())),
    )
    db.commit()
    uid = cur.lastrowid
    user = dict(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    if tg.ready:
        await tg.ensure_topic(user)
        asyncio.create_task(safe_backup())
    return await login_user(request, uid)


async def login(request):
    body = await request.json()
    if (body.get("nickname") or "").strip():
        return json_err("ditolak", 400)
    ip = client_ip(request)
    if rate_limited("login:" + ip, 30, 600):
        return json_err("terlalu banyak percobaan, tunggu sebentar", 429)
    username = (body.get("username") or "").strip().lower()
    if rate_limited("login:" + ip + ":" + username, 8, 600):
        return json_err("terlalu banyak percobaan untuk akun ini", 429)
    if not check_pow(body.get("pow") or ""):
        return json_err("verifikasi gagal, muat ulang halaman", 400)
    password = body.get("password") or ""
    row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not check_pw(password, row["salt"], row["hash"]):
        return json_err("username atau password salah", 401)
    return await login_user(request, row["id"])


async def login_user(request, uid):
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)",
        (token, uid, int(time.time()) + 30 * 86400),
    )
    db.commit()
    user = dict(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    resp = JSONResponse(me_payload(user))
    resp.set_cookie(COOKIE, token, max_age=30 * 86400, httponly=True, samesite="lax", path="/")
    return resp


def me_payload(user):
    used = user_used(user["id"])
    tsize, tcount = user_trashed(user["id"])
    return {
        "id": user["id"],
        "username": user["username"],
        "used": used,
        "quota": QUOTA,
        "count": db.execute(
            "SELECT COUNT(*) c FROM files WHERE user_id=? AND trashed_at=0", (user["id"],)
        ).fetchone()["c"],
        "trash": {"size": tsize, "count": tcount},
    }


async def logout(request):
    token = cookies(request).get(COOKIE)
    if token:
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
        db.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


async def me(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    payload = me_payload(user)
    _u, key = key_auth(request)
    payload["auth"] = "key" if key else "session"
    if key:
        payload["key"] = {
            "id": key["id"],
            "name": key["name"],
            "scope": key["scope"],
        }
    return JSONResponse(payload)


async def list_files(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    folder = request.query_params.get("folder", "")
    q = (request.query_params.get("q") or "").strip()
    view = request.query_params.get("view") or ""
    sql = (
        "SELECT id,name,size,mime,folder,share_token,created_at,status,msg_id,trashed_at "
        "FROM files WHERE user_id=?"
    )
    args = [user["id"]]
    if view == "trash":
        sql += " AND trashed_at>0"
    else:
        sql += " AND trashed_at=0 AND folder=?"
        args.append(folder)
    if q:
        sql += " AND name LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY created_at DESC"
    files = [dict(r) for r in db.execute(sql, args).fetchall()]
    folders = [
        dict(r)
        for r in db.execute(
            "SELECT id,name FROM folders WHERE user_id=? ORDER BY name", (user["id"],)
        ).fetchall()
    ]
    return JSONResponse({"files": files, "folders": folders, "del": DELAY})


UPLOAD_CHUNK = 4 * 1024 * 1024
UPLOAD_DIR = TMP / "up"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def up_meta_path(uid):
    return UPLOAD_DIR / (uid + ".json")


def up_data_path(uid):
    return UPLOAD_DIR / (uid + ".part")


def load_meta(uid):
    p = up_meta_path(uid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def save_meta(meta):
    up_meta_path(meta["uid"]).write_text(json.dumps(meta))


upload_locks = {}


def merge_ranges(ranges, start, end):
    out = []
    for a, b in sorted(ranges + [[start, end]]):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def gc_uploads(max_age=6 * 3600):
    now = time.time()
    for p in UPLOAD_DIR.glob("*.json"):
        try:
            if now - p.stat().st_mtime > max_age:
                p.unlink(missing_ok=True)
                p.with_suffix(".part").unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


async def upload_init(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    if not tg.ready:
        return json_err("storage belum siap", 503)
    body = await request.json()
    name = clean_name(body.get("name") or "")
    try:
        size = int(body.get("size") or 0)
    except Exception:  # noqa: BLE001
        size = 0
    if not name or size <= 0:
        return json_err("file tidak valid")
    if size > MAX_FILE:
        return json_err("maksimal 2 GB per file", 413)
    if user_used(user["id"]) + size > QUOTA:
        return json_err("kuota penuh", 413)
    uid = secrets.token_hex(12)
    meta = {
        "uid": uid,
        "user": user["id"],
        "name": name,
        "size": size,
        "folder": clean_folder(body.get("folder") or ""),
        "mime": body.get("mime") or mimetypes.guess_type(name)[0] or "application/octet-stream",
        "received": 0,
        "ranges": [],
        "created": time.time(),
    }
    up_data_path(uid).write_bytes(b"")
    save_meta(meta)
    wait = user_next.get(user["id"], 0) - time.time()
    return JSONResponse({"id": uid, "chunk_size": UPLOAD_CHUNK, "wait": int(wait) + 1 if wait > 0 else 0})


async def upload_chunk(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    uid = request.query_params.get("id") or ""
    try:
        offset = int(request.query_params.get("offset") or 0)
    except Exception:  # noqa: BLE001
        return json_err("offset tidak valid")
    payload = await request.body()
    if not payload:
        return json_err("chunk kosong")
    lock = upload_locks.setdefault(uid, asyncio.Lock())
    async with lock:
        meta = load_meta(uid)
        if not meta or meta.get("user") != user["id"]:
            return json_err("sesi upload tidak ada", 404)
        end = offset + len(payload)
        if offset < 0 or end > meta["size"]:
            return json_err("melebihi ukuran file", 413)
        meta.setdefault("ranges", [])
        meta["ranges"] = merge_ranges(meta["ranges"], offset, end)
        meta["received"] = sum(b - a for a, b in meta["ranges"])
        with open(up_data_path(uid), "r+b") as fh:
            fh.seek(offset)
            fh.write(payload)
        save_meta(meta)
        return JSONResponse({"received": meta["received"]})


async def upload_status(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    meta = load_meta(request.query_params.get("id") or "")
    if not meta or meta.get("user") != user["id"]:
        return json_err("sesi upload tidak ada", 404)
    return JSONResponse(
        {"received": meta["received"], "size": meta["size"], "ranges": meta.get("ranges", [])}
    )


async def upload_finish(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    body = await request.json()
    uid = body.get("id") or ""
    meta = load_meta(uid)
    if not meta or meta.get("user") != user["id"]:
        return json_err("sesi upload tidak ada", 404)
    if meta["received"] != meta["size"]:
        return json_err("upload belum lengkap", 409)
    if user_used(user["id"]) + meta["size"] > QUOTA:
        return json_err("kuota penuh", 413)
    fid_prev = meta.get("file_id")
    if fid_prev:
        row = db.execute("SELECT * FROM files WHERE id=?", (fid_prev,)).fetchone()
        if row:
            return JSONResponse(
                {
                    "file": {
                        "id": row["id"],
                        "name": row["name"],
                        "size": row["size"],
                        "mime": row["mime"],
                        "folder": row["folder"],
                        "status": row["status"],
                    },
                    "me": me_payload(user),
                    "delay": DELAY,
                }
            )

    # dedup: kalau byte-nya sudah ada di akun ini, jangan kirim ulang ke storage
    digest = ""
    try:
        digest = file_hash(up_data_path(uid))
    except Exception:  # noqa: BLE001
        digest = ""
    if digest:
        dup = db.execute(
            "SELECT * FROM files WHERE user_id=? AND hash=? AND status='ready' AND trashed_at=0 LIMIT 1",
            (user["id"], digest),
        ).fetchone()
        if dup and dup["msg_id"]:
            wait = user_next.get(user["id"], 0) - time.time()
            if wait > 2:
                return JSONResponse({"retry": True, "wait": int(wait) + 1})
            fid = secrets.token_hex(8)
            db.execute(
                "INSERT INTO files(id,user_id,name,size,mime,msg_id,folder,created_at,status,hash)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (fid, user["id"], meta["name"], meta["size"], meta["mime"], dup["msg_id"],
                 meta["folder"], int(time.time()), "ready", digest),
            )
            db.commit()
            up_data_path(uid).unlink(missing_ok=True)
            up_meta_path(uid).unlink(missing_ok=True)
            asyncio.create_task(safe_backup())
            return JSONResponse(
                {
                    "file": {
                        "id": fid,
                        "name": meta["name"],
                        "size": meta["size"],
                        "mime": meta["mime"],
                        "folder": meta["folder"],
                        "status": "ready",
                        "dedup": True,
                    },
                    "me": me_payload(user),
                    "delay": DELAY,
                }
            )

    wait = user_next.get(user["id"], 0) - time.time()
    if wait > 2:
        return JSONResponse({"retry": True, "wait": int(wait) + 1})
    fid = secrets.token_hex(8)
    db.execute(
        "INSERT INTO files(id,user_id,name,size,mime,msg_id,folder,created_at,status,hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (fid, user["id"], meta["name"], meta["size"], meta["mime"], 0, meta["folder"], int(time.time()), "pending", digest),
    )
    db.commit()
    meta["file_id"] = fid
    save_meta(meta)
    asyncio.create_task(store_pending(fid, user["id"], uid, meta["name"], meta["mime"]))
    asyncio.create_task(safe_backup())
    return JSONResponse(
        {
            "file": {
                "id": fid,
                "name": meta["name"],
                "size": meta["size"],
                "mime": meta["mime"],
                "folder": meta["folder"],
                "status": "pending",
            },
            "me": me_payload(user),
            "delay": DELAY,
        }
    )


STORE_STATS = {}


async def store_pending(fid, user_id, meta_id, name, mime):
    path = up_data_path(meta_id)
    t0 = time.time()
    try:
        user = dict(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        msg_id = await do_upload(user, str(path), name, mime)
        db.execute("UPDATE files SET msg_id=?, status='ready' WHERE id=?", (msg_id, fid))
        db.commit()
        asyncio.create_task(safe_backup())
        secs = time.time() - t0
        size = os.path.getsize(path) if path.exists() else 0
        STORE_STATS.update(
            {"mb": round(size / 1048576, 1), "secs": round(secs, 1), "mbps": round(size / 1048576 / max(0.1, secs), 2), "mode": tg.mode}
        )
        print("[store] filed", fid, "msg", msg_id, flush=True)
    except Exception as exc:  # noqa: BLE001
        db.execute("UPDATE files SET status='failed' WHERE id=?", (fid,))
        db.commit()
        print("[store] store failed", fid, type(exc).__name__, flush=True)
    finally:
        path.unlink(missing_ok=True)


async def upload(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    if not tg.ready:
        return json_err(f"storage belum siap: {tg.error}", 503)
    form = await request.form()
    folder = clean_folder(form.get("folder") or "")
    uploads = [f for f in form.getlist("files") if getattr(f, "filename", None)]
    if not uploads:
        return json_err("tidak ada file")
    used = user_used(user["id"])
    already = db.execute("SELECT COUNT(*) c FROM files WHERE user_id=?", (user["id"],)).fetchone()["c"]
    out = []
    for up in uploads:
        name = clean_name(up.filename)
        tmp = TMP / f"{secrets.token_hex(8)}.part"
        size = 0
        with open(tmp, "wb") as fh:
            while True:
                chunk = await up.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if used + size > QUOTA:
                    fh.close()
                    tmp.unlink(missing_ok=True)
                    return json_err("kuota penuh", 413)
                if size > MAX_FILE:
                    fh.close()
                    tmp.unlink(missing_ok=True)
                    return json_err("maksimal 2 GB per file", 413)
                fh.write(chunk)
        if size == 0:
            tmp.unlink(missing_ok=True)
            continue
        mime = up.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            msg_id = await do_upload(user, str(tmp), name, mime)
        except Exception as exc:  # noqa: BLE001
            Path(tmp).unlink(missing_ok=True)
            return json_err(f"upload gagal: {exc}", 502)
        fid = secrets.token_hex(8)
        db.execute(
            "INSERT INTO files(id,user_id,name,size,mime,msg_id,folder,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (fid, user["id"], name, size, mime, msg_id, folder, int(time.time())),
        )
        db.commit()
        used += size
        already += 1
        out.append({"id": fid, "name": name, "size": size, "mime": mime, "folder": folder})
    return JSONResponse({"files": out, "me": me_payload(user), "delay": DELAY})


def migrate():
    cols = {r["name"] for r in db.execute("PRAGMA table_info(files)").fetchall()}
    if "status" not in cols:
        db.execute("ALTER TABLE files ADD COLUMN status TEXT DEFAULT 'ready'")
        db.commit()
    if "hash" not in cols:
        db.execute("ALTER TABLE files ADD COLUMN hash TEXT NOT NULL DEFAULT ''")
        db.commit()
    if "trashed_at" not in cols:
        db.execute("ALTER TABLE files ADD COLUMN trashed_at INTEGER NOT NULL DEFAULT 0")
        db.commit()
    db.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files(user_id, hash)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_files_trash ON files(user_id, trashed_at)")
    db.execute("UPDATE files SET status='ready' WHERE status IS NULL")
    db.execute("UPDATE files SET status='failed' WHERE status='pending' AND (msg_id IS NULL OR msg_id=0)")
    db.commit()


def get_file(fid, user_id=None, share=None):
    if share:
        return db.execute("SELECT * FROM files WHERE share_token=?", (share,)).fetchone()
    return db.execute("SELECT * FROM files WHERE id=? AND user_id=?", (fid, user_id)).fetchone()


def disposition(name, inline):
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename*=UTF-8''{quote(name)}"


async def send_file(request, row, inline=False):
    if not row["msg_id"]:
        if (row["status"] or "") == "failed":
            return json_err("file gagal diproses, hapus lalu upload ulang", 409)
        return json_err("file masih diproses, coba lagi sebentar", 409)
    try:
        msg = await tg.media(row["msg_id"])
    except Exception as exc:  # noqa: BLE001
        return json_err(f"gagal ambil file: {exc}", 502)
    if not msg:
        return json_err("file tidak ada di storage", 404)
    size = row["size"]
    mime = row["mime"] or "application/octet-stream"
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range")
    if rng:
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
    length = end - start + 1
    headers = {
        "Content-Type": mime,
        "Content-Disposition": disposition(row["name"], inline),
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "private, max-age=3600",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    async def body():
        produced = 0
        skip = start % 4096
        async for chunk in tg.download_iter(msg, start, length):
            if skip:
                if len(chunk) <= skip:
                    skip -= len(chunk)
                    continue
                chunk = chunk[skip:]
                skip = 0
            if produced + len(chunk) > length:
                chunk = chunk[: length - produced]
            produced += len(chunk)
            if chunk:
                yield chunk
            if produced >= length:
                break

    return StreamingResponse(body(), status_code=status, headers=headers)


async def download(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    row = get_file(request.path_params["fid"], user["id"])
    if not row:
        return json_err("not found", 404)
    return await send_file(request, row, inline=False)


async def view(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    row = get_file(request.path_params["fid"], user["id"])
    if not row:
        return json_err("not found", 404)
    return await send_file(request, row, inline=True)


async def share_info(request):
    row = get_file(None, share=request.path_params["token"])
    if not row:
        return json_err("not found", 404)
    return JSONResponse(
        {"name": row["name"], "size": row["size"], "mime": row["mime"], "created_at": row["created_at"]}
    )


async def share_download(request):
    row = get_file(None, share=request.path_params["token"])
    if not row:
        return json_err("not found", 404)
    return await send_file(request, row, inline=request.path_params["token"] in request.url.path and request.query_params.get("inline") == "1")


async def rename(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    row = get_file(request.path_params["fid"], user["id"])
    if not row:
        return json_err("not found", 404)
    body = await request.json()
    name = clean_name(body.get("name"))
    folder = clean_name(body.get("folder")) if body.get("folder") is not None else row["folder"]
    db.execute("UPDATE files SET name=?, folder=? WHERE id=?", (name, folder, row["id"]))
    db.commit()
    return JSONResponse({"ok": True, "name": name, "folder": folder})


TRASH_DAYS = int(os.environ.get("NEBULA_TRASH_DAYS", "30"))


def storage_refs(msg_id):
    """Berapa file lain yang masih memakai pesan storage yang sama (efek dedup)."""
    return db.execute(
        "SELECT COUNT(*) c FROM files WHERE msg_id=?", (msg_id,)
    ).fetchone()["c"]


async def drop_storage(msg_id):
    """Hapus pesan storage hanya kalau tidak ada file lain yang menunjuk ke sana.

    Dipanggil SETELAH row-nya dihapus dari DB, jadi hitungan ini hanya menyisakan
    file lain yang berbagi pesan storage yang sama.
    """
    if not msg_id:
        return
    if storage_refs(msg_id) > 0:
        return
    try:
        if tg.ready and tg.vault:
            await tg.client.delete_messages(tg.vault, [msg_id])
    except Exception as exc:  # noqa: BLE001
        print("[store] delete failed:", type(exc).__name__, flush=True)


async def delete(request):
    """Soft delete: file masuk tong sampah, storage tetap ada sampai di-purge."""
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    row = get_file(request.path_params["fid"], user["id"])
    if not row:
        return json_err("not found", 404)
    db.execute(
        "UPDATE files SET trashed_at=?, share_token=NULL WHERE id=?",
        (int(time.time()), row["id"]),
    )
    db.commit()
    return JSONResponse({"ok": True, "trashed": True, "me": me_payload(user)})


async def restore(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    row = get_file(request.path_params["fid"], user["id"])
    if not row:
        return json_err("not found", 404)
    if not row["trashed_at"]:
        return json_err("file tidak di tong sampah", 409)
    used = user_used(user["id"])
    if used + row["size"] > QUOTA:
        return json_err("kuota penuh, kosongkan dulu", 413)
    db.execute("UPDATE files SET trashed_at=0 WHERE id=?", (row["id"],))
    db.commit()
    return JSONResponse({"ok": True, "me": me_payload(user)})


async def purge(request):
    """Hapus permanen satu file dari tong sampah (atau paksa hapus file aktif)."""
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    row = get_file(request.path_params["fid"], user["id"])
    if not row:
        return json_err("not found", 404)
    db.execute("DELETE FROM files WHERE id=?", (row["id"],))
    db.commit()
    await drop_storage(row["msg_id"])
    return JSONResponse({"ok": True, "me": me_payload(user)})


async def trash_endpoint(request):
    """GET: isi tong sampah. DELETE: kosongkan semua."""
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if request.method == "DELETE":
        if key_scope(request) == "read":
            return readonly_blocked(request)
        rows = db.execute(
            "SELECT id,msg_id FROM files WHERE user_id=? AND trashed_at>0", (user["id"],)
        ).fetchall()
        db.execute("DELETE FROM files WHERE user_id=? AND trashed_at>0", (user["id"],))
        db.commit()
        for r in rows:
            await drop_storage(r["msg_id"])
        return JSONResponse({"ok": True, "removed": len(rows), "me": me_payload(user)})
    rows = db.execute(
        "SELECT id,name,size,mime,folder,created_at,trashed_at,status FROM files"
        " WHERE user_id=? AND trashed_at>0 ORDER BY trashed_at DESC",
        (user["id"],),
    ).fetchall()
    left = 0
    if TRASH_DAYS > 0:
        deadline = int(time.time()) - TRASH_DAYS * 86400
        left = db.execute(
            "SELECT COUNT(*) c FROM files WHERE user_id=? AND trashed_at>0 AND trashed_at<?",
            (user["id"], deadline),
        ).fetchone()["c"]
    return JSONResponse(
        {"files": [dict(r) for r in rows], "days": TRASH_DAYS, "expired": left}
    )


async def purge_expired():
    """Buang file tong sampah yang lewat masa simpan."""
    if TRASH_DAYS <= 0:
        return 0
    deadline = int(time.time()) - TRASH_DAYS * 86400
    rows = db.execute(
        "SELECT id,msg_id FROM files WHERE trashed_at>0 AND trashed_at<?", (deadline,)
    ).fetchall()
    if not rows:
        return 0
    db.execute("DELETE FROM files WHERE trashed_at>0 AND trashed_at<?", (deadline,))
    db.commit()
    for r in rows:
        await drop_storage(r["msg_id"])
    print("[trash] purged", len(rows), flush=True)
    return len(rows)


async def share(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    row = get_file(request.path_params["fid"], user["id"])
    if not row:
        return json_err("not found", 404)
    token = row["share_token"] or secrets.token_urlsafe(12)
    db.execute("UPDATE files SET share_token=? WHERE id=?", (token, row["id"]))
    db.commit()
    return JSONResponse({"token": token})


async def unshare(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    row = get_file(request.path_params["fid"], user["id"])
    if not row:
        return json_err("not found", 404)
    db.execute("UPDATE files SET share_token=NULL WHERE id=?", (row["id"],))
    db.commit()
    return JSONResponse({"ok": True})


async def folders(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if request.method == "POST":
        if key_scope(request) == "read":
            return readonly_blocked(request)
        body = await request.json()
        name = clean_name(body.get("name"))
        if not name:
            return json_err("nama folder kosong")
        db.execute(
            "INSERT OR IGNORE INTO folders(user_id,name,created_at) VALUES(?,?,?)",
            (user["id"], name, int(time.time())),
        )
        db.commit()
        return JSONResponse({"ok": True, "name": name})
    rows = db.execute("SELECT id,name FROM folders WHERE user_id=? ORDER BY name", (user["id"],)).fetchall()
    return JSONResponse({"folders": [dict(r) for r in rows]})


async def folder_delete(request):
    user = current_user(request)
    if not user:
        return json_err("unauthorized", 401)
    if key_scope(request) == "read":
        return readonly_blocked(request)
    fid = int(request.path_params["fid"])
    row = db.execute("SELECT * FROM folders WHERE id=? AND user_id=?", (fid, user["id"])).fetchone()
    if not row:
        return json_err("not found", 404)
    db.execute("DELETE FROM folders WHERE id=?", (fid,))
    db.execute("UPDATE files SET folder='' WHERE user_id=? AND folder=?", (user["id"], row["name"]))
    db.commit()
    return JSONResponse({"ok": True})


@asynccontextmanager
async def lifespan(app):
    gc_uploads()
    await tg.start()
    migrate()
    stop = asyncio.Event()

    async def backup_loop():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=BACKUP_EVERY)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await tg.backup_db()
            except Exception as exc:  # noqa: BLE001
                print("[db] backup failed:", repr(exc)[:160], flush=True)
            gc_uploads(3600)
            try:
                await purge_expired()
            except Exception as exc:  # noqa: BLE001
                print("[trash] purge failed:", type(exc).__name__, flush=True)

    task = None
    if tg.ready:
        try:
            await tg.backup_db()
        except Exception as exc:  # noqa: BLE001
            print("[db] initial backup failed:", repr(exc)[:160], flush=True)
        if BACKUP_EVERY > 0:
            task = asyncio.create_task(backup_loop())
    try:
        yield
    finally:
        stop.set()
        if task:
            task.cancel()
        try:
            if tg.ready:
                await asyncio.wait_for(tg.backup_db(), 25)
        except Exception:  # noqa: BLE001
            pass
        if tg.client:
            await tg.client.disconnect()


app = Starlette(
    routes=[
        Route("/", index),
        Route("/s/{token}", index),
        Route("/sw.js", service_worker),
        Route("/api/health", health),
        Route("/api/challenge", challenge_endpoint),
        Route("/api/register", register, methods=["POST"]),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/me", me),
        Route("/api/files", list_files),
        Route("/api/upload", upload, methods=["POST"]),
        Route("/api/upload/init", upload_init, methods=["POST"]),
        Route("/api/upload/chunk", upload_chunk, methods=["POST"]),
        Route("/api/upload/status", upload_status, methods=["GET"]),
        Route("/api/upload/finish", upload_finish, methods=["POST"]),
        Route("/api/file/{fid}/download", download),
        Route("/api/file/{fid}/view", view),
        Route("/api/file/{fid}", rename, methods=["PATCH"]),
        Route("/api/file/{fid}", delete, methods=["DELETE"]),
        Route("/api/file/{fid}/purge", purge, methods=["DELETE"]),
        Route("/api/file/{fid}/restore", restore, methods=["POST"]),
        Route("/api/trash", trash_endpoint, methods=["GET", "DELETE"]),
        Route("/api/file/{fid}/share", share, methods=["POST"]),
        Route("/api/file/{fid}/share", unshare, methods=["DELETE"]),
        Route("/api/folders", folders, methods=["GET", "POST"]),
        Route("/api/folders/{fid}", folder_delete, methods=["DELETE"]),
        Route("/api/share/{token}", share_info),
        Route("/api/share/{token}/download", share_download),
        Route("/api/keys", keys_endpoint, methods=["GET", "POST"]),
        Route("/api/keys/{kid}", key_detail, methods=["PATCH", "DELETE"]),
        Mount("/static", StaticFiles(directory=BASE / "public")),
    ],
    lifespan=lifespan,
)

class NoCacheStatic:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith("/static"):
            async def _send(message):
                if message["type"] == "http.response.start":
                    headers = message.setdefault("headers", [])
                    headers[:] = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-cache"))
                await send(message)

            await self.inner(scope, receive, _send)
        else:
            await self.inner(scope, receive, send)


app = NoCacheStatic(app)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", CFG.get("port", 8080))), log_level="info")
