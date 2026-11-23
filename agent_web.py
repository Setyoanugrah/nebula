import hmac
import json
import math
import re
import time
import uuid
from datetime import datetime, timezone

from agent_public_status import allowed_public_status_origin


WEB_CHAT_ID = "web:owner"
MAX_MESSAGE_CHARS = 12_000
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_OPEN_TASKS = 10
MAX_CONVERSATION_MESSAGES = 120
MAX_TASK_MESSAGES = 80
MAX_TASK_LIST = 30

_OPEN_TASK_STATUSES = {"waiting", "running", "waiting_approval"}
_TASK_ID_RE = re.compile(r"[0-9a-f-]{36}\Z", re.IGNORECASE)


class WebStateError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def allowed_web_origin(origin):
    return allowed_public_status_origin(origin)


def web_token_from_headers(headers):
    auth = (headers.get("authorization") or "").strip()
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return (headers.get("x-trixom-token") or "").strip()


def authorized_web_token(supplied, expected):
    if not isinstance(supplied, str) or not supplied or not isinstance(expected, str) or not expected:
        return False
    try:
        return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeError:
        return False


def valid_task_id(value):
    return isinstance(value, str) and bool(_TASK_ID_RE.fullmatch(value))


def clean_message(value):
    if not isinstance(value, str):
        raise WebStateError("message content must be text")
    content = value.strip()
    if not content:
        raise WebStateError("message content is empty")
    if len(content) > MAX_MESSAGE_CHARS:
        raise WebStateError(f"message content exceeds {MAX_MESSAGE_CHARS} characters")
    if "\x00" in content:
        raise WebStateError("message content contains invalid characters")
    return content


def _iso_now(now):
    if isinstance(now, datetime):
        value = now
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
    else:
        value = datetime.fromtimestamp(float(now), tz=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp(value):
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                number = parsed.timestamp()
            except (ValueError, OverflowError):
                return 0.0
    else:
        return 0.0
    if not math.isfinite(number) or number < 0:
        return 0.0
    while number >= 100_000_000_000:
        number /= 1000
    return number


def _safe_text(value, limit):
    if not isinstance(value, str):
        return None
    return value[:limit]


def _safe_task(task):
    if not isinstance(task, dict):
        return None
    task_id = task.get("id")
    if not valid_task_id(task_id):
        return None
    status = task.get("status")
    if not isinstance(status, str):
        status = "unknown"
    iteration = task.get("iteration")
    if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 0:
        iteration = 0
    return {
        "id": task_id,
        "status": status,
        "iteration": iteration,
        "last_error": _safe_text(task.get("last_error"), 1_500),
        "created_at": _safe_text(task.get("created_at"), 80),
        "updated_at": _safe_text(task.get("updated_at"), 80),
    }


def _safe_message(message):
    if not isinstance(message, dict):
        return None
    message_id = message.get("id")
    if not isinstance(message_id, str) or not message_id:
        return None
    role = message.get("role")
    if role not in {"user", "assistant"}:
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content:
        return None
    task_id = message.get("task_id")
    return {
        "id": message_id[:80],
        "task_id": task_id if valid_task_id(task_id) else None,
        "role": role,
        "kind": _safe_text(message.get("kind"), 40),
        "content": content[:MAX_MESSAGE_CHARS],
        "created_at": _safe_text(message.get("created_at"), 80),
    }


def _normalize_state(value):
    if not isinstance(value, dict):
        raise WebStateError("agent state is malformed", 409)
    if value.get("schema_version") != 1:
        raise WebStateError("agent state schema is unsupported", 409)
    if not isinstance(value.get("kv"), dict):
        raise WebStateError("agent state kv is malformed", 409)
    for field in ("telegram_updates", "messages", "tasks", "tool_runs", "approvals", "runner_instances"):
        if not isinstance(value.get(field), list):
            raise WebStateError(f"agent state {field} is malformed", 409)
    return value


def decode_state(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise WebStateError("agent state is unreadable", 409) from exc
    return _normalize_state(value)


def encode_state(state):
    try:
        text = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise WebStateError("agent state is not serializable", 500) from exc
    if len(text.encode("utf-8")) >= MAX_STATE_BYTES:
        raise WebStateError("agent state is full; clear old tasks first", 507)
    return text


def _web_messages(state):
    messages = state.get("messages")
    return [message for message in messages if isinstance(message, dict) and message.get("chat_id") == WEB_CHAT_ID]


def _web_tasks(state):
    tasks = state.get("tasks")
    return [task for task in tasks if isinstance(task, dict) and task.get("chat_id") == WEB_CHAT_ID]


def _session_start(state):
    entry = state["kv"].get(f"conversation_start:{WEB_CHAT_ID}")
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    return value if isinstance(value, str) and value else None


def conversation_payload(state):
    state = _normalize_state(state)
    since = _session_start(state)
    messages = []
    for message in _web_messages(state):
        safe = _safe_message(message)
        if not safe:
            continue
        if since is not None and (_timestamp(safe["created_at"]) < _timestamp(since)):
            continue
        messages.append(safe)
    messages.sort(key=lambda item: (_timestamp(item["created_at"]), item["id"]))
    messages = messages[-MAX_CONVERSATION_MESSAGES:]

    tasks = [safe for safe in (_safe_task(task) for task in _web_tasks(state)) if safe]
    tasks.sort(key=lambda item: (_timestamp(item["updated_at"]), item["id"]), reverse=True)
    return {
        "messages": messages,
        "tasks": tasks[:MAX_TASK_LIST],
        "session_started_at": since,
    }


def task_payload(state, task_id):
    state = _normalize_state(state)
    task = next((item for item in _web_tasks(state) if item.get("id") == task_id), None)
    if task is None:
        raise WebStateError("task not found", 404)
    messages = []
    for message in _web_messages(state):
        if message.get("task_id") != task_id:
            continue
        safe = _safe_message(message)
        if safe:
            messages.append(safe)
    messages.sort(key=lambda item: (_timestamp(item["created_at"]), item["id"]))
    return {"task": _safe_task(task), "messages": messages[-MAX_TASK_MESSAGES:]}


def append_web_task(state, content, now=None):
    state = _normalize_state(state)
    content = clean_message(content)
    open_tasks = [task for task in _web_tasks(state) if task.get("status") in _OPEN_TASK_STATUSES]
    if len(open_tasks) >= MAX_OPEN_TASKS:
        raise WebStateError("too many open tasks; wait or cancel one first", 429)

    created = _iso_now(datetime.now(timezone.utc) if now is None else now)
    task_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    task = {
        "id": task_id,
        "chat_id": WEB_CHAT_ID,
        "source_update_id": None,
        "status": "waiting",
        "prompt": content,
        "summary": "",
        "checkpoint_json": json.dumps({"messages": [], "pending": []}, separators=(",", ":")),
        "iteration": 0,
        "last_error": None,
        "created_at": created,
        "updated_at": created,
    }
    message = {
        "id": message_id,
        "chat_id": WEB_CHAT_ID,
        "task_id": task_id,
        "source_update_id": None,
        "telegram_message_id": None,
        "role": "user",
        "kind": "text",
        "content": content,
        "created_at": created,
    }
    state["tasks"].append(task)
    state["messages"].append(message)
    return _safe_task(task)


def cancel_web_task(state, task_id, now=None):
    state = _normalize_state(state)
    if not valid_task_id(task_id):
        raise WebStateError("invalid task id")
    task = next((item for item in _web_tasks(state) if item.get("id") == task_id), None)
    if task is None:
        raise WebStateError("task not found", 404)
    updated = _iso_now(datetime.now(timezone.utc) if now is None else now)
    if task.get("status") in _OPEN_TASK_STATUSES or task.get("status") == "failed":
        task["status"] = "cancelled"
        task["last_error"] = "Cancelled from web chat"
        task["updated_at"] = updated
    for approval in state["approvals"]:
        if isinstance(approval, dict) and approval.get("task_id") == task_id and approval.get("status") == "pending":
            approval["status"] = "rejected"
            approval["decided_at"] = updated
    return _safe_task(task)


def reset_web_session(state, now=None):
    state = _normalize_state(state)
    updated = _iso_now(datetime.now(timezone.utc) if now is None else now)
    open_ids = set()
    for task in _web_tasks(state):
        if task.get("status") in _OPEN_TASK_STATUSES:
            task["status"] = "cancelled"
            task["last_error"] = "Superseded by new chat"
            task["updated_at"] = updated
            if valid_task_id(task.get("id")):
                open_ids.add(task["id"])
    for approval in state["approvals"]:
        if (
            isinstance(approval, dict)
            and approval.get("task_id") in open_ids
            and approval.get("status") == "pending"
        ):
            approval["status"] = "rejected"
            approval["decided_at"] = updated
    state["kv"][f"conversation_start:{WEB_CHAT_ID}"] = {"value": updated, "updated_at": updated}
    return updated


def read_newest_agent_state(conn, key="trixagent"):
    row = conn.execute(
        "SELECT user_id,version,value,updated_at FROM agent_states WHERE key=? "
        "ORDER BY updated_at DESC,version DESC,user_id DESC LIMIT 1",
        (key,),
    ).fetchone()
    if not row:
        raise WebStateError("agent state is not initialized", 409)
    return {
        "user_id": row["user_id"],
        "version": row["version"],
        "value": decode_state(row["value"]),
        "updated_at": row["updated_at"],
    }


def read_web_conversation(conn, key="trixagent"):
    state = read_newest_agent_state(conn, key)
    return conversation_payload(state["value"])


def read_web_task(conn, task_id, key="trixagent"):
    state = read_newest_agent_state(conn, key)
    return task_payload(state["value"], task_id)


def mutate_web_state(conn, mutator, key="trixagent", now=None):
    try:
        conn.execute("BEGIN IMMEDIATE")
        state = read_newest_agent_state(conn, key)
        result = mutator(state["value"])
        value_text = encode_state(state["value"])
        version = state["version"] + 1
        updated_at = int(time.time()) if now is None else int(now)
        cursor = conn.execute(
            "UPDATE agent_states SET version=?,value=?,updated_at=? "
            "WHERE user_id=? AND key=? AND version=?",
            (version, value_text, updated_at, state["user_id"], key, state["version"]),
        )
        if cursor.rowcount != 1:
            raise WebStateError("agent state changed while updating; retry", 409)
        conn.commit()
        return result
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def create_web_task(conn, content, key="trixagent", now=None):
    return mutate_web_state(conn, lambda state: append_web_task(state, content, now), key, now)


def cancel_web_task_db(conn, task_id, key="trixagent", now=None):
    return mutate_web_state(conn, lambda state: cancel_web_task(state, task_id, now), key, now)


def reset_web_session_db(conn, key="trixagent", now=None):
    return mutate_web_state(conn, lambda state: reset_web_session(state, now), key, now)
