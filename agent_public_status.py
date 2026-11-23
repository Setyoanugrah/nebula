import json
import math
import re
from datetime import datetime, timezone


PUBLIC_STATUS_ORIGIN = "https://trixtom.my.id"
PUBLIC_STATUS_MAX_AGE = 120
PROVIDERS = {
    "text": "Groq GPT-OSS 120B",
    "vision": "Gemini Flash",
}

_LOCALHOST_ORIGIN_RE = re.compile(r"http://localhost(?::([0-9]{1,5}))?\Z")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+:/()\-]{0,95}\Z")
_RUNNER_FIELDS = {"active", "is_active", "status", "model", "started_at", "last_seen_at"}
_TASK_FIELDS = {"status", "iteration", "last_iteration", "updated_at"}
_TASK_STATUSES = ("waiting", "running", "waiting_approval", "failed", "completed")
_TASK_STATUS_ALIASES = {
    "queued": "waiting",
    "pending": "waiting",
    "in_progress": "running",
    "awaiting_approval": "waiting_approval",
    "approval_required": "waiting_approval",
    "error": "failed",
    "done": "completed",
    "complete": "completed",
    "success": "completed",
    "succeeded": "completed",
}
_RUNNER_STATUSES = {
    "active",
    "busy",
    "failed",
    "idle",
    "offline",
    "ready",
    "running",
    "starting",
    "stopped",
    "stopping",
}
_RUNNER_STATUS_ALIASES = {
    "alive": "active",
    "dead": "offline",
    "error": "failed",
    "in_progress": "running",
    "online": "active",
}
_ACTIVE_RUNNER_STATUSES = {"active", "busy", "idle", "ready", "running", "starting"}


def allowed_public_status_origin(origin):
    if not isinstance(origin, str):
        return None
    origin = origin.strip()
    if origin == PUBLIC_STATUS_ORIGIN:
        return origin
    match = _LOCALHOST_ORIGIN_RE.fullmatch(origin)
    if not match:
        return None
    port = match.group(1)
    if port is not None and not 1 <= int(port) <= 65535:
        return None
    return origin


def read_agent_public_snapshot(conn):
    row = conn.execute(
        "SELECT s.value,s.updated_at,l.holder_id,l.expires_at,l.contender_id "
        "FROM agent_states s LEFT JOIN agent_leases l "
        "ON l.user_id=s.user_id AND l.name=? "
        "WHERE s.key=? ORDER BY s.updated_at DESC,s.version DESC,s.user_id DESC LIMIT 1",
        ("trixagent", "trixagent"),
    ).fetchone()
    if not row:
        return None, None, None
    lease = {
        "holder_id": row["holder_id"],
        "expires_at": row["expires_at"],
        "contender_id": row["contender_id"],
    }
    return row["value"], row["updated_at"], lease


def _state_object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, RecursionError, UnicodeError):
            return {}
    return value if isinstance(value, dict) else {}


def _timestamp(value):
    if isinstance(value, bool) or value is None:
        return None
    number = None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                number = parsed.timestamp()
            except (ValueError, OverflowError):
                return None
    if number is None or not math.isfinite(number) or number < 0:
        return None
    while number >= 100_000_000_000:
        number /= 1000
    return int(number)


def _nonnegative_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        return int(value)
    return None


def _normalized_word(value):
    if not isinstance(value, str):
        return None
    word = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return word or None


def _task_status(value):
    status = _normalized_word(value)
    status = _TASK_STATUS_ALIASES.get(status, status)
    return status if status in _TASK_STATUSES else None


def _runner_status(value):
    status = _normalized_word(value)
    status = _RUNNER_STATUS_ALIASES.get(status, status)
    return status if status in _RUNNER_STATUSES else None


def _safe_model(value):
    if not isinstance(value, str):
        return None
    model = value.strip()
    lowered = model.lower()
    if (
        not _MODEL_RE.fullmatch(model)
        or model.startswith(("/", "~", "."))
        or "\\" in model
        or ".." in model
        or "://" in model
        or re.search(r"(?:api[_ -]?key|access[_ -]?token|bearer|password|secret)", lowered)
        or lowered.startswith(("sk-", "gsk_", "aiza"))
    ):
        return None
    return model


def _records(value, record_fields):
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    if record_fields.intersection(value):
        return [value]
    return [item for item in value.values() if isinstance(item, dict)]


def _runner_is_active(runner):
    for field in ("active", "is_active"):
        if isinstance(runner.get(field), bool):
            return runner[field]
    return _runner_status(runner.get("status")) in _ACTIVE_RUNNER_STATUSES


def _newest_active_runner(state):
    source = state.get("runner_instances")
    if source is None:
        source = state.get("runners")
    if source is None:
        source = state.get("runner")
    newest = None
    newest_key = None
    for index, runner in enumerate(_records(source, _RUNNER_FIELDS)):
        if not _runner_is_active(runner):
            continue
        last_seen = _timestamp(runner.get("last_seen_at"))
        started = _timestamp(runner.get("started_at"))
        key = (
            last_seen if last_seen is not None else -1,
            started if started is not None else -1,
            index,
        )
        if newest_key is None or key > newest_key:
            newest = runner
            newest_key = key
    return newest


def _task_summary(state):
    tasks = _records(state.get("tasks"), _TASK_FIELDS)
    counts = {status: 0 for status in _TASK_STATUSES}
    newest = None
    newest_key = None
    for index, task in enumerate(tasks):
        status = _task_status(task.get("status"))
        if status:
            counts[status] += 1
        updated_at = _timestamp(task.get("updated_at"))
        key = (updated_at if updated_at is not None else -1, index)
        if newest_key is None or key > newest_key:
            newest = task
            newest_key = key

    counts["total"] = len(tasks)
    counts["last_status"] = _task_status(newest.get("status")) if newest else None
    counts["last_iteration"] = (
        _nonnegative_int(newest.get("iteration", newest.get("last_iteration"))) if newest else None
    )
    counts["last_updated_at"] = _timestamp(newest.get("updated_at")) if newest else None
    return counts


def _collection_count(value):
    if isinstance(value, (list, dict)):
        return len(value)
    count = _nonnegative_int(value)
    return count if count is not None else 0


def build_agent_public_status(value, state_updated_at, lease, now, ok=True):
    """Return a fixed, content-free public projection of an agent state value."""
    state = _state_object(value)
    lease = lease if isinstance(lease, dict) else {}
    checked_at = _timestamp(now)
    runner = _newest_active_runner(state)

    holder_id = lease.get("holder_id")
    contender_id = lease.get("contender_id")
    holder_present = isinstance(holder_id, str) and bool(holder_id.strip())
    contender_present = isinstance(contender_id, str) and bool(contender_id.strip())
    expires_at = _timestamp(lease.get("expires_at"))

    runner_status = _runner_status(runner.get("status")) if runner else None
    runner_model = _safe_model(runner.get("model")) if runner else None
    runner_started_at = _timestamp(runner.get("started_at")) if runner else None
    runner_last_seen_at = _timestamp(runner.get("last_seen_at")) if runner else None

    online = False
    if checked_at is not None and runner_last_seen_at is not None:
        runner_age = checked_at - runner_last_seen_at
        online = bool(
            holder_present
            and expires_at is not None
            and expires_at > checked_at
            and 0 <= runner_age <= PUBLIC_STATUS_MAX_AGE
        )

    memory = state.get("memory")
    memory = memory if isinstance(memory, dict) else {}
    messages = memory["messages"] if "messages" in memory else state.get("messages")
    tool_runs = memory["tool_runs"] if "tool_runs" in memory else state.get("tool_runs")

    return {
        "ok": bool(ok),
        "online": online,
        "checked_at": checked_at,
        "state_updated_at": _timestamp(state_updated_at),
        "lease": {
            "holder_present": holder_present,
            "expires_at": expires_at,
            "contender_present": contender_present,
        },
        "runner": {
            "status": runner_status,
            "model": runner_model,
            "started_at": runner_started_at,
            "last_seen_at": runner_last_seen_at,
        },
        "tasks": _task_summary(state),
        "memory": {
            "messages": _collection_count(messages),
            "tool_runs": _collection_count(tool_runs),
        },
        "providers": dict(PROVIDERS),
    }
