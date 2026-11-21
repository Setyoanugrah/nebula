import sqlite3
import tempfile
import unittest

from agent_store import (
    AgentLeaseConflict,
    AgentStateConflict,
    AgentValueTooLarge,
    MAX_AGENT_VALUE_BYTES,
    acquire_agent_lease,
    get_agent_lease,
    get_agent_state,
    init_agent_schema,
    put_agent_state,
    release_agent_lease,
    renew_agent_lease,
)


class AgentStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(self.tempdir.name + "/agent.db")
        self.db.row_factory = sqlite3.Row
        init_agent_schema(self.db)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_state_cas_and_user_isolation(self):
        self.assertIsNone(get_agent_state(self.db, 1, "cursor"))
        created = put_agent_state(self.db, 1, "cursor", None, {"offset": 3}, 100)
        self.assertEqual(created, {
            "key": "cursor", "version": 1, "value": {"offset": 3}, "updated_at": 100
        })

        with self.assertRaises(AgentStateConflict) as conflict:
            put_agent_state(self.db, 1, "cursor", None, {"offset": 4}, 101)
        self.assertEqual(conflict.exception.current_version, 1)

        updated = put_agent_state(self.db, 1, "cursor", 1, ["next"], 102)
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["value"], ["next"])
        self.assertIsNone(get_agent_state(self.db, 2, "cursor"))
        other = put_agent_state(self.db, 2, "cursor", None, True, 103)
        self.assertEqual(other["version"], 1)

        raw = self.db.execute(
            "SELECT value FROM agent_states WHERE user_id=? AND key=?", (1, "cursor")
        ).fetchone()["value"]
        self.assertIsInstance(raw, str)

    def test_state_serialized_value_limit(self):
        value = "x" * (MAX_AGENT_VALUE_BYTES - 2)
        created = put_agent_state(self.db, 1, "large", None, value, 100)
        self.assertEqual(created["value"], value)
        with self.assertRaises(AgentValueTooLarge):
            put_agent_state(self.db, 1, "too-large", None, value + "x", 100)

    def test_state_create_conflict_reports_missing_version(self):
        with self.assertRaises(AgentStateConflict) as conflict:
            put_agent_state(self.db, 1, "new", 1, None, 100)
        self.assertIsNone(conflict.exception.current_version)
        self.assertIsNone(get_agent_state(self.db, 1, "new"))

    def test_lease_lifecycle_contention_and_expiry(self):
        acquired, lease = acquire_agent_lease(self.db, 1, "worker", "alpha", 60, 100)
        self.assertTrue(acquired)
        self.assertEqual(lease["expires_at"], 160)

        acquired, lease = acquire_agent_lease(self.db, 1, "worker", "beta", 60, 110)
        self.assertFalse(acquired)
        self.assertEqual(lease["holder_id"], "alpha")
        self.assertEqual(lease["contender_id"], "beta")
        self.assertEqual(lease["contender_seen_at"], 110)

        with self.assertRaises(AgentLeaseConflict):
            renew_agent_lease(self.db, 1, "worker", "beta", 60, 120)
        renewed = renew_agent_lease(self.db, 1, "worker", "alpha", 90, 120)
        self.assertEqual(renewed["expires_at"], 210)

        with self.assertRaises(AgentLeaseConflict):
            release_agent_lease(self.db, 1, "worker", "beta", 130)
        released = release_agent_lease(self.db, 1, "worker", "alpha", 130)
        self.assertIsNone(released["holder_id"])
        self.assertEqual(released["expires_at"], 130)

        acquired, lease = acquire_agent_lease(self.db, 1, "worker", "beta", 30, 130)
        self.assertTrue(acquired)
        self.assertEqual(lease["holder_id"], "beta")
        acquired, lease = acquire_agent_lease(self.db, 1, "worker", "gamma", 30, 161)
        self.assertTrue(acquired)
        self.assertEqual(lease["holder_id"], "gamma")

        self.assertIsNone(get_agent_lease(self.db, 2, "worker"))
        acquired, lease = acquire_agent_lease(self.db, 2, "worker", "other", 30, 100)
        self.assertTrue(acquired)
        self.assertEqual(lease["holder_id"], "other")


if __name__ == "__main__":
    unittest.main()
