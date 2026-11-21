import json


MAX_AGENT_VALUE_BYTES = 4 * 1024 * 1024

AGENT_SCHEMA = """
    CREATE TABLE IF NOT EXISTS agent_states(
        user_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        version INTEGER NOT NULL,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, key)
    );
    CREATE TABLE IF NOT EXISTS agent_leases(
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        holder_id TEXT,
        expires_at INTEGER NOT NULL,
        contender_id TEXT,
        contender_seen_at INTEGER,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, name)
    );
    CREATE INDEX IF NOT EXISTS idx_agent_leases_expires ON agent_leases(expires_at);
"""


class AgentStateConflict(Exception):
    def __init__(self, current_version):
        super().__init__("state version conflict")
        self.current_version = current_version


class AgentValueInvalid(ValueError):
    pass


class AgentValueTooLarge(ValueError):
    pass


class AgentLeaseConflict(Exception):
    pass


def init_agent_schema(conn):
    conn.executescript(AGENT_SCHEMA)


def _state_public(row):
    return {
        "key": row["key"],
        "version": row["version"],
        "value": json.loads(row["value"]),
        "updated_at": row["updated_at"],
    }


def _lease_public(row):
    return {
        "name": row["name"],
        "holder_id": row["holder_id"],
        "expires_at": row["expires_at"],
        "contender_id": row["contender_id"],
        "contender_seen_at": row["contender_seen_at"],
        "updated_at": row["updated_at"],
    }


def _serialize_value(value):
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        size = len(text.encode("utf-8"))
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise AgentValueInvalid("value must be valid JSON") from exc
    if size > MAX_AGENT_VALUE_BYTES:
        raise AgentValueTooLarge("serialized value exceeds 4 MiB")
    return text


def get_agent_state(conn, user_id, key):
    row = conn.execute(
        "SELECT key,version,value,updated_at FROM agent_states WHERE user_id=? AND key=?",
        (user_id, key),
    ).fetchone()
    return _state_public(row) if row else None


def put_agent_state(conn, user_id, key, expected_version, value, now):
    value_text = _serialize_value(value)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT version FROM agent_states WHERE user_id=? AND key=?",
            (user_id, key),
        ).fetchone()
        current_version = row["version"] if row else None
        if current_version != expected_version:
            raise AgentStateConflict(current_version)

        if row:
            version = current_version + 1
            conn.execute(
                "UPDATE agent_states SET version=?,value=?,updated_at=? "
                "WHERE user_id=? AND key=? AND version=?",
                (version, value_text, now, user_id, key, current_version),
            )
        else:
            version = 1
            conn.execute(
                "INSERT INTO agent_states(user_id,key,version,value,updated_at) VALUES(?,?,?,?,?)",
                (user_id, key, version, value_text, now),
            )
        result = get_agent_state(conn, user_id, key)
        conn.commit()
        return result
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def get_agent_lease(conn, user_id, name):
    row = conn.execute(
        "SELECT name,holder_id,expires_at,contender_id,contender_seen_at,updated_at "
        "FROM agent_leases WHERE user_id=? AND name=?",
        (user_id, name),
    ).fetchone()
    return _lease_public(row) if row else None


def acquire_agent_lease(conn, user_id, name, holder_id, ttl_seconds, now):
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT holder_id,expires_at FROM agent_leases WHERE user_id=? AND name=?",
            (user_id, name),
        ).fetchone()
        acquired = not row or row["holder_id"] == holder_id or row["expires_at"] <= now
        if not row:
            conn.execute(
                "INSERT INTO agent_leases("
                "user_id,name,holder_id,expires_at,contender_id,contender_seen_at,updated_at"
                ") VALUES(?,?,?,?,NULL,NULL,?)",
                (user_id, name, holder_id, now + ttl_seconds, now),
            )
        elif acquired:
            conn.execute(
                "UPDATE agent_leases SET holder_id=?,expires_at=?,contender_id=NULL,"
                "contender_seen_at=NULL,updated_at=? WHERE user_id=? AND name=?",
                (holder_id, now + ttl_seconds, now, user_id, name),
            )
        else:
            conn.execute(
                "UPDATE agent_leases SET contender_id=?,contender_seen_at=?,updated_at=? "
                "WHERE user_id=? AND name=?",
                (holder_id, now, now, user_id, name),
            )
        lease = get_agent_lease(conn, user_id, name)
        conn.commit()
        return acquired, lease
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def renew_agent_lease(conn, user_id, name, holder_id, ttl_seconds, now):
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT holder_id,expires_at FROM agent_leases WHERE user_id=? AND name=?",
            (user_id, name),
        ).fetchone()
        if not row or row["holder_id"] != holder_id or row["expires_at"] <= now:
            raise AgentLeaseConflict("lease is not held by holder")
        conn.execute(
            "UPDATE agent_leases SET expires_at=?,updated_at=? WHERE user_id=? AND name=?",
            (now + ttl_seconds, now, user_id, name),
        )
        lease = get_agent_lease(conn, user_id, name)
        conn.commit()
        return lease
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def release_agent_lease(conn, user_id, name, holder_id, now):
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT holder_id,expires_at FROM agent_leases WHERE user_id=? AND name=?",
            (user_id, name),
        ).fetchone()
        if not row or row["holder_id"] != holder_id or row["expires_at"] <= now:
            raise AgentLeaseConflict("lease is not held by holder")
        conn.execute(
            "UPDATE agent_leases SET holder_id=NULL,expires_at=?,contender_id=NULL,"
            "contender_seen_at=NULL,updated_at=? WHERE user_id=? AND name=?",
            (now, now, user_id, name),
        )
        lease = get_agent_lease(conn, user_id, name)
        conn.commit()
        return lease
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
