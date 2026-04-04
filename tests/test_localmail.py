"""Tests for localmail.py — async agent-to-agent messaging adapter."""

import json
import os
import time
import pytest
from pathlib import Path

import localmail


class TestSendMail:
    def test_creates_file_in_inbox(self, hub_dir):
        msg_id = localmail.send_mail("Jr", "Q", "hello from Jr")
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        files = list(inbox.glob("*.json"))
        assert len(files) == 1
        with open(files[0]) as f:
            data = json.load(f)
        assert data["from"] == "Jr"
        assert data["to"] == "Q"
        assert data["text"] == "hello from Jr"
        assert data["id"] == msg_id

    def test_default_priority(self, hub_dir):
        localmail.send_mail("Jr", "Q", "hello")
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        files = list(inbox.glob("*.json"))
        with open(files[0]) as f:
            data = json.load(f)
        assert data["priority"] == 3

    def test_custom_priority(self, hub_dir):
        localmail.send_mail("Jr", "Q", "urgent!", priority=1)
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        files = list(inbox.glob("*.json"))
        with open(files[0]) as f:
            data = json.load(f)
        assert data["priority"] == 1

    def test_meta_field(self, hub_dir):
        localmail.send_mail("Jr", "Q", "hello", meta={"thread": "demo-prep"})
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        files = list(inbox.glob("*.json"))
        with open(files[0]) as f:
            data = json.load(f)
        assert data["meta"]["thread"] == "demo-prep"

    def test_creates_dir_for_new_agent(self, hub_dir):
        localmail.send_mail("Jr", "NewAgent", "hello")
        inbox = hub_dir.parent / "agents" / "NewAgent" / "asdaaas" / "adapters" / "localmail" / "inbox"
        assert inbox.exists()
        assert len(list(inbox.glob("*.json"))) == 1

    def test_multiple_messages(self, hub_dir):
        for i in range(5):
            localmail.send_mail("Jr", "Q", f"message {i}")
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        assert len(list(inbox.glob("*.json"))) == 5

    def test_has_timestamp(self, hub_dir):
        localmail.send_mail("Jr", "Q", "hello")
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        files = list(inbox.glob("*.json"))
        with open(files[0]) as f:
            data = json.load(f)
        assert "ts" in data

    def test_atomic_write(self, hub_dir):
        localmail.send_mail("Jr", "Q", "hello")
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        assert len(list(inbox.glob("*.tmp"))) == 0


class TestReadMail:
    def test_reads_and_deletes(self, hub_dir):
        localmail.send_mail("Jr", "Q", "hello")
        messages = localmail.read_mail("Q")
        assert len(messages) == 1
        assert messages[0]["text"] == "hello"
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        assert len(list(inbox.glob("*.json"))) == 0

    def test_chronological_order(self, hub_dir):
        for i in range(3):
            localmail.send_mail("Jr", "Q", f"msg {i}")
        messages = localmail.read_mail("Q")
        texts = [m["text"] for m in messages]
        assert texts == ["msg 0", "msg 1", "msg 2"]

    def test_empty_inbox(self, hub_dir):
        messages = localmail.read_mail("Q")
        assert messages == []

    def test_nonexistent_agent(self, hub_dir):
        messages = localmail.read_mail("NonexistentAgent")
        assert messages == []

    def test_skips_corrupt_json(self, hub_dir):
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        with open(inbox / "bad_001.json", "w") as f:
            f.write("not json{{{")
        localmail.send_mail("Jr", "Q", "good message")
        messages = localmail.read_mail("Q")
        assert len(messages) == 1
        assert messages[0]["text"] == "good message"


class TestPeekMail:
    def test_reads_without_deleting(self, hub_dir):
        localmail.send_mail("Jr", "Q", "hello")
        messages = localmail.peek_mail("Q")
        assert len(messages) == 1
        # Should still be there
        messages2 = localmail.peek_mail("Q")
        assert len(messages2) == 1

    def test_empty_inbox(self, hub_dir):
        messages = localmail.peek_mail("Q")
        assert messages == []


class TestRingDoorbell:
    def test_creates_doorbell_file(self, hub_dir):
        msg = {"id": "test-001", "from": "Jr", "to": "Q", "text": "hello", "priority": 3}
        localmail.ring_doorbell("Q", msg)
        bell_dir = hub_dir.parent / "agents" / "Q" / "asdaaas" / "doorbells"
        files = list(bell_dir.glob("*.json"))
        assert len(files) == 1
        with open(files[0]) as f:
            data = json.load(f)
        assert data["adapter"] == "localmail"
        assert data["priority"] == 3
        assert "Jr" in data["text"]
        assert "hello" in data["text"]

    def test_inline_content_short_message(self, hub_dir):
        msg = {"id": "test-002", "from": "Sr", "to": "Trip", "text": "short msg", "priority": 5}
        localmail.ring_doorbell("Trip", msg)
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        files = list(bell_dir.glob("*.json"))
        with open(files[0]) as f:
            data = json.load(f)
        assert data["text"] == "[localmail] Mail from Sr:\nshort msg"

    def test_truncates_long_message(self, hub_dir):
        long_text = "x" * 600
        msg = {"id": "test-003", "from": "Jr", "to": "Q", "text": long_text, "priority": 3}
        localmail.ring_doorbell("Q", msg)
        bell_dir = hub_dir.parent / "agents" / "Q" / "asdaaas" / "doorbells"
        files = list(bell_dir.glob("*.json"))
        with open(files[0]) as f:
            data = json.load(f)
        assert "Full message:" in data["text"]
        assert len(data["text"]) < 700  # preview + path reference
        # Payload file should exist with full content
        payload_dir = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "payloads"
        payload_files = list(payload_dir.glob("*.json"))
        assert len(payload_files) == 1
        with open(payload_files[0]) as f:
            payload = json.load(f)
        assert payload["text"] == long_text

    def test_preserves_priority(self, hub_dir):
        msg = {"id": "test-004", "from": "Jr", "to": "Q", "text": "urgent", "priority": 1}
        localmail.ring_doorbell("Q", msg)
        bell_dir = hub_dir.parent / "agents" / "Q" / "asdaaas" / "doorbells"
        files = list(bell_dir.glob("*.json"))
        with open(files[0]) as f:
            data = json.load(f)
        assert data["priority"] == 1

    def test_atomic_write(self, hub_dir):
        msg = {"id": "test-005", "from": "Jr", "to": "Q", "text": "hello", "priority": 3}
        localmail.ring_doorbell("Q", msg)
        bell_dir = hub_dir.parent / "agents" / "Q" / "asdaaas" / "doorbells"
        assert len(list(bell_dir.glob("*.tmp"))) == 0


class TestGetAsdaaasAgents:
    def test_detects_healthy_agents(self, hub_dir, write_health):
        write_health("Trip")
        write_health("Q")
        agents = localmail.get_asdaaas_agents()
        assert "Trip" in agents
        assert "Q" in agents

    def test_excludes_stale_agents(self, hub_dir, write_health):
        write_health("Trip")
        # Make Trip's health file old
        health_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "health.json"
        old_time = time.time() - 7200  # 2 hours ago
        os.utime(health_file, (old_time, old_time))
        agents = localmail.get_asdaaas_agents()
        assert "Trip" not in agents

    def test_excludes_error_status(self, hub_dir):
        health_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "health.json"
        with open(health_file, "w") as f:
            json.dump({"agent": "Trip", "status": "error"}, f)
        agents = localmail.get_asdaaas_agents()
        assert "Trip" not in agents

    def test_empty_health_dir(self, hub_dir):
        agents = localmail.get_asdaaas_agents()
        assert agents == set()


class TestRoundTrip:
    """End-to-end tests for the localmail message flow."""

    def test_send_then_read(self, hub_dir):
        localmail.send_mail("Jr", "Q", "How's the demo prep?")
        messages = localmail.read_mail("Q")
        assert len(messages) == 1
        assert messages[0]["from"] == "Jr"
        assert messages[0]["text"] == "How's the demo prep?"

    def test_bidirectional(self, hub_dir):
        localmail.send_mail("Jr", "Q", "Status?")
        localmail.send_mail("Q", "Jr", "All good, 14/14 tests pass")
        
        q_mail = localmail.read_mail("Q")
        jr_mail = localmail.read_mail("Jr")
        
        assert len(q_mail) == 1
        assert q_mail[0]["from"] == "Jr"
        assert len(jr_mail) == 1
        assert jr_mail[0]["from"] == "Q"

    def test_send_and_doorbell(self, hub_dir, write_health):
        write_health("Q")
        localmail.send_mail("Jr", "Q", "Check this out")
        
        # Simulate what the watcher does
        inbox = hub_dir.parent / "agents" / "Q" / "asdaaas" / "adapters" / "localmail" / "inbox"
        files = sorted(inbox.glob("*.json"))
        assert len(files) == 1
        
        with open(files[0]) as f:
            msg = json.load(f)
        
        asdaaas_agents = localmail.get_asdaaas_agents()
        assert "Q" in asdaaas_agents
        
        localmail.ring_doorbell("Q", msg)
        files[0].unlink()
        
        # Verify doorbell
        bell_dir = hub_dir.parent / "agents" / "Q" / "asdaaas" / "doorbells"
        bells = list(bell_dir.glob("*.json"))
        assert len(bells) == 1
        with open(bells[0]) as f:
            bell = json.load(f)
        assert bell["adapter"] == "localmail"
        assert "Jr" in bell["text"]
        assert "Check this out" in bell["text"]

    def test_tui_agent_message_stays(self, hub_dir):
        """TUI agents (no health file) should have messages stay in inbox."""
        localmail.send_mail("Q", "Jr", "Response to your question")
        
        asdaaas_agents = localmail.get_asdaaas_agents()
        assert "Jr" not in asdaaas_agents
        
        # Message should still be readable
        messages = localmail.read_mail("Jr")
        assert len(messages) == 1
        assert messages[0]["text"] == "Response to your question"
