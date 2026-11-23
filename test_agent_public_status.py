import json
import sqlite3
import unittest

from agent_public_status import (
    allowed_public_status_origin,
    build_agent_public_status,
    read_agent_public_snapshot,
)
from agent_store import init_agent_schema


class AgentPublicStatusTests(unittest.TestCase):
    def test_projection_does_not_expose_content_or_ids(self):
        state = {
            "runners": {
                "runner-private-id": {
                    "active": True,
                    "status": "running",
                    "model": "openai/gpt-oss-120b",
                    "started_at": 900,
                    "last_seen_at": 990,
                    "prompt": "private prompt marker",
                    "api_key": "private key marker",
                    "path": "/private/path/marker",
                }
            },
            "tasks": [
                {
                    "status": "running",
                    "iteration": 2,
                    "updated_at": 995,
                    "summary": "private summary marker",
                    "tool_output": "private output marker",
                }
            ],
            "memory": {
                "messages": [{"content": "private message marker"}],
                "tool_runs": [{"arguments": "private arguments marker"}],
            },
            "browser_state": {"cookies": "private browser marker"},
            "user": {"email": "private user marker"},
        }
        lease = {
            "holder_id": "holder-private-id",
            "expires_at": 1100,
            "contender_id": "contender-private-id",
        }

        result = build_agent_public_status(state, 996, lease, 1000)

        self.assertEqual(
            set(result),
            {
                "ok",
                "online",
                "checked_at",
                "state_updated_at",
                "lease",
                "runner",
                "tasks",
                "memory",
                "providers",
            },
        )
        self.assertEqual(
            set(result["runner"]), {"status", "model", "started_at", "last_seen_at"}
        )
        self.assertEqual(
            set(result["lease"]), {"holder_present", "expires_at", "contender_present"}
        )
        self.assertTrue(result["lease"]["holder_present"])
        self.assertTrue(result["lease"]["contender_present"])
        rendered = json.dumps(result)
        for private_value in (
            "runner-private-id",
            "holder-private-id",
            "contender-private-id",
            "private prompt marker",
            "private key marker",
            "/private/path/marker",
            "private summary marker",
            "private output marker",
            "private message marker",
            "private arguments marker",
            "private browser marker",
            "private user marker",
        ):
            self.assertNotIn(private_value, rendered)

    def test_online_requires_live_lease_and_recent_newest_active_runner(self):
        state = {
            "runner_instances": [
                {"active": True, "status": "idle", "last_seen_at": 850},
                {
                    "active": True,
                    "status": "running",
                    "started_at": 900,
                    "last_seen_at": 990,
                },
                {"active": False, "status": "stopped", "last_seen_at": 999},
            ]
        }
        lease = {"holder_id": "worker", "expires_at": 1100}

        result = build_agent_public_status(state, 999, lease, 1000)
        self.assertTrue(result["online"])
        self.assertEqual(result["runner"]["last_seen_at"], 990)
        self.assertEqual(result["runner"]["status"], "running")

        stale = build_agent_public_status(
            {"runners": [{"active": True, "last_seen_at": 879}]}, 999, lease, 1000
        )
        expired = build_agent_public_status(state, 999, {**lease, "expires_at": 1000}, 1000)
        holder_missing = build_agent_public_status(
            state, 999, {"holder_id": "", "expires_at": 1100}, 1000
        )
        future_heartbeat = build_agent_public_status(
            {"runners": [{"active": True, "last_seen_at": 1001}]}, 999, lease, 1000
        )
        self.assertFalse(stale["online"])
        self.assertFalse(expired["online"])
        self.assertFalse(holder_missing["online"])
        self.assertFalse(future_heartbeat["online"])

    def test_task_and_memory_counts_use_values_without_returning_them(self):
        state = {
            "tasks": {
                "one": {"status": "waiting", "iteration": 0, "updated_at": 10},
                "two": {"status": "pending", "iteration": 1, "updated_at": 20},
                "three": {"status": "running", "iteration": 2, "updated_at": 30},
                "four": {"status": "waiting-approval", "iteration": 3, "updated_at": 40},
                "five": {"status": "error", "iteration": 4, "updated_at": 60},
                "six": {"status": "done", "iteration": 5, "updated_at": 50},
                "seven": {"status": "cancelled", "iteration": 6, "updated_at": 15},
            },
            "messages": [{"content": "one"}, {"content": "two"}],
            "tool_runs": {"a": {"output": "one"}, "b": {"output": "two"}},
        }

        result = build_agent_public_status(state, 70, None, 100)

        self.assertEqual(
            result["tasks"],
            {
                "waiting": 2,
                "running": 1,
                "waiting_approval": 1,
                "failed": 1,
                "completed": 1,
                "total": 7,
                "last_status": "failed",
                "last_iteration": 4,
                "last_updated_at": 60,
            },
        )
        self.assertEqual(result["memory"], {"messages": 2, "tool_runs": 2})

    def test_malformed_state_is_offline(self):
        result = build_agent_public_status(
            "{not-json", 50, {"holder_id": "worker", "expires_at": 200}, 100
        )
        self.assertFalse(result["online"])
        self.assertEqual(result["tasks"]["total"], 0)
        self.assertEqual(result["memory"], {"messages": 0, "tool_runs": 0})

    def test_snapshot_uses_newest_state_and_its_matching_lease(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        init_agent_schema(db)
        db.executemany(
            "INSERT INTO agent_states(user_id,key,version,value,updated_at) VALUES(?,?,?,?,?)",
            [
                (1, "trixagent", 1, '{"runner":{"active":true}}', 100),
                (2, "trixagent", 1, '{"runner":{"active":true}}', 200),
                (3, "other", 1, "{}", 300),
            ],
        )
        db.executemany(
            "INSERT INTO agent_leases(user_id,name,holder_id,expires_at,updated_at) "
            "VALUES(?,?,?,?,?)",
            [
                (1, "trixagent", "older", 150, 100),
                (2, "trixagent", "newest", 250, 200),
            ],
        )

        value, updated_at, lease = read_agent_public_snapshot(db)

        self.assertEqual(value, '{"runner":{"active":true}}')
        self.assertEqual(updated_at, 200)
        self.assertEqual(lease["holder_id"], "newest")
        self.assertEqual(lease["expires_at"], 250)

    def test_cors_origin_allowlist(self):
        self.assertEqual(
            allowed_public_status_origin("https://trixtom.my.id"), "https://trixtom.my.id"
        )
        self.assertEqual(
            allowed_public_status_origin("http://localhost:3000"), "http://localhost:3000"
        )
        self.assertIsNone(allowed_public_status_origin("https://localhost:3000"))
        self.assertIsNone(allowed_public_status_origin("http://localhost.evil.test:3000"))
        self.assertIsNone(allowed_public_status_origin("https://trixtom.my.id.evil.test"))


if __name__ == "__main__":
    unittest.main()
