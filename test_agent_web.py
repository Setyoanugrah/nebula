import json
import sqlite3
import unittest

import agent_web
from agent_store import init_agent_schema


def empty_state():
    return {
        "schema_version": 1,
        "kv": {},
        "telegram_updates": [],
        "messages": [],
        "tasks": [],
        "tool_runs": [],
        "approvals": [],
        "runner_instances": [],
    }


def insert_state(conn, value, version=1, updated_at=100, user_id=7):
    conn.execute(
        "INSERT OR REPLACE INTO agent_states(user_id,key,version,value,updated_at) VALUES(?,?,?,?,?)",
        (user_id, "trixagent", version, json.dumps(value), updated_at),
    )
    conn.commit()


class AgentWebTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        init_agent_schema(self.conn)
        insert_state(self.conn, empty_state())

    def tearDown(self):
        self.conn.close()

    def test_task_creation_is_atomic_and_projected_safely(self):
        task = agent_web.create_web_task(self.conn, "hello world", now=200)
        self.assertEqual(task["status"], "waiting")
        state = agent_web.read_newest_agent_state(self.conn)
        self.assertEqual(state["version"], 2)
        self.assertEqual(len(state["value"]["tasks"]), 1)
        self.assertEqual(len(state["value"]["messages"]), 1)
        self.assertEqual(state["value"]["tasks"][0]["checkpoint_json"], '{"messages":[],"pending":[]}')
        self.assertNotIn("checkpoint_json", task)

        conversation = agent_web.read_web_conversation(self.conn)
        self.assertEqual(conversation["messages"][0]["content"], "hello world")
        self.assertEqual(conversation["messages"][0]["role"], "user")
        self.assertEqual(conversation["tasks"][0]["id"], task["id"])

    def test_projection_hides_telegram_and_internal_content(self):
        state = empty_state()
        state["messages"] = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "chat_id": "web:owner",
                "task_id": "22222222-2222-2222-2222-222222222222",
                "source_update_id": None,
                "telegram_message_id": None,
                "role": "assistant",
                "kind": "final",
                "content": "public answer",
                "created_at": "2026-01-01T00:00:00.000Z",
            },
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "chat_id": "5547953802",
                "task_id": "44444444-4444-4444-4444-444444444444",
                "source_update_id": 9,
                "telegram_message_id": 9,
                "role": "user",
                "kind": "text",
                "content": "telegram secret",
                "created_at": "2026-01-01T00:00:01.000Z",
            },
            {
                "id": "55555555-5555-5555-5555-555555555555",
                "chat_id": "web:owner",
                "task_id": "22222222-2222-2222-2222-222222222222",
                "source_update_id": None,
                "telegram_message_id": None,
                "role": "tool",
                "kind": "tool",
                "content": "tool internals",
                "created_at": "2026-01-01T00:00:02.000Z",
            },
        ]
        insert_state(self.conn, state)
        payload = agent_web.read_web_conversation(self.conn)
        self.assertEqual([item["content"] for item in payload["messages"]], ["public answer"])
        encoded = json.dumps(payload)
        self.assertNotIn("telegram secret", encoded)
        self.assertNotIn("tool internals", encoded)

    def test_cancel_rejects_pending_approval(self):
        task = agent_web.create_web_task(self.conn, "run this", now=200)
        state = agent_web.read_newest_agent_state(self.conn)
        state["value"]["approvals"].append(
            {
                "id": "66666666-6666-6666-6666-666666666666",
                "task_id": task["id"],
                "tool_run_id": "77777777-7777-7777-7777-777777777777",
                "chat_id": "5547953802",
                "reason": "risky",
                "status": "pending",
                "nonce_hash": "00",
                "requested_at": "2026-01-01T00:00:00.000Z",
                "decided_at": None,
                "telegram_message_id": None,
            }
        )
        insert_state(self.conn, state["value"], version=state["version"])

        cancelled = agent_web.cancel_web_task_db(self.conn, task["id"], now=300)
        self.assertEqual(cancelled["status"], "cancelled")
        after = agent_web.read_newest_agent_state(self.conn)["value"]
        self.assertEqual(after["approvals"][0]["status"], "rejected")

    def test_new_session_filters_old_messages_and_tasks(self):
        first = agent_web.create_web_task(self.conn, "old question", now=200)
        agent_web.reset_web_session_db(self.conn, now=300)
        second = agent_web.create_web_task(self.conn, "new question", now=400)

        conversation = agent_web.read_web_conversation(self.conn)
        self.assertEqual([item["content"] for item in conversation["messages"]], ["new question"])
        self.assertNotIn(first["id"], [item["id"] for item in conversation["tasks"]])
        self.assertIn(second["id"], [item["id"] for item in conversation["tasks"]])

    def test_open_task_limit(self):
        for index in range(agent_web.MAX_OPEN_TASKS):
            agent_web.create_web_task(self.conn, f"task {index}", now=200 + index)
        with self.assertRaises(agent_web.WebStateError) as error:
            agent_web.create_web_task(self.conn, "one too many", now=500)
        self.assertEqual(error.exception.status_code, 429)

    def test_invalid_content_is_rejected(self):
        with self.assertRaises(agent_web.WebStateError):
            agent_web.clean_message("   ")
        with self.assertRaises(agent_web.WebStateError):
            agent_web.clean_message("x" * (agent_web.MAX_MESSAGE_CHARS + 1))

    def test_token_comparison(self):
        self.assertTrue(agent_web.authorized_web_token("secret-token", "secret-token"))
        self.assertFalse(agent_web.authorized_web_token("secret-token", "other"))
        self.assertFalse(agent_web.authorized_web_token("", "secret-token"))
        self.assertFalse(agent_web.authorized_web_token("secret-token", ""))

    def test_cors_origin(self):
        self.assertEqual(agent_web.allowed_web_origin("https://trixtom.my.id"), "https://trixtom.my.id")
        self.assertEqual(agent_web.allowed_web_origin("http://localhost:8080"), "http://localhost:8080")
        self.assertIsNone(agent_web.allowed_web_origin("https://evil.example"))


if __name__ == "__main__":
    unittest.main()
