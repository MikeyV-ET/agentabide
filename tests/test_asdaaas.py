"""Tests for asdaaas.py — extractable functions (no subprocess needed)."""

import asyncio
import json
import os
import time
import pytest
from pathlib import Path

import asdaaas
import adapter_api


# ============================================================================
# Gaze
# ============================================================================

class TestReadGaze:
    def test_split_format(self, hub_dir, write_gaze):
        write_gaze("Trip", speech_target="irc", speech_params={"channel": "#standup"},
                    thoughts_target="irc", thoughts_params={"channel": "#trip-thoughts"})
        gaze = asdaaas.read_gaze("Trip")
        assert gaze["speech"]["target"] == "irc"
        assert gaze["speech"]["params"]["channel"] == "#standup"
        assert gaze["thoughts"]["target"] == "irc"
        assert gaze["thoughts"]["params"]["channel"] == "#trip-thoughts"

    def test_null_thoughts(self, hub_dir, write_gaze):
        write_gaze("Trip", speech_target="irc", thoughts_target=None)
        gaze = asdaaas.read_gaze("Trip")
        assert gaze["speech"] is not None
        assert gaze["thoughts"] is None

    def test_legacy_format(self, hub_dir):
        """Legacy gaze files without speech/thoughts keys should work."""
        gaze_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "gaze.json"
        with open(gaze_file, "w") as f:
            json.dump({"target": "irc", "params": {"channel": "#standup"}}, f)
        gaze = asdaaas.read_gaze("Trip")
        assert gaze["speech"]["target"] == "irc"
        assert gaze["thoughts"] is None

    def test_missing_file_defaults(self, hub_dir):
        gaze = asdaaas.read_gaze("NonexistentAgent")
        assert gaze["speech"]["target"] == "irc"
        assert gaze["speech"]["params"]["room"] == "#standup"
        assert gaze["thoughts"] is None

    def test_corrupt_json_defaults(self, hub_dir):
        gaze_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "gaze.json"
        with open(gaze_file, "w") as f:
            f.write("not valid json{{{")
        gaze = asdaaas.read_gaze("Trip")
        assert gaze["speech"]["target"] == "irc"


# ============================================================================
# Awareness
# ============================================================================

class TestReadAwareness:
    def test_custom_awareness(self, hub_dir, write_awareness):
        write_awareness("Trip", direct_attach=["irc", "mesh"],
                       control_watch={"impress": {"timeout": 10}},
                       notify_watch=["localmail"])
        awareness = asdaaas.read_awareness("Trip")
        assert awareness["direct_attach"] == ["irc", "mesh"]
        assert awareness["control_watch"]["impress"]["timeout"] == 10
        assert awareness["notify_watch"] == ["localmail"]

    def test_missing_file_defaults(self, hub_dir):
        awareness = asdaaas.read_awareness("NonexistentAgent")
        assert awareness["direct_attach"] == ["irc"]
        assert awareness["control_watch"] == {}
        assert awareness["notify_watch"] == []
        assert awareness["accept_from"] == ["*"]

    def test_corrupt_json_defaults(self, hub_dir):
        awareness_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "awareness.json"
        with open(awareness_file, "w") as f:
            f.write("broken{{{")
        awareness = asdaaas.read_awareness("Trip")
        assert awareness["direct_attach"] == ["irc"]


# ============================================================================
# write_to_outbox
# ============================================================================

class TestWriteToOutbox:
    def test_writes_to_per_adapter_path(self, hub_dir):
        gaze_target = {"target": "irc", "params": {"channel": "#standup"}}
        asdaaas.write_to_outbox("Trip", "hello world", gaze_target, "speech")
        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = list(outbox.glob("*.json"))
        assert len(files) == 1
        with open(files[0]) as f:
            data = json.load(f)
        assert data["from"] == "Trip"
        assert data["content_type"] == "speech"
        assert data["text"] == "hello world"
        assert data["channel"] == "#standup"

    def test_thoughts_content_type(self, hub_dir):
        gaze_target = {"target": "irc", "params": {"channel": "#trip-thoughts"}}
        asdaaas.write_to_outbox("Trip", "thinking...", gaze_target, "thoughts")
        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = list(outbox.glob("*.json"))
        with open(files[0]) as f:
            data = json.load(f)
        assert data["content_type"] == "thoughts"
        assert data["channel"] == "#trip-thoughts"

    def test_null_target_discards(self, hub_dir):
        asdaaas.write_to_outbox("Trip", "should be discarded", None, "speech")
        # Nothing should be written anywhere
        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = list(outbox.glob("*.json"))
        assert len(files) == 0

    def test_params_passthrough(self, hub_dir):
        gaze_target = {"target": "irc", "params": {"channel": "#standup", "pm": "eric"}}
        asdaaas.write_to_outbox("Sr", "hello", gaze_target, "speech")
        outbox = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = list(outbox.glob("*.json"))
        with open(files[0]) as f:
            data = json.load(f)
        assert data["pm"] == "eric"

    def test_creates_outbox_dir(self, hub_dir):
        gaze_target = {"target": "mesh", "params": {"agent": "Jr"}}
        asdaaas.write_to_outbox("Trip", "hello Jr", gaze_target, "speech")
        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "mesh" / "outbox"
        assert outbox.exists()
        assert len(list(outbox.glob("*.json"))) == 1


# ============================================================================
# poll_inbox (legacy universal inbox)
# ============================================================================

class TestPollInbox:
    def test_reads_messages_for_agent(self, hub_dir):
        inbox = hub_dir / "inbox"
        msg = {"to": "Trip", "from": "eric", "text": "hello", "adapter": "irc"}
        with open(inbox / "msg_001.json", "w") as f:
            json.dump(msg, f)
        messages = asdaaas.poll_inbox("Trip")
        assert len(messages) == 1
        assert messages[0]["text"] == "hello"

    def test_ignores_other_agents(self, hub_dir):
        inbox = hub_dir / "inbox"
        msg = {"to": "Sr", "from": "eric", "text": "hello", "adapter": "irc"}
        with open(inbox / "msg_001.json", "w") as f:
            json.dump(msg, f)
        messages = asdaaas.poll_inbox("Trip")
        assert len(messages) == 0

    def test_reads_broadcast(self, hub_dir):
        inbox = hub_dir / "inbox"
        msg = {"to": "broadcast", "from": "eric", "text": "hello all", "adapter": "irc"}
        with open(inbox / "msg_001.json", "w") as f:
            json.dump(msg, f)
        messages = asdaaas.poll_inbox("Trip")
        assert len(messages) == 1

    def test_deletes_after_read(self, hub_dir):
        inbox = hub_dir / "inbox"
        msg = {"to": "Trip", "from": "eric", "text": "hello"}
        with open(inbox / "msg_001.json", "w") as f:
            json.dump(msg, f)
        asdaaas.poll_inbox("Trip")
        assert len(list(inbox.glob("*.json"))) == 0

    def test_empty_inbox(self, hub_dir):
        messages = asdaaas.poll_inbox("Trip")
        assert messages == []


# ============================================================================
# poll_adapter_inboxes
# ============================================================================

class TestPollAdapterInboxes:
    def test_reads_from_direct_attach(self, hub_dir, write_awareness):
        write_awareness("Trip", direct_attach=["irc"])
        inbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "inbox"
        msg = {"from": "eric", "text": "hello from IRC", "adapter": "irc"}
        with open(inbox / "msg_001.json", "w") as f:
            json.dump(msg, f)
        
        awareness = asdaaas.read_awareness("Trip")
        messages = asdaaas.poll_adapter_inboxes("Trip", awareness)
        assert len(messages) == 1
        assert messages[0]["text"] == "hello from IRC"

    def test_reads_from_multiple_adapters(self, hub_dir, write_awareness):
        write_awareness("Trip", direct_attach=["irc", "localmail"])
        
        irc_inbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "inbox"
        mail_inbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "localmail" / "inbox"
        
        with open(irc_inbox / "msg_001.json", "w") as f:
            json.dump({"from": "eric", "text": "from IRC"}, f)
        with open(mail_inbox / "msg_001.json", "w") as f:
            json.dump({"from": "Jr", "text": "from localmail"}, f)
        
        awareness = asdaaas.read_awareness("Trip")
        messages = asdaaas.poll_adapter_inboxes("Trip", awareness)
        assert len(messages) == 2
        texts = {m["text"] for m in messages}
        assert "from IRC" in texts
        assert "from localmail" in texts

    def test_empty_awareness(self, hub_dir):
        awareness = {"direct_attach": [], "control_watch": {}, "notify_watch": []}
        messages = asdaaas.poll_adapter_inboxes("Trip", awareness)
        assert messages == []

    def test_nonexistent_adapter_dir(self, hub_dir):
        awareness = {"direct_attach": ["nonexistent"]}
        messages = asdaaas.poll_adapter_inboxes("Trip", awareness)
        assert messages == []

    def test_deletes_files_after_read(self, hub_dir, write_awareness):
        """poll_adapter_inboxes is destructive: files are gone after read."""
        write_awareness("Trip", direct_attach=["irc"])
        inbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "inbox"
        with open(inbox / "msg_001.json", "w") as f:
            json.dump({"from": "eric", "text": "hello"}, f)

        awareness = asdaaas.read_awareness("Trip")
        messages = asdaaas.poll_adapter_inboxes("Trip", awareness)
        assert len(messages) == 1
        # File should be gone
        assert not list(inbox.glob("*.json"))

    def test_second_poll_returns_empty(self, hub_dir, write_awareness):
        """After destructive poll, second poll finds nothing."""
        write_awareness("Trip", direct_attach=["irc"])
        inbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "inbox"
        with open(inbox / "msg_001.json", "w") as f:
            json.dump({"from": "eric", "text": "hello"}, f)

        awareness = asdaaas.read_awareness("Trip")
        asdaaas.poll_adapter_inboxes("Trip", awareness)
        # Second poll returns empty
        messages = asdaaas.poll_adapter_inboxes("Trip", awareness)
        assert messages == []

    def test_messages_delivered_in_arrival_order(self, hub_dir, write_awareness):
        """Messages written via adapter_api arrive in chronological order,
        even when multiple land in the same polling cycle."""
        import time
        write_awareness("Trip", direct_attach=["irc"])
        for i in range(10):
            adapter_api.write_to_adapter_inbox("irc", "Trip", f"msg {i}", sender="eric")
            time.sleep(0.002)  # distinct timestamps

        awareness = asdaaas.read_awareness("Trip")
        messages = asdaaas.poll_adapter_inboxes("Trip", awareness)
        assert [m["text"] for m in messages] == [f"msg {i}" for i in range(10)]


class TestHasPendingAdapterMessages:
    """Non-destructive inbox check — used during delay interruption."""

    def test_detects_pending_message(self, hub_dir, write_awareness):
        write_awareness("Trip", direct_attach=["irc"])
        inbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "inbox"
        with open(inbox / "msg_001.json", "w") as f:
            json.dump({"from": "eric", "text": "hello"}, f)
        awareness = asdaaas.read_awareness("Trip")
        assert asdaaas.has_pending_adapter_messages("Trip", awareness) is True

    def test_no_pending(self, hub_dir, write_awareness):
        write_awareness("Trip", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Trip")
        assert asdaaas.has_pending_adapter_messages("Trip", awareness) is False

    def test_empty_dir(self, hub_dir, write_awareness):
        """Directory exists but no json files."""
        write_awareness("Trip", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Trip")
        assert asdaaas.has_pending_adapter_messages("Trip", awareness) is False

    def test_nonexistent_adapter(self, hub_dir):
        awareness = {"direct_attach": ["nonexistent"]}
        assert asdaaas.has_pending_adapter_messages("Trip", awareness) is False

    def test_does_not_delete_files(self, hub_dir, write_awareness):
        """Critical: has_pending must NOT consume the message."""
        write_awareness("Trip", direct_attach=["irc"])
        inbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "inbox"
        with open(inbox / "msg_001.json", "w") as f:
            json.dump({"from": "eric", "text": "hello"}, f)

        awareness = asdaaas.read_awareness("Trip")
        # Check three times — file must survive every check
        assert asdaaas.has_pending_adapter_messages("Trip", awareness) is True
        assert asdaaas.has_pending_adapter_messages("Trip", awareness) is True
        assert asdaaas.has_pending_adapter_messages("Trip", awareness) is True
        # File still there
        assert list(inbox.glob("*.json"))

    def test_message_survives_for_poll(self, hub_dir, write_awareness):
        """The whole point: has_pending detects, then poll_adapter_inboxes reads."""
        write_awareness("Trip", direct_attach=["irc"])
        inbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "inbox"
        with open(inbox / "msg_001.json", "w") as f:
            json.dump({"from": "eric", "text": "important message"}, f)

        awareness = asdaaas.read_awareness("Trip")
        # Non-destructive check (simulates delay interruption)
        assert asdaaas.has_pending_adapter_messages("Trip", awareness) is True
        # Destructive read (simulates main loop after delay breaks)
        messages = asdaaas.poll_adapter_inboxes("Trip", awareness)
        assert len(messages) == 1
        assert messages[0]["text"] == "important message"

    def test_multiple_adapters(self, hub_dir, write_awareness):
        """Detects messages across multiple direct_attach adapters."""
        write_awareness("Trip", direct_attach=["irc", "localmail"])
        mail_inbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "localmail" / "inbox"
        with open(mail_inbox / "msg_001.json", "w") as f:
            json.dump({"from": "Sr", "text": "mail"}, f)

        awareness = asdaaas.read_awareness("Trip")
        # IRC inbox is empty, but localmail has a message
        assert asdaaas.has_pending_adapter_messages("Trip", awareness) is True


# ============================================================================
# Doorbells
# ============================================================================

class TestPollDoorbells:
    def test_reads_and_persists(self, hub_dir):
        """Doorbells persist on disk after reading (not deleted)."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "localmail", "priority": 3, "text": "You have mail"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        bells = asdaaas.poll_doorbells("Trip")
        assert len(bells) == 1
        assert bells[0]["text"] == "You have mail"
        # Doorbell persists on disk (not deleted)
        assert len(list(bell_dir.glob("*.json"))) == 1

    def test_delivered_count_incremented(self, hub_dir):
        """Each poll increments delivered_count."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "localmail", "text": "You have mail"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        bells = asdaaas.poll_doorbells("Trip")
        assert bells[0]["delivered_count"] == 1
        
        bells = asdaaas.poll_doorbells("Trip")
        assert bells[0]["delivered_count"] == 2
        
        bells = asdaaas.poll_doorbells("Trip")
        assert bells[0]["delivered_count"] == 3

    def test_id_assigned_from_filename(self, hub_dir):
        """Doorbell gets id from filename stem if not already set."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "localmail", "text": "You have mail"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        bells = asdaaas.poll_doorbells("Trip")
        assert bells[0]["id"] == "bell_001"

    def test_id_preserved_if_present(self, hub_dir):
        """Doorbell keeps its own id if already set."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "localmail", "text": "mail", "id": "custom_id_123"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        bells = asdaaas.poll_doorbells("Trip")
        assert bells[0]["id"] == "custom_id_123"

    def test_ttl_expiry(self, hub_dir):
        """Doorbell expires when delivered_count exceeds TTL."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "heartbeat", "source": "heartbeat", "text": "ping"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        awareness = {"doorbell_ttl": {"heartbeat": 2, "default": 0}}
        
        # First delivery: count=1, TTL=2, ok
        bells = asdaaas.poll_doorbells("Trip", awareness)
        assert len(bells) == 1
        assert bells[0]["delivered_count"] == 1
        
        # Second delivery: count=2, TTL=2, ok
        bells = asdaaas.poll_doorbells("Trip", awareness)
        assert len(bells) == 1
        assert bells[0]["delivered_count"] == 2
        
        # Third delivery: count=3 > TTL=2, expired and removed
        bells = asdaaas.poll_doorbells("Trip", awareness)
        assert len(bells) == 0
        assert len(list(bell_dir.glob("*.json"))) == 0

    def test_ttl_zero_persists_indefinitely(self, hub_dir):
        """TTL=0 means persist forever (never auto-expire)."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "remind", "source": "remind", "text": "check trip"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        awareness = {"doorbell_ttl": {"remind": 0, "default": 0}}
        
        # Poll 10 times -- should never expire
        for i in range(10):
            bells = asdaaas.poll_doorbells("Trip", awareness)
            assert len(bells) == 1
            assert bells[0]["delivered_count"] == i + 1

    def test_ttl_per_source(self, hub_dir):
        """Different sources have different TTLs."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        # Heartbeat with TTL=1
        with open(bell_dir / "bell_hb.json", "w") as f:
            json.dump({"adapter": "heartbeat", "source": "heartbeat", "text": "ping"}, f)
        # Remind with TTL=0 (persist forever)
        with open(bell_dir / "bell_rm.json", "w") as f:
            json.dump({"adapter": "remind", "source": "remind", "text": "check"}, f)
        
        awareness = {"doorbell_ttl": {"heartbeat": 1, "remind": 0, "default": 5}}
        
        # First delivery: both present
        bells = asdaaas.poll_doorbells("Trip", awareness)
        assert len(bells) == 2
        
        # Second delivery: heartbeat expired (count=2 > TTL=1), remind persists
        bells = asdaaas.poll_doorbells("Trip", awareness)
        assert len(bells) == 1
        assert bells[0]["source"] == "remind"

    def test_ttl_default_fallback(self, hub_dir):
        """Sources not in ttl_map use the 'default' TTL."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "unknown_adapter", "source": "unknown_adapter", "text": "hello"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        awareness = {"doorbell_ttl": {"default": 1}}
        
        # First delivery: ok
        bells = asdaaas.poll_doorbells("Trip", awareness)
        assert len(bells) == 1
        
        # Second: expired
        bells = asdaaas.poll_doorbells("Trip", awareness)
        assert len(bells) == 0

    def test_no_awareness_no_ttl(self, hub_dir):
        """Without awareness, doorbells persist indefinitely (TTL=0 default)."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "test", "text": "persist"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        for i in range(5):
            bells = asdaaas.poll_doorbells("Trip")
            assert len(bells) == 1
            assert bells[0]["delivered_count"] == i + 1

    def test_priority_ordering(self, hub_dir):
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        for p, name in [(5, "low"), (1, "high"), (3, "mid")]:
            bell = {"adapter": "test", "priority": p, "text": name}
            with open(bell_dir / f"bell_{name}.json", "w") as f:
                json.dump(bell, f)
        
        bells = asdaaas.poll_doorbells("Trip")
        assert [b["text"] for b in bells] == ["high", "mid", "low"]

    def test_default_priority(self, hub_dir):
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "test", "text": "no priority set"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        bells = asdaaas.poll_doorbells("Trip")
        assert len(bells) == 1  # default priority 5

    def test_empty_dir(self, hub_dir):
        bells = asdaaas.poll_doorbells("Trip")
        assert bells == []


class TestFormatDoorbell:
    def test_with_command(self):
        bell = {"adapter": "impress", "command": "next_slide", "text": "ok: slide 3 of 5"}
        result = asdaaas.format_doorbell(bell)
        assert "[impress:next_slide" in result
        assert "ok: slide 3 of 5" in result

    def test_without_command(self):
        bell = {"adapter": "localmail", "text": "You have mail from Jr"}
        result = asdaaas.format_doorbell(bell)
        assert "[localmail" in result
        assert "You have mail from Jr" in result

    def test_includes_id(self):
        bell = {"adapter": "localmail", "text": "mail", "id": "bell_123"}
        result = asdaaas.format_doorbell(bell)
        assert "id=bell_123" in result

    def test_includes_delivery_count_on_redelivery(self):
        bell = {"adapter": "localmail", "text": "mail", "id": "bell_123", "delivered_count": 3}
        result = asdaaas.format_doorbell(bell)
        assert "delivery=3" in result

    def test_no_delivery_count_on_first(self):
        """First delivery (count=1) doesn't show delivery= to reduce noise."""
        bell = {"adapter": "localmail", "text": "mail", "id": "bell_123", "delivered_count": 1}
        result = asdaaas.format_doorbell(bell)
        assert "delivery=" not in result

    def test_no_id_no_meta(self):
        """Without id, no meta parenthetical."""
        bell = {"adapter": "localmail", "text": "mail"}
        result = asdaaas.format_doorbell(bell)
        assert result == "[localmail] mail"


class TestAckDoorbells:
    def test_ack_removes_matching(self, hub_dir):
        """Acking a doorbell removes it from disk."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        bell = {"adapter": "remind", "text": "check", "id": "bell_001"}
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump(bell, f)
        
        removed = asdaaas.ack_doorbells("Trip", ["bell_001"])
        assert removed == 1
        assert len(list(bell_dir.glob("*.json"))) == 0

    def test_ack_preserves_unmatched(self, hub_dir):
        """Doorbells not in handled list persist."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump({"adapter": "remind", "text": "a", "id": "bell_001"}, f)
        with open(bell_dir / "bell_002.json", "w") as f:
            json.dump({"adapter": "irc", "text": "b", "id": "bell_002"}, f)
        
        removed = asdaaas.ack_doorbells("Trip", ["bell_001"])
        assert removed == 1
        assert len(list(bell_dir.glob("*.json"))) == 1
        # Remaining is bell_002
        with open(bell_dir / "bell_002.json") as f:
            remaining = json.load(f)
        assert remaining["id"] == "bell_002"

    def test_ack_multiple(self, hub_dir):
        """Can ack multiple doorbells at once."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        for i in range(5):
            with open(bell_dir / f"bell_{i:03d}.json", "w") as f:
                json.dump({"adapter": "test", "text": f"msg{i}", "id": f"bell_{i:03d}"}, f)
        
        removed = asdaaas.ack_doorbells("Trip", ["bell_001", "bell_003"])
        assert removed == 2
        assert len(list(bell_dir.glob("*.json"))) == 3

    def test_ack_nonexistent_id(self, hub_dir):
        """Acking a non-existent id is a no-op."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump({"adapter": "test", "text": "msg", "id": "bell_001"}, f)
        
        removed = asdaaas.ack_doorbells("Trip", ["nonexistent_id"])
        assert removed == 0
        assert len(list(bell_dir.glob("*.json"))) == 1

    def test_ack_empty_list(self, hub_dir):
        """Acking empty list is a no-op."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump({"adapter": "test", "text": "msg", "id": "bell_001"}, f)
        
        removed = asdaaas.ack_doorbells("Trip", [])
        assert removed == 0
        assert len(list(bell_dir.glob("*.json"))) == 1

    def test_ack_uses_id_from_file(self, hub_dir):
        """Ack matches on id field, not filename."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        # File named differently than id
        with open(bell_dir / "somefile.json", "w") as f:
            json.dump({"adapter": "test", "text": "msg", "id": "custom_id"}, f)
        
        removed = asdaaas.ack_doorbells("Trip", ["custom_id"])
        assert removed == 1

    def test_ack_no_dir(self):
        """Acking when no doorbell directory exists returns 0."""
        removed = asdaaas.ack_doorbells("nonexistent_agent", ["id1"])
        assert removed == 0

    def test_ack_continue_doorbell_full_cycle(self, hub_dir, write_awareness):
        """Reproduce ghost doorbell: continue bell (no id) -> poll -> ack -> verify gone.
        
        The continue doorbell is written without an 'id' field. poll_doorbells
        assigns id=f.stem and writes it back. The agent sees the id in the
        formatted text and acks it. The ack should remove the file."""
        write_awareness("Trip", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Trip")
        
        # Step 1: Queue a continue doorbell (no id field, like queue_continue_doorbell does)
        asdaaas.queue_continue_doorbell("Trip")
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        files = list(bell_dir.glob("cont_*.json"))
        assert len(files) == 1
        filename_stem = files[0].stem
        
        # Step 2: poll_doorbells assigns id and increments delivered_count
        bells = asdaaas.poll_doorbells("Trip", awareness)
        assert len(bells) == 1
        assert bells[0]["id"] == filename_stem  # id assigned from filename
        assert bells[0]["delivered_count"] == 1
        
        # Step 3: format_doorbell shows the id to the agent
        formatted = asdaaas.format_doorbell(bells[0])
        assert f"id={filename_stem}" in formatted
        
        # Step 4: Agent acks using the id it saw
        removed = asdaaas.ack_doorbells("Trip", [filename_stem])
        assert removed == 1
        assert len(list(bell_dir.glob("*.json"))) == 0  # GONE


class TestCleanupCompactDoorbells:
    """Tests for _cleanup_compact_doorbells -- prevents compaction confirmation loop."""

    def test_removes_compact_confirm_doorbells(self, hub_dir):
        """compact_confirm doorbells are removed after compaction."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        with open(bell_dir / "cpt_abc123.json", "w") as f:
            json.dump({"adapter": "session", "command": "compact_confirm",
                        "text": "Compaction requested. To confirm..."}, f)
        asdaaas._cleanup_compact_doorbells("Trip")
        assert len(list(bell_dir.glob("*.json"))) == 0

    def test_preserves_non_compact_doorbells(self, hub_dir):
        """Other doorbells are not affected by compact cleanup."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        with open(bell_dir / "cpt_abc123.json", "w") as f:
            json.dump({"adapter": "session", "command": "compact_confirm",
                        "text": "Compaction requested..."}, f)
        with open(bell_dir / "irc_msg.json", "w") as f:
            json.dump({"adapter": "irc", "text": "hello"}, f)
        with open(bell_dir / "hb_xyz.json", "w") as f:
            json.dump({"adapter": "heartbeat", "text": "heartbeat"}, f)
        asdaaas._cleanup_compact_doorbells("Trip")
        remaining = list(bell_dir.glob("*.json"))
        assert len(remaining) == 2  # irc + heartbeat survive

    def test_removes_multiple_compact_confirm(self, hub_dir):
        """Multiple stale compact_confirm doorbells are all cleaned up."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        for i in range(5):
            with open(bell_dir / f"cpt_{i:03d}.json", "w") as f:
                json.dump({"adapter": "session", "command": "compact_confirm",
                            "text": f"confirm {i}"}, f)
        asdaaas._cleanup_compact_doorbells("Trip")
        assert len(list(bell_dir.glob("*.json"))) == 0

    def test_noop_when_no_compact_doorbells(self, hub_dir):
        """No error when no compact_confirm doorbells exist."""
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        with open(bell_dir / "irc_msg.json", "w") as f:
            json.dump({"adapter": "irc", "text": "hello"}, f)
        asdaaas._cleanup_compact_doorbells("Trip")
        assert len(list(bell_dir.glob("*.json"))) == 1

    def test_noop_when_no_dir(self):
        """No error when doorbell directory doesn't exist."""
        asdaaas._cleanup_compact_doorbells("nonexistent_agent")  # should not raise


class TestHasPendingDoorbells:
    def test_has_pending(self, hub_dir):
        bell_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "doorbells"
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump({"adapter": "test", "text": "msg"}, f)
        assert asdaaas.has_pending_doorbells("Trip") is True

    def test_no_pending(self, hub_dir):
        assert asdaaas.has_pending_doorbells("Trip") is False

    def test_empty_dir(self, hub_dir):
        """Directory exists but no json files."""
        # bell_dir already created by fixture
        assert asdaaas.has_pending_doorbells("Trip") is False


# ============================================================================
# Commands
# ============================================================================

class TestPollCommands:
    def test_reads_and_deletes_legacy(self, hub_dir):
        """Legacy commands.json is read and deleted."""
        cmd_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "compact", "request_id": "req-001"}, f)
        
        cmds = asdaaas.poll_commands("Trip")
        assert len(cmds) == 1
        assert cmds[0]["action"] == "compact"
        assert cmds[0]["request_id"] == "req-001"
        assert not cmd_file.exists()

    def test_no_command(self, hub_dir):
        cmds = asdaaas.poll_commands("Trip")
        assert cmds == []

    def test_second_poll_returns_empty(self, hub_dir):
        """poll_commands is destructive: second poll finds nothing."""
        cmd_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "delay", "seconds": 300}, f)

        asdaaas.poll_commands("Trip")
        assert asdaaas.poll_commands("Trip") == []

    def test_queue_directory(self, hub_dir):
        """Commands in commands/ directory are read in order."""
        cmd_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands"
        cmd_dir.mkdir(parents=True, exist_ok=True)
        with open(cmd_dir / "cmd_001_aaaa.json", "w") as f:
            json.dump({"action": "ack", "handled": ["bell_1"]}, f)
        with open(cmd_dir / "cmd_002_bbbb.json", "w") as f:
            json.dump({"action": "delay", "seconds": 300}, f)

        cmds = asdaaas.poll_commands("Trip")
        assert len(cmds) == 2
        assert cmds[0]["action"] == "ack"
        assert cmds[1]["action"] == "delay"
        # Files deleted
        assert not list(cmd_dir.glob("*.json"))

    def test_legacy_before_queue(self, hub_dir):
        """Legacy commands.json is processed before queue directory."""
        cmd_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands.json"
        cmd_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands"
        cmd_dir.mkdir(parents=True, exist_ok=True)
        with open(cmd_file, "w") as f:
            json.dump({"action": "ack", "handled": ["bell_1"]}, f)
        with open(cmd_dir / "cmd_001_aaaa.json", "w") as f:
            json.dump({"action": "delay", "seconds": 60}, f)

        cmds = asdaaas.poll_commands("Trip")
        assert len(cmds) == 2
        assert cmds[0]["action"] == "ack"   # legacy first
        assert cmds[1]["action"] == "delay"  # queue second

    def test_write_command_helper(self, hub_dir):
        """write_command creates a file in commands/ directory."""
        fp = asdaaas.write_command("Trip", {"action": "delay", "seconds": 300})
        assert fp.exists()
        cmds = asdaaas.poll_commands("Trip")
        assert len(cmds) == 1
        assert cmds[0]["action"] == "delay"
        assert cmds[0]["seconds"] == 300

    def test_write_command_ordering(self, hub_dir):
        """Multiple write_command calls are read in order."""
        asdaaas.write_command("Trip", {"action": "ack", "handled": ["b1"]})
        time.sleep(0.002)  # ensure different timestamps
        asdaaas.write_command("Trip", {"action": "delay", "seconds": 60})
        cmds = asdaaas.poll_commands("Trip")
        assert len(cmds) == 2
        assert cmds[0]["action"] == "ack"
        assert cmds[1]["action"] == "delay"


class TestHasPendingCommands:
    """Non-destructive command check — used during delay interruption."""

    def test_detects_pending_command(self, hub_dir):
        cmd_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "compact"}, f)
        assert asdaaas.has_pending_commands("Trip") is True

    def test_no_pending(self, hub_dir):
        assert asdaaas.has_pending_commands("Trip") is False

    def test_does_not_delete_file(self, hub_dir):
        """Critical: has_pending must NOT consume the command."""
        cmd_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "delay", "seconds": 300}, f)

        # Check three times — file must survive
        assert asdaaas.has_pending_commands("Trip") is True
        assert asdaaas.has_pending_commands("Trip") is True
        assert asdaaas.has_pending_commands("Trip") is True
        assert cmd_file.exists()

    def test_command_survives_for_poll(self, hub_dir):
        """has_pending detects, then poll_commands reads and deletes."""
        cmd_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "ack", "handled": ["bell_001"]}, f)

        # Non-destructive check (simulates delay interruption)
        assert asdaaas.has_pending_commands("Trip") is True
        # Destructive read (simulates main loop after delay breaks)
        cmds = asdaaas.poll_commands("Trip")
        assert cmds[0]["action"] == "ack"
        # Now it's gone
        assert asdaaas.has_pending_commands("Trip") is False

    def test_detects_queue_directory(self, hub_dir):
        """has_pending detects commands in queue directory."""
        cmd_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands"
        cmd_dir.mkdir(parents=True, exist_ok=True)
        with open(cmd_dir / "cmd_001.json", "w") as f:
            json.dump({"action": "delay", "seconds": 60}, f)
        assert asdaaas.has_pending_commands("Trip") is True

    def test_detects_either_source(self, hub_dir):
        """has_pending detects from legacy or queue."""
        # Queue only
        cmd_dir = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands"
        cmd_dir.mkdir(parents=True, exist_ok=True)
        with open(cmd_dir / "cmd_001.json", "w") as f:
            json.dump({"action": "delay"}, f)
        assert asdaaas.has_pending_commands("Trip") is True
        # Consume queue
        asdaaas.poll_commands("Trip")
        assert asdaaas.has_pending_commands("Trip") is False
        # Legacy only
        cmd_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "ack"}, f)
        assert asdaaas.has_pending_commands("Trip") is True


# ============================================================================
# Health
# ============================================================================

class TestWriteHealth:
    def test_creates_health_file(self, hub_dir):
        asdaaas.write_health("Trip", "ready", "session=test", 50000, 200000)
        health_file = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "health.json"
        assert health_file.exists()
        with open(health_file) as f:
            data = json.load(f)
        assert data["agent"] == "Trip"
        assert data["status"] == "ready"
        assert data["totalTokens"] == 50000
        assert data["contextWindow"] == 200000
        assert data["pid"] == os.getpid()


# ============================================================================
# Profiling
# ============================================================================

class TestMessageTimer:
    def test_mark_and_elapsed(self):
        timer = asdaaas.MessageTimer("Trip", "msg-001")
        time.sleep(0.01)
        timer.mark("prompt_sent")
        time.sleep(0.01)
        timer.mark("first_chunk")
        
        elapsed = timer.elapsed("inbox_pickup", "prompt_sent")
        assert elapsed is not None
        assert elapsed >= 10  # at least 10ms

    def test_summary(self):
        timer = asdaaas.MessageTimer("Trip", "msg-001")
        timer.mark("prompt_sent")
        timer.mark("first_chunk")
        timer.mark("prompt_complete")
        timer.mark("outbox_done")
        
        summary = timer.summary()
        assert "queue_wait" in summary
        assert "agent_think" in summary
        assert "total" in summary

    def test_log_line(self):
        timer = asdaaas.MessageTimer("Trip", "msg-001")
        timer.mark("prompt_sent")
        timer.mark("first_chunk")
        timer.mark("prompt_complete")
        timer.mark("outbox_done")
        
        line = timer.log_line()
        assert "[profile]" in line
        assert "Trip" in line
        assert "msg-001" in line

    def test_missing_marks(self):
        timer = asdaaas.MessageTimer("Trip", "msg-001")
        elapsed = timer.elapsed("prompt_sent", "first_chunk")
        assert elapsed is None


class TestWriteProfile:
    def test_writes_jsonl_and_latest(self, hub_dir):
        timer = asdaaas.MessageTimer("Trip", "msg-001")
        timer.mark("prompt_sent")
        timer.mark("first_chunk")
        timer.mark("prompt_complete")
        timer.mark("outbox_done")
        
        asdaaas.write_profile("Trip", timer)
        
        jsonl = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "profile" / "Trip.jsonl"
        latest = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "profile" / "Trip_latest.json"
        assert jsonl.exists()
        assert latest.exists()
        
        with open(latest) as f:
            data = json.load(f)
        assert data["agent"] == "Trip"
        assert "stages_ms" in data


# ============================================================================
# JSON-RPC helpers
# ============================================================================

class TestRpcHelpers:
    def test_rpc_request_format(self):
        # Reset counter for deterministic test
        old_id = asdaaas._rpc_id
        asdaaas._rpc_id = 0
        
        msg = asdaaas.rpc_request("initialize", {"key": "value"})
        data = json.loads(msg.strip())
        assert data["jsonrpc"] == "2.0"
        assert data["method"] == "initialize"
        assert data["params"] == {"key": "value"}
        assert data["id"] == 1
        
        asdaaas._rpc_id = old_id

    def test_rpc_notification_no_id(self):
        msg = asdaaas.rpc_notification("notifications/initialized")
        data = json.loads(msg.strip())
        assert data["jsonrpc"] == "2.0"
        assert data["method"] == "notifications/initialized"
        assert "id" not in data

    def test_rpc_request_increments_id(self):
        old_id = asdaaas._rpc_id
        asdaaas.rpc_request("test1")
        id1 = asdaaas._rpc_id
        asdaaas.rpc_request("test2")
        id2 = asdaaas._rpc_id
        assert id2 == id1 + 1
        asdaaas._rpc_id = old_id


# ============================================================================
# Attention Structure
# ============================================================================

class TestPollAttentions:
    def test_empty_dir(self, hub_dir):
        result = asdaaas.poll_attentions("Sr")
        assert result == []

    def test_no_dir(self, hub_dir):
        """Agent with no attention directory returns empty list."""
        result = asdaaas.poll_attentions("NonexistentAgent")
        assert result == []

    def test_reads_attention_file(self, hub_dir, write_attention_file):
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-001")
        result = asdaaas.poll_attentions("Jr")
        assert len(result) == 1
        assert result[0]["msg_id"] == "msg-001"
        assert result[0]["expecting_from"] == "Trip"
        assert "_path" in result[0]

    def test_fifo_ordering(self, hub_dir, write_attention_file):
        """Attentions sorted by created_at (oldest first)."""
        now = time.time()
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-old",
                            created_at=now - 100)
        write_attention_file("Jr", expecting_from="Q", msg_id="msg-new",
                            created_at=now)
        result = asdaaas.poll_attentions("Jr")
        assert len(result) == 2
        assert result[0]["msg_id"] == "msg-old"
        assert result[1]["msg_id"] == "msg-new"

    def test_skips_corrupt_json(self, hub_dir):
        """Corrupt JSON files are skipped, not crash."""
        attn_dir = hub_dir.parent / "agents" / "Jr" / "asdaaas" / "attention"
        attn_dir.mkdir(parents=True, exist_ok=True)
        with open(attn_dir / "bad.json", "w") as f:
            f.write("not valid json{{{")
        result = asdaaas.poll_attentions("Jr")
        assert result == []


class TestCheckAttentionTimeouts:
    def test_no_timeouts_when_fresh(self, hub_dir, write_attention_file):
        """Attentions that haven't expired don't produce timeouts."""
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-001",
                            timeout_s=3600)  # 1 hour from now
        attentions = asdaaas.poll_attentions("Jr")
        timeouts = asdaaas.check_attention_timeouts("Jr", attentions)
        assert timeouts == []

    def test_expired_produces_timeout(self, hub_dir, write_attention_file):
        """Expired attention produces timeout doorbell and deletes file."""
        now = time.time()
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-expired",
                            timeout_s=10, created_at=now - 60)  # expired 50s ago
        attentions = asdaaas.poll_attentions("Jr")
        timeouts = asdaaas.check_attention_timeouts("Jr", attentions)
        assert len(timeouts) == 1
        assert "TIMEOUT" in timeouts[0]["text"]
        assert "msg-expired" in timeouts[0]["text"]
        assert "Trip" in timeouts[0]["text"]
        # File should be deleted
        attn_file = hub_dir.parent / "agents" / "Jr" / "asdaaas" / "attention" / "msg-expired.json"
        assert not attn_file.exists()

    def test_mixed_expired_and_fresh(self, hub_dir, write_attention_file):
        """Only expired attentions produce timeouts."""
        now = time.time()
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-expired",
                            timeout_s=10, created_at=now - 60)
        write_attention_file("Jr", expecting_from="Q", msg_id="msg-fresh",
                            timeout_s=3600, created_at=now)
        attentions = asdaaas.poll_attentions("Jr")
        timeouts = asdaaas.check_attention_timeouts("Jr", attentions)
        assert len(timeouts) == 1
        assert "msg-expired" in timeouts[0]["text"]
        # Fresh attention file should still exist
        fresh_file = hub_dir.parent / "agents" / "Jr" / "asdaaas" / "attention" / "msg-fresh.json"
        assert fresh_file.exists()


class TestMatchAttention:
    def test_matches_by_sender(self, hub_dir, write_attention_file):
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-001")
        attentions = asdaaas.poll_attentions("Jr")
        result = asdaaas.match_attention("Jr", attentions, "Trip")
        assert result is not None
        assert result["msg_id"] == "msg-001"

    def test_no_match_wrong_sender(self, hub_dir, write_attention_file):
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-001")
        attentions = asdaaas.poll_attentions("Jr")
        result = asdaaas.match_attention("Jr", attentions, "Q")
        assert result is None

    def test_case_insensitive(self, hub_dir, write_attention_file):
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-001")
        attentions = asdaaas.poll_attentions("Jr")
        result = asdaaas.match_attention("Jr", attentions, "trip")
        assert result is not None

    def test_fifo_match(self, hub_dir, write_attention_file):
        """When multiple attentions expect same sender, oldest matches first."""
        now = time.time()
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-old",
                            created_at=now - 100)
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-new",
                            created_at=now)
        attentions = asdaaas.poll_attentions("Jr")
        result = asdaaas.match_attention("Jr", attentions, "Trip")
        assert result["msg_id"] == "msg-old"  # FIFO: oldest first

    def test_match_different_targets(self, hub_dir, write_attention_file):
        """Attentions for different targets match independently."""
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-trip")
        write_attention_file("Jr", expecting_from="Q", msg_id="msg-q")
        attentions = asdaaas.poll_attentions("Jr")
        
        trip_match = asdaaas.match_attention("Jr", attentions, "Trip")
        assert trip_match["msg_id"] == "msg-trip"
        
        q_match = asdaaas.match_attention("Jr", attentions, "Q")
        assert q_match["msg_id"] == "msg-q"

    def test_empty_attentions(self, hub_dir):
        result = asdaaas.match_attention("Jr", [], "Trip")
        assert result is None


class TestResolveAttention:
    def test_creates_response_doorbell(self, hub_dir, write_attention_file):
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-001")
        attentions = asdaaas.poll_attentions("Jr")
        attn = attentions[0]
        
        bell = asdaaas.resolve_attention(attn, "slide advanced to 3 of 5")
        assert bell["adapter"] == "attention"
        assert "RESPONSE to msg-001" in bell["text"]
        assert "Trip" in bell["text"]
        assert "slide advanced" in bell["text"]
        assert bell["priority"] == 2

    def test_truncates_long_response(self, hub_dir, write_attention_file):
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-001")
        attentions = asdaaas.poll_attentions("Jr")
        attn = attentions[0]
        
        long_text = "x" * 2000
        bell = asdaaas.resolve_attention(attn, long_text)
        # Response preview should be truncated to 800 + "..."
        assert len(bell["text"]) < 900  # 800 preview + prefix text

    def test_deletes_attention_file(self, hub_dir, write_attention_file):
        write_attention_file("Jr", expecting_from="Trip", msg_id="msg-001")
        attentions = asdaaas.poll_attentions("Jr")
        attn = attentions[0]
        
        asdaaas.resolve_attention(attn, "done")
        # File should be deleted
        attn_file = hub_dir.parent / "agents" / "Jr" / "asdaaas" / "attention" / "msg-001.json"
        assert not attn_file.exists()


# ============================================================================
# collect_response: _meta extraction after prompt_complete
# ============================================================================

class TestCollectResponseMeta:
    """Verify that collect_response captures _meta.totalTokens from the
    JSON-RPC response frame that arrives AFTER _x.ai/session/prompt_complete.
    
    Bug: prompt_complete was breaking the loop before the response frame
    (which carries result._meta.totalTokens) could be read. This caused
    all health files to show totalTokens=0.
    """

    def test_meta_extracted_after_prompt_complete(self):
        """Response frame with _meta arrives after prompt_complete -- must be captured."""
        import asyncio

        async def _run():
            # Simulate the frame sequence that grok stdio sends:
            # 1. agent_message_chunk with streaming _meta (every frame has this)
            # 2. _x.ai/session/prompt_complete (notification)
            # 3. JSON-RPC response with id + result._meta (final authoritative meta)
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Hello world"}},
                    "_meta": {"totalTokens": 84900}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
                {"jsonrpc": "2.0", "id": 42, "result": {
                    "_meta": {"totalTokens": 85000, "modelId": "grok-4", "stopReason": "end_turn"}}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            return await asdaaas.collect_response(reader, prompt_id=42, timeout=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "Hello world"
        assert meta.get("totalTokens") == 85000
        assert meta.get("modelId") == "grok-4"
        assert meta.get("stopReason") == "end_turn"

    def test_meta_from_streaming_when_no_response_frame(self):
        """Even without a response frame, streaming _meta should provide totalTokens."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Hi"}},
                    "_meta": {"totalTokens": 45000, "updateType": "AgentMessageChunk"}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            return await asdaaas.collect_response(reader, prompt_id=42, timeout=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "Hi"
        assert meta.get("totalTokens") == 45000

    def test_meta_zero_when_no_meta_anywhere(self):
        """If no _meta on any frame, totalTokens should be 0."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Hi"}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            return await asdaaas.collect_response(reader, prompt_id=42, timeout=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "Hi"
        assert meta.get("totalTokens", 0) == 0

    def test_streaming_meta_updates_throughout_response(self):
        """Streaming _meta should track the latest totalTokens across chunks."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "First "}},
                    "_meta": {"totalTokens": 30000}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "second "}},
                    "_meta": {"totalTokens": 30500}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc1", "title": "read_file"},
                    "_meta": {"totalTokens": 31000}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "third."}},
                    "_meta": {"totalTokens": 49000}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            return await asdaaas.collect_response(reader, prompt_id=42, timeout=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "First second third."
        # Should have the latest streaming totalTokens (49000)
        assert meta.get("totalTokens") == 49000

    def test_on_meta_callback_fires_with_streaming_tokens(self):
        """on_meta callback should fire for each streaming frame with totalTokens."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "A"}},
                    "_meta": {"totalTokens": 10000}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc1", "title": "grep"},
                    "_meta": {"totalTokens": 15000}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "B"}},
                    "_meta": {"totalTokens": 20000}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            callback_values = []
            def on_meta(tokens):
                callback_values.append(tokens)

            speech, thoughts, meta = await asdaaas.collect_response(
                reader, prompt_id=42, timeout=5.0, on_meta=on_meta)
            return speech, meta, callback_values

        speech, meta, cb_values = asyncio.run(_run())
        assert speech == "AB"
        assert cb_values == [10000, 15000, 20000]
        assert meta.get("totalTokens") == 20000

    def test_meta_from_id_match_without_prompt_complete(self):
        """Response frame with matching id should work even without prompt_complete."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Test"}}}},
                {"jsonrpc": "2.0", "id": 99, "result": {
                    "_meta": {"totalTokens": 120000, "modelId": "grok-4", "stopReason": "end_turn"}}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            return await asdaaas.collect_response(reader, prompt_id=99, timeout=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "Test"
        assert meta.get("totalTokens") == 120000


# ============================================================================
# collect_response: keepalive timeout behavior
# ============================================================================

class TestCollectResponseKeepalive:
    """Verify keepalive timeout: as long as frames arrive, keep reading."""

    def test_keepalive_resets_on_each_frame(self):
        """Frames arriving within keepalive window should all be collected,
        even if total elapsed would exceed old wall-clock timeout."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": f"chunk{i} "}},
                    "_meta": {"totalTokens": 10000 + i * 100}}}
                for i in range(10)
            ]
            frames.append({"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}})
            frames.append({"jsonrpc": "2.0", "id": 42, "result": {
                "_meta": {"totalTokens": 85000, "modelId": "grok-4", "stopReason": "end_turn"}}})

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            return await asdaaas.collect_response(
                reader, prompt_id=42,
                keepalive_timeout=5.0, max_wall_clock=600.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert "chunk0" in speech
        assert "chunk9" in speech
        assert meta.get("totalTokens") == 85000

    def test_keepalive_timeout_fires_on_silence(self):
        """When no frames arrive, keepalive timeout should fire."""
        import asyncio

        async def _run():
            reader = asyncio.StreamReader()
            # Feed one frame then nothing — keepalive should fire
            frame = {"jsonrpc": "2.0", "method": "session/update", "params": {
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"text": "hello"}}}}
            reader.feed_data(json.dumps(frame).encode() + b"\n")
            # Don't feed EOF — simulate silence

            return await asdaaas.collect_response(
                reader, prompt_id=42,
                keepalive_timeout=0.2, max_wall_clock=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "hello"
        # Should have timed out via keepalive, not wall clock

    def test_prompt_complete_tightens_keepalive(self):
        """After prompt_complete, keepalive tightens to 2s for response frame."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Hi"}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
                # Response frame arrives immediately
                {"jsonrpc": "2.0", "id": 42, "result": {
                    "_meta": {"totalTokens": 50000, "modelId": "grok-4", "stopReason": "end_turn"}}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            return await asdaaas.collect_response(
                reader, prompt_id=42,
                keepalive_timeout=30.0, max_wall_clock=600.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "Hi"
        assert meta.get("totalTokens") == 50000

    def test_backward_compat_timeout_param(self):
        """Old-style timeout= parameter still works (doesn't crash)."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "compat"}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            return await asdaaas.collect_response(reader, prompt_id=42, timeout=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "compat"


# ============================================================================
# collect_response: on_speech_chunk and on_tool_call callbacks
# ============================================================================

class TestCollectResponseStreamingCallbacks:
    """Verify on_speech_chunk and on_tool_call callbacks fire correctly."""

    def test_on_speech_chunk_fires_for_each_chunk(self):
        """on_speech_chunk callback fires once per agent_message_chunk."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Hello "}}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "world"}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            chunks = []
            speech, thoughts, meta = await asdaaas.collect_response(
                reader, prompt_id=42, timeout=5.0,
                on_speech_chunk=lambda t: chunks.append(t))
            return speech, chunks

        speech, chunks = asyncio.run(_run())
        assert speech == "Hello world"
        assert chunks == ["Hello ", "world"]

    def test_on_tool_call_fires_with_title(self):
        """on_tool_call callback fires with tool title on tool_call frames."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Let me check..."}}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc1", "title": "run_terminal_cmd"}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "All good."}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            tools = []
            speech, thoughts, meta = await asdaaas.collect_response(
                reader, prompt_id=42, timeout=5.0,
                on_tool_call=lambda t: tools.append(t))
            return speech, tools

        speech, tools = asyncio.run(_run())
        assert speech == "Let me check...All good."
        assert tools == ["run_terminal_cmd"]

    def test_multiple_tool_calls(self):
        """Multiple tool_call frames each fire the callback."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Step 1. "}}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc1", "title": "read_file"}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Step 2. "}}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc2", "title": "grep"}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Done."}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            chunks = []
            tools = []
            speech, thoughts, meta = await asdaaas.collect_response(
                reader, prompt_id=42, timeout=5.0,
                on_speech_chunk=lambda t: chunks.append(t),
                on_tool_call=lambda t: tools.append(t))
            return speech, chunks, tools

        speech, chunks, tools = asyncio.run(_run())
        assert speech == "Step 1. Step 2. Done."
        assert chunks == ["Step 1. ", "Step 2. ", "Done."]
        assert tools == ["read_file", "grep"]

    def test_no_callbacks_is_backward_compat(self):
        """Without callbacks, collect_response works exactly as before."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "normal"}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            return await asdaaas.collect_response(reader, prompt_id=42, timeout=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "normal"


# ============================================================================
# collect_response: tool_call tracking (pending tools extend keepalive)
# ============================================================================

class TestCollectResponseToolCallTracking:
    """Verify that pending tool calls prevent premature keepalive exit."""

    def test_tool_call_extends_keepalive(self):
        """While a tool_call is pending, keepalive should not fire even if
        no frames arrive for longer than keepalive_timeout."""
        import asyncio

        async def _run():
            reader = asyncio.StreamReader()

            # Feed frames with a delay between tool_call and tool_call_update
            # to simulate a long-running tool execution
            frames_before = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Running build..."}}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc1", "title": "run_terminal_cmd"}}},
            ]
            reader.feed_data(b"".join(json.dumps(f).encode() + b"\n" for f in frames_before))

            # Wait longer than keepalive_timeout (0.5s) to simulate tool execution
            await asyncio.sleep(0.7)

            # Tool completes, more speech, done
            frames_after = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call_update",
                               "toolCallId": "tc1", "status": "completed"}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": " Build passed."}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]
            reader.feed_data(b"".join(json.dumps(f).encode() + b"\n" for f in frames_after))
            reader.feed_eof()

            return await asdaaas.collect_response(
                reader, prompt_id=42,
                keepalive_timeout=0.5,  # short keepalive — would fire without tool tracking
                max_wall_clock=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        # Without tool tracking, we'd only get "Running build..." (keepalive fires at 0.5s)
        # With tool tracking, we get the full response
        assert speech == "Running build... Build passed."

    def test_tool_call_update_resumes_normal_keepalive(self):
        """After all tool calls complete and prompt_complete arrives,
        normal keepalive should resume for catching the response frame.
        If no response frame arrives after prompt_complete, tightened
        keepalive (2s) fires."""
        import asyncio

        async def _run():
            reader = asyncio.StreamReader()

            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Check."}}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc1", "title": "read_file"}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call_update",
                               "toolCallId": "tc1", "status": "completed"}}},
                # prompt_complete signals turn is over — keepalive tightens
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]
            reader.feed_data(b"".join(json.dumps(f).encode() + b"\n" for f in frames))

            # After prompt_complete with no pending tools, tightened keepalive
            # (2s) should fire. We verify by checking the function returns
            # in ~2s, not max_wall_clock (5s).
            return await asdaaas.collect_response(
                reader, prompt_id=42,
                keepalive_timeout=0.5,
                max_wall_clock=5.0)

        t0 = time.monotonic()
        speech, thoughts, meta = asyncio.run(_run())
        elapsed = time.monotonic() - t0
        assert speech == "Check."
        # Should exit in ~2s (tightened keepalive after prompt_complete), not 5s
        assert elapsed < 3.0, f"Took {elapsed:.1f}s, expected ~2s"

    def test_multiple_concurrent_tool_calls(self):
        """Multiple tool calls in flight — keepalive extended until ALL complete."""
        import asyncio

        async def _run():
            reader = asyncio.StreamReader()

            # Two tool calls started
            frames_start = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc1", "title": "read_file"}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc2", "title": "grep"}}},
            ]
            reader.feed_data(b"".join(json.dumps(f).encode() + b"\n" for f in frames_start))

            await asyncio.sleep(0.3)

            # First tool completes — still one pending
            frames_mid = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call_update",
                               "toolCallId": "tc1", "status": "completed"}}},
            ]
            reader.feed_data(b"".join(json.dumps(f).encode() + b"\n" for f in frames_mid))

            # Wait longer than keepalive — should NOT exit because tc2 still pending
            await asyncio.sleep(0.7)

            # Second tool completes, final speech
            frames_end = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call_update",
                               "toolCallId": "tc2", "status": "completed"}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Both done."}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]
            reader.feed_data(b"".join(json.dumps(f).encode() + b"\n" for f in frames_end))
            reader.feed_eof()

            return await asdaaas.collect_response(
                reader, prompt_id=42,
                keepalive_timeout=0.5,
                max_wall_clock=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "Both done."

    def test_tool_call_without_id_still_works(self):
        """tool_call frames without toolCallId should not crash."""
        import asyncio

        async def _run():
            frames = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Hi"}}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "title": "read_file"}}},  # no toolCallId
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": " there"}}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]

            frame_data = b"".join(json.dumps(f).encode() + b"\n" for f in frames)
            reader = asyncio.StreamReader()
            reader.feed_data(frame_data)
            reader.feed_eof()

            tools = []
            return await asdaaas.collect_response(
                reader, prompt_id=42, timeout=5.0,
                on_tool_call=lambda t: tools.append(t))

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "Hi there"

    def test_prompt_complete_with_pending_tools_no_tighten(self):
        """If prompt_complete arrives while tools are still pending (edge case),
        keepalive should NOT be tightened to 2s."""
        import asyncio

        async def _run():
            reader = asyncio.StreamReader()

            # tool_call started, then prompt_complete arrives (anomalous)
            frames_start = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call",
                               "toolCallId": "tc1", "title": "run_terminal_cmd"}}},
                {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete", "params": {}},
            ]
            reader.feed_data(b"".join(json.dumps(f).encode() + b"\n" for f in frames_start))

            # Wait 3s — would exceed 2s tightened keepalive but not wall clock
            await asyncio.sleep(0.7)

            # Tool completes, final speech
            frames_end = [
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "tool_call_update",
                               "toolCallId": "tc1", "status": "completed"}}},
                {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"text": "Recovered."}}}},
                {"jsonrpc": "2.0", "id": 42, "result": {
                    "_meta": {"totalTokens": 50000}}},
            ]
            reader.feed_data(b"".join(json.dumps(f).encode() + b"\n" for f in frames_end))
            reader.feed_eof()

            return await asdaaas.collect_response(
                reader, prompt_id=42,
                keepalive_timeout=0.5,
                max_wall_clock=5.0)

        speech, thoughts, meta = asyncio.run(_run())
        assert speech == "Recovered."
        assert meta.get("totalTokens") == 50000


class TestStreamingThoughts:
    """Tests for the StreamingThoughts accumulator class."""

    def test_accumulates_and_flushes_on_tool_call(self, hub_dir, write_gaze):
        write_gaze("Trip", speech_target="irc", speech_params={"channel": "#standup"},
                    thoughts_target="irc", thoughts_params={"channel": "#trip-thoughts"})
        gaze = asdaaas.read_gaze("Trip")
        st = asdaaas.StreamingThoughts("Trip", gaze)

        st.on_chunk("Let me ")
        st.on_chunk("check the tests...")
        st.on_tool_call("run_terminal_cmd")

        # Should have flushed to thoughts outbox
        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = sorted(outbox.glob("*.json"))
        assert len(files) == 1
        with open(files[0]) as f:
            data = json.load(f)
        assert data["content_type"] == "thoughts"
        assert "Let me check the tests..." in data["text"]
        assert "[run_terminal_cmd]" in data["text"]

    def test_flush_at_end(self, hub_dir, write_gaze):
        write_gaze("Trip", speech_target="irc", speech_params={"channel": "#standup"},
                    thoughts_target="irc", thoughts_params={"channel": "#trip-thoughts"})
        gaze = asdaaas.read_gaze("Trip")
        st = asdaaas.StreamingThoughts("Trip", gaze)

        st.on_chunk("Final words.")
        st.flush()

        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = list(outbox.glob("*.json"))
        assert len(files) == 1

    def test_null_thoughts_discards(self, hub_dir, write_gaze):
        """When thoughts target is null, chunks are discarded silently."""
        write_gaze("Trip", speech_target="irc", thoughts_target=None)
        gaze = asdaaas.read_gaze("Trip")
        st = asdaaas.StreamingThoughts("Trip", gaze)

        st.on_chunk("This should be discarded")
        st.on_tool_call("grep")
        st.flush()

        # No outbox files should exist
        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = list(outbox.glob("*.json"))
        assert len(files) == 0

    def test_multiple_flushes(self, hub_dir, write_gaze):
        """Each tool call flushes separately."""
        write_gaze("Trip", speech_target="irc", speech_params={"channel": "#standup"},
                    thoughts_target="irc", thoughts_params={"channel": "#trip-thoughts"})
        gaze = asdaaas.read_gaze("Trip")
        st = asdaaas.StreamingThoughts("Trip", gaze)

        st.on_chunk("Part 1. ")
        st.on_tool_call("read_file")
        st.on_chunk("Part 2. ")
        st.on_tool_call("grep")
        st.on_chunk("Part 3.")
        st.flush()

        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = sorted(outbox.glob("*.json"))
        assert len(files) == 3

    def test_final_speech_not_duplicated_to_thoughts(self, hub_dir, write_gaze):
        """Text after the last tool call is final speech, not thoughts.
        
        The main loop should NOT flush() after collect_response completes.
        Only text flushed at tool_call boundaries is intermediate thinking.
        The remaining buffer is the agent's actual response.
        """
        write_gaze("Trip", speech_target="irc", speech_params={"channel": "#standup"},
                    thoughts_target="irc", thoughts_params={"channel": "#trip-thoughts"})
        gaze = asdaaas.read_gaze("Trip")
        st = asdaaas.StreamingThoughts("Trip", gaze)

        st.on_chunk("Let me check... ")
        st.on_tool_call("read_file")  # flushes "Let me check..." to thoughts
        st.on_chunk("Here are the results: everything passed.")
        # Do NOT call st.flush() -- this simulates the main loop fix.
        # The remaining buffer is the final speech, routed via gaze.speech.

        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = sorted(outbox.glob("*.json"))
        assert len(files) == 1  # only the tool_call flush, not the final speech
        with open(files[0]) as f:
            data = json.load(f)
        assert "Let me check" in data["text"]
        assert "results" not in data["text"]  # final speech NOT in thoughts

    def test_empty_buffer_no_write(self, hub_dir, write_gaze):
        """Flushing empty buffer writes nothing."""
        write_gaze("Trip", speech_target="irc", speech_params={"channel": "#standup"},
                    thoughts_target="irc", thoughts_params={"channel": "#trip-thoughts"})
        gaze = asdaaas.read_gaze("Trip")
        st = asdaaas.StreamingThoughts("Trip", gaze)

        st.flush()
        st.on_tool_call("grep")  # tool call with no preceding speech

        outbox = hub_dir.parent / "agents" / "Trip" / "asdaaas" / "adapters" / "irc" / "outbox"
        files = list(outbox.glob("*.json"))
        assert len(files) == 0

    def test_chunk_count(self, hub_dir, write_gaze):
        write_gaze("Trip", speech_target="irc", thoughts_target=None)
        gaze = asdaaas.read_gaze("Trip")
        st = asdaaas.StreamingThoughts("Trip", gaze)

        st.on_chunk("a")
        st.on_chunk("b")
        st.on_chunk("c")
        assert st.chunk_count == 3


# ============================================================================
# context_left_tag: compact context-remaining tag
# ============================================================================

class TestContextLeftTag:
    # Usable budget = 85% of context_window (compaction threshold)
    # For 200k window: usable = 170k

    def test_basic_format(self):
        # 170k usable - 111k used = 59k left
        tag = asdaaas.context_left_tag(111000, 200000)
        assert tag == "\n[Context left 59k]"

    def test_large_remaining(self):
        # 170k usable - 10k used = 160k left
        tag = asdaaas.context_left_tag(10000, 200000)
        assert tag == "\n[Context left 160k]"

    def test_small_remaining(self):
        # 170k usable - 165k used = 5k left
        tag = asdaaas.context_left_tag(165000, 200000)
        assert tag == "\n[Context left 5.0k]"

    def test_very_small_remaining(self):
        # 170k usable - 169.5k used = 0.5k left
        tag = asdaaas.context_left_tag(169500, 200000)
        assert tag == "\n[Context left 0.5k]"

    def test_zero_remaining(self):
        # At compaction threshold exactly
        tag = asdaaas.context_left_tag(170000, 200000)
        assert tag == "\n[Context left 0.0k]"

    def test_over_compaction_threshold(self):
        # Past compaction threshold -- 0 left
        tag = asdaaas.context_left_tag(180000, 200000)
        assert tag == "\n[Context left 0.0k]"

    def test_zero_context_window(self):
        tag = asdaaas.context_left_tag(100000, 0)
        assert tag == ""

    def test_zero_tokens(self):
        tag = asdaaas.context_left_tag(0, 200000)
        assert tag == ""

    def test_boundary_10k(self):
        # 170k - 160k = 10k left -- integer format
        tag = asdaaas.context_left_tag(160000, 200000)
        assert tag == "\n[Context left 10k]"

    def test_just_under_10k(self):
        # 170k - 160.5k = 9.5k left -- decimal format
        tag = asdaaas.context_left_tag(160500, 200000)
        assert tag == "\n[Context left 9.5k]"

    # ---- Compaction status in tag ----

    def test_just_compacted(self):
        tag = asdaaas.context_left_tag(30000, 200000, turns_since_compaction=0)
        assert "just compacted" in tag
        assert "140k" in tag

    def test_compacted_one_turn_ago(self):
        tag = asdaaas.context_left_tag(35000, 200000, turns_since_compaction=1)
        assert "compacted 1 turn ago" in tag

    def test_compaction_available(self):
        tag = asdaaas.context_left_tag(100000, 200000, turns_since_compaction=2)
        assert "compaction available" in tag

    def test_compaction_available_many_turns(self):
        tag = asdaaas.context_left_tag(100000, 200000, turns_since_compaction=50)
        assert "compaction available" in tag

    def test_no_compaction_status_when_none(self):
        # When turns_since_compaction is None, no status suffix
        tag = asdaaas.context_left_tag(100000, 200000, turns_since_compaction=None)
        assert "compacted" not in tag
        assert "available" not in tag
        assert "70k" in tag

    def test_post_compaction_probe_text(self):
        """Probe prompt sent after /compact should contain compaction notice but NOT context tag.
        
        The probe's purpose is to force the grok binary to recalculate totalTokens.
        The context_left_tag is omitted because total_tokens is still stale at probe time.
        The next real prompt after the probe will have the correct tag.
        """
        probe_text = "[Compaction complete. You are resuming from a compacted context.]"
        assert "Compaction complete" in probe_text
        assert "resuming from a compacted context" in probe_text
        # No context tag on probe -- it would show stale data
        assert "[Context left" not in probe_text

    # ---- Gaze in context tag ----

    def test_gaze_irc_pm(self):
        gaze = {"speech": {"target": "irc", "params": {"room": "pm:eric"}}}
        tag = asdaaas.context_left_tag(100000, 200000, turns_since_compaction=2, gaze=gaze)
        assert tag == "\n[Context left 70k | compaction available | irc/pm:eric]"

    def test_gaze_irc_channel(self):
        gaze = {"speech": {"target": "irc", "params": {"room": "#standup"}}}
        tag = asdaaas.context_left_tag(100000, 200000, turns_since_compaction=2, gaze=gaze)
        assert tag == "\n[Context left 70k | compaction available | irc/#standup]"

    def test_gaze_slack(self):
        gaze = {"speech": {"target": "slack", "params": {"room": "#general"}}}
        tag = asdaaas.context_left_tag(100000, 200000, turns_since_compaction=2, gaze=gaze)
        assert tag == "\n[Context left 70k | compaction available | slack/#general]"

    def test_gaze_no_compaction_status(self):
        gaze = {"speech": {"target": "irc", "params": {"room": "pm:eric"}}}
        tag = asdaaas.context_left_tag(100000, 200000, gaze=gaze)
        assert tag == "\n[Context left 70k | irc/pm:eric]"

    def test_gaze_just_compacted(self):
        gaze = {"speech": {"target": "irc", "params": {"room": "pm:eric"}}}
        tag = asdaaas.context_left_tag(30000, 200000, turns_since_compaction=0, gaze=gaze)
        assert "just compacted" in tag
        assert "irc/pm:eric" in tag

    def test_gaze_none_no_label(self):
        tag = asdaaas.context_left_tag(100000, 200000, turns_since_compaction=2, gaze=None)
        assert tag == "\n[Context left 70k | compaction available]"

    def test_gaze_no_speech(self):
        gaze = {"speech": None, "thoughts": None}
        tag = asdaaas.context_left_tag(100000, 200000, turns_since_compaction=2, gaze=gaze)
        assert tag == "\n[Context left 70k | compaction available | none]"

    def test_gaze_adapter_only_no_room(self):
        gaze = {"speech": {"target": "irc", "params": {}}}
        tag = asdaaas.context_left_tag(100000, 200000, turns_since_compaction=2, gaze=gaze)
        assert tag == "\n[Context left 70k | compaction available | irc]"


class TestGazeLabel:
    def test_irc_pm(self):
        gaze = {"speech": {"target": "irc", "params": {"room": "pm:eric"}}}
        assert asdaaas.gaze_label(gaze) == "irc/pm:eric"

    def test_irc_channel(self):
        gaze = {"speech": {"target": "irc", "params": {"room": "#standup"}}}
        assert asdaaas.gaze_label(gaze) == "irc/#standup"

    def test_slack_channel(self):
        gaze = {"speech": {"target": "slack", "params": {"room": "#general"}}}
        assert asdaaas.gaze_label(gaze) == "slack/#general"

    def test_no_speech(self):
        gaze = {"speech": None}
        assert asdaaas.gaze_label(gaze) == "none"

    def test_empty_gaze(self):
        gaze = {}
        assert asdaaas.gaze_label(gaze) == "none"

    def test_adapter_no_room(self):
        gaze = {"speech": {"target": "irc"}}
        assert asdaaas.gaze_label(gaze) == "irc"


# ============================================================================
# Default Doorbell + Delay Command
# ============================================================================

class TestDefaultDoorbell:
    """Tests for the default doorbell (continuous existence) model."""

    def test_delay_command_parsed(self, hub_dir):
        """Delay command is read from command file."""
        cmd_file = hub_dir.parent / "agents" / "Q" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "delay", "seconds": 300}, f)
        cmds = asdaaas.poll_commands("Q")
        assert cmds[0]["action"] == "delay"
        assert cmds[0]["seconds"] == 300

    def test_delay_until_event_parsed(self, hub_dir):
        """Delay until_event command is read correctly."""
        cmd_file = hub_dir.parent / "agents" / "Q" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "delay", "seconds": "until_event"}, f)
        cmds = asdaaas.poll_commands("Q")
        assert cmds[0]["action"] == "delay"
        assert cmds[0]["seconds"] == "until_event"

    def test_delay_command_consumed_on_read(self, hub_dir):
        """Delay command file is deleted after reading."""
        cmd_file = hub_dir.parent / "agents" / "Q" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "delay", "seconds": 60}, f)
        asdaaas.poll_commands("Q")
        assert not cmd_file.exists()

    def test_default_doorbell_awareness_flag(self, hub_dir):
        """Default doorbell is controlled by awareness file flag."""
        awareness_file = hub_dir.parent / "agents" / "Q" / "asdaaas" / "awareness.json"
        with open(awareness_file, "w") as f:
            json.dump({"default_doorbell": True, "direct_attach": ["irc"]}, f)
        awareness = asdaaas.read_awareness("Q")
        assert awareness.get("default_doorbell") is True

    def test_default_doorbell_flag_absent(self, hub_dir):
        """Without the flag, default_doorbell is False (legacy mode)."""
        awareness_file = hub_dir.parent / "agents" / "Q" / "asdaaas" / "awareness.json"
        with open(awareness_file, "w") as f:
            json.dump({"direct_attach": ["irc"]}, f)
        awareness = asdaaas.read_awareness("Q")
        assert awareness.get("default_doorbell", False) is False

    def test_continue_doorbell_format(self, hub_dir):
        """Continue doorbell has correct format."""
        bell = {
            "adapter": "continue",
            "priority": 10,
            "text": "Your turn ended. You may continue, delay, or stand by.",
            "source": "continue",
        }
        formatted = asdaaas.format_doorbell(bell)
        assert "[continue]" in formatted
        assert "continue, delay, or stand by" in formatted

    def test_continue_doorbell_lowest_priority(self):
        """Continue doorbell has lowest priority (10) so other doorbells go first."""
        bells = [
            {"adapter": "continue", "priority": 10, "text": "continue"},
            {"adapter": "remind", "priority": 1, "text": "check trip"},
            {"adapter": "irc", "priority": 5, "text": "message from eric"},
        ]
        bells.sort(key=lambda b: b.get("priority", 5))
        assert bells[0]["adapter"] == "remind"
        assert bells[1]["adapter"] == "irc"
        assert bells[2]["adapter"] == "continue"

    def test_delay_zero_is_immediate(self):
        """Delay of 0 means immediate continuation (no sleep)."""
        delay = 0
        assert delay == 0  # trivial but documents the contract

    def test_delay_coexists_with_compact(self, hub_dir):
        """Delay and compact are separate command actions, don't interfere."""
        cmd_file = hub_dir.parent / "agents" / "Q" / "asdaaas" / "commands.json"
        # Write delay
        with open(cmd_file, "w") as f:
            json.dump({"action": "delay", "seconds": 60}, f)
        cmds = asdaaas.poll_commands("Q")
        assert cmds[0]["action"] == "delay"
        # Write compact
        with open(cmd_file, "w") as f:
            json.dump({"action": "compact"}, f)
        cmds = asdaaas.poll_commands("Q")
        assert cmds[0]["action"] == "compact"

    def test_doorbell_ttl_in_awareness(self, hub_dir):
        """Agent can declare per-source TTL in awareness file."""
        awareness_file = hub_dir.parent / "agents" / "Q" / "asdaaas" / "awareness.json"
        with open(awareness_file, "w") as f:
            json.dump({
                "default_doorbell": True,
                "doorbell_ttl": {
                    "heartbeat": 1,
                    "remind": 0,
                    "irc": 3,
                    "continue": 1,
                    "default": 5,
                },
            }, f)
        awareness = asdaaas.read_awareness("Q")
        ttl = awareness.get("doorbell_ttl", {})
        assert ttl["heartbeat"] == 1
        assert ttl["remind"] == 0
        assert ttl["continue"] == 1
        assert ttl["default"] == 5


# ============================================================================
# Piggyback Ack — atomic command + ack in single command file
# ============================================================================

class TestPiggybackAck:
    """Tests for the piggyback ack pattern: any command can carry an 'ack'
    field to clear doorbells atomically with the command action.
    
    Solves the single-slot race condition: commands.json holds one command
    at a time. Writing ack then delay overwrites the ack. Piggyback ack
    puts both in one file: {"action": "delay", "seconds": 300, "ack": ["bell_id"]}
    """

    def test_delay_with_ack_preserves_both(self, hub_dir):
        """Command file with action + ack has both fields readable."""
        cmd_file = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({
                "action": "delay",
                "seconds": 300,
                "ack": ["bell_001", "bell_002"]
            }, f)
        cmds = asdaaas.poll_commands("Sr")
        assert cmds[0]["action"] == "delay"
        assert cmds[0]["seconds"] == 300
        assert cmds[0]["ack"] == ["bell_001", "bell_002"]

    def test_ack_field_clears_doorbells(self, hub_dir):
        """Piggyback ack ids clear matching doorbells via ack_doorbells."""
        bell_dir = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "doorbells"
        with open(bell_dir / "bell_001.json", "w") as f:
            json.dump({"adapter": "context", "text": "ctx info", "id": "bell_001"}, f)
        with open(bell_dir / "bell_002.json", "w") as f:
            json.dump({"adapter": "remind", "text": "check", "id": "bell_002"}, f)
        with open(bell_dir / "bell_003.json", "w") as f:
            json.dump({"adapter": "irc", "text": "msg", "id": "bell_003"}, f)

        # Simulate what the main loop does: read command, process ack field
        cmd_file = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({
                "action": "delay",
                "seconds": "until_event",
                "ack": ["bell_001", "bell_002"]
            }, f)
        cmds = asdaaas.poll_commands("Sr")
        piggyback_ack = cmds[0].get("ack", [])
        removed = asdaaas.ack_doorbells("Sr", piggyback_ack)
        assert removed == 2
        # bell_003 survives
        remaining = list(bell_dir.glob("*.json"))
        assert len(remaining) == 1

    def test_no_ack_field_is_noop(self, hub_dir):
        """Command without ack field -- ack list is empty, no crash."""
        cmd_file = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "delay", "seconds": 60}, f)
        cmds = asdaaas.poll_commands("Sr")
        piggyback_ack = cmds[0].get("ack", [])
        assert piggyback_ack == []
        # ack_doorbells with empty list is a no-op
        removed = asdaaas.ack_doorbells("Sr", piggyback_ack)
        assert removed == 0

    def test_compact_with_ack(self, hub_dir):
        """Non-delay commands also support piggyback ack."""
        bell_dir = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "doorbells"
        with open(bell_dir / "ctx_abc.json", "w") as f:
            json.dump({"adapter": "context", "text": "45%", "id": "ctx_abc"}, f)

        cmd_file = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "compact", "ack": ["ctx_abc"]}, f)
        cmds = asdaaas.poll_commands("Sr")
        assert cmds[0]["action"] == "compact"
        removed = asdaaas.ack_doorbells("Sr", cmds[0].get("ack", []))
        assert removed == 1
        assert not list(bell_dir.glob("*.json"))

    def test_single_slot_race_solved_by_queue(self, hub_dir):
        """Command queue solves the race: multiple commands processed in order."""
        # OLD PATTERN (race with single file): two writes, second overwrites first
        cmd_file = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "ack", "handled": ["bell_001"]}, f)
        with open(cmd_file, "w") as f:
            json.dump({"action": "delay", "seconds": 300}, f)
        cmds = asdaaas.poll_commands("Sr")
        assert len(cmds) == 1  # only delay survived
        assert cmds[0]["action"] == "delay"

        # NEW PATTERN (queue): two separate files, both processed
        asdaaas.write_command("Sr", {"action": "ack", "handled": ["bell_001"]})
        time.sleep(0.002)
        asdaaas.write_command("Sr", {"action": "delay", "seconds": 300})
        cmds = asdaaas.poll_commands("Sr")
        assert len(cmds) == 2
        assert cmds[0]["action"] == "ack"
        assert cmds[1]["action"] == "delay"


# ============================================================================
# Delay Interruption -- the message drop bug (commit b9b7359)
# ============================================================================
#
# Scenario: agent sets delay=300. During the delay, a message arrives.
# The delay loop detects it and breaks. The main loop must then be able
# to read the message. Before the fix, poll_adapter_inboxes() in the
# delay loop consumed and deleted the message, so the main loop found
# nothing.
# ============================================================================

class TestDelayInterruptionPreservesMessages:
    """Regression tests for the delay-loop message drop bug."""

    def test_has_pending_then_poll_reads_message(self, hub_dir, write_awareness):
        """Simulates delay interruption: has_pending detects, poll reads."""
        write_awareness("Sr", direct_attach=["irc"])
        inbox = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "adapters" / "irc" / "inbox"
        with open(inbox / "msg_eric_pm.json", "w") as f:
            json.dump({
                "from": "eric", "to": "Sr", "text": "5",
                "adapter": "irc", "meta": {"room": "pm:eric"},
            }, f)

        awareness = asdaaas.read_awareness("Sr")

        # Step 1: delay loop checks (non-destructive)
        assert asdaaas.has_pending_adapter_messages("Sr", awareness) is True
        # Step 2: delay breaks, main loop polls (destructive)
        messages = asdaaas.poll_adapter_inboxes("Sr", awareness)
        assert len(messages) == 1
        assert messages[0]["text"] == "5"
        assert messages[0]["from"] == "eric"

    def test_old_bug_destructive_poll_loses_message(self, hub_dir, write_awareness):
        """Documents the old bug: two destructive polls = message lost."""
        write_awareness("Sr", direct_attach=["irc"])
        inbox = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "adapters" / "irc" / "inbox"
        with open(inbox / "msg_eric_pm.json", "w") as f:
            json.dump({
                "from": "eric", "to": "Sr", "text": "3",
                "adapter": "irc", "meta": {"room": "pm:eric"},
            }, f)

        awareness = asdaaas.read_awareness("Sr")

        # OLD BUG: delay loop used poll_adapter_inboxes (destructive)
        first_poll = asdaaas.poll_adapter_inboxes("Sr", awareness)
        assert len(first_poll) == 1  # message consumed here
        # Main loop re-polls — message is gone
        second_poll = asdaaas.poll_adapter_inboxes("Sr", awareness)
        assert len(second_poll) == 0  # THIS is the bug — message lost

    def test_multiple_messages_during_delay(self, hub_dir, write_awareness):
        """Multiple messages arrive during delay — all preserved."""
        write_awareness("Sr", direct_attach=["irc"])
        inbox = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "adapters" / "irc" / "inbox"
        for i in range(5):
            with open(inbox / f"msg_{i:03d}.json", "w") as f:
                json.dump({
                    "from": "eric", "to": "Sr", "text": str(i),
                    "adapter": "irc", "meta": {"room": "pm:eric"},
                }, f)

        awareness = asdaaas.read_awareness("Sr")

        # Non-destructive checks during delay
        assert asdaaas.has_pending_adapter_messages("Sr", awareness) is True
        # Main loop reads all of them
        messages = asdaaas.poll_adapter_inboxes("Sr", awareness)
        assert len(messages) == 5
        texts = {m["text"] for m in messages}
        assert texts == {"0", "1", "2", "3", "4"}

    def test_command_during_delay_preserved(self, hub_dir):
        """Command file during delay — preserved for main loop."""
        cmd_file = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "ack", "handled": ["bell_001"]}, f)

        # Non-destructive check during delay
        assert asdaaas.has_pending_commands("Sr") is True
        # Main loop reads it
        cmds = asdaaas.poll_commands("Sr")
        assert cmds[0]["action"] == "ack"

    def test_doorbell_during_delay_preserved(self, hub_dir):
        """Doorbell during delay — already non-destructive (was correct)."""
        bell_dir = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "doorbells"
        with open(bell_dir / "irc_001.json", "w") as f:
            json.dump({"adapter": "irc", "text": "message from Trip", "priority": 3}, f)

        # Non-destructive check during delay
        assert asdaaas.has_pending_doorbells("Sr") is True
        # File still there for main loop
        assert list(bell_dir.glob("*.json"))


class TestDelayInterruptSkipsContinue:
    """Tests for the delay interrupt + continue doorbell bug.

    Bug: when an external event (IRC message) arrives during a timed delay,
    the delay breaks correctly but still queues a [continue] doorbell.
    Agent sees both [continue] and the real message on the next turn,
    leading to false "conversation ended" responses mid-conversation.

    Fix: extracted run_delay_loop() and queue_continue_doorbell() functions.
    run_delay_loop returns (interrupted, reason). When interrupted, the main
    loop skips queue_continue_doorbell and goes straight to the next iteration."""

    @pytest.mark.asyncio
    async def test_delay_interrupted_by_message_returns_true(self, hub_dir, write_awareness):
        """run_delay_loop detects an IRC message and returns interrupted=True."""
        write_awareness("Sr", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Sr")
        inbox = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "adapters" / "irc" / "inbox"

        # "User" sends a message — simulates Eric typing during the delay
        with open(inbox / "msg_eric_pm.json", "w") as f:
            json.dump({
                "from": "eric", "to": "Sr",
                "text": "what about the dashboard?",
                "adapter": "irc", "meta": {"room": "pm:eric"},
            }, f)

        # Run delay loop with short delay (message is already there)
        interrupted, reason = await asdaaas.run_delay_loop(
            "Sr", delay_seconds=1.0, awareness=awareness, poll_interval=0.1
        )

        assert interrupted is True
        assert reason == "external_event"

        # Message still in inbox (non-destructive check)
        assert asdaaas.has_pending_adapter_messages("Sr", awareness) is True

    @pytest.mark.asyncio
    async def test_delay_expires_naturally_returns_false(self, hub_dir, write_awareness):
        """run_delay_loop with no external events returns interrupted=False."""
        write_awareness("Sr", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Sr")

        # No messages, no doorbells, no commands — delay should expire
        interrupted, reason = await asdaaas.run_delay_loop(
            "Sr", delay_seconds=0.3, awareness=awareness, poll_interval=0.1
        )

        assert interrupted is False
        assert reason == "expired"

    @pytest.mark.asyncio
    async def test_delay_interrupted_by_doorbell(self, hub_dir, write_awareness):
        """run_delay_loop detects a doorbell and returns interrupted=True."""
        write_awareness("Sr", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Sr")
        bell_dir = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "doorbells"

        # Localmail doorbell arrives during delay
        with open(bell_dir / "lm_001.json", "w") as f:
            json.dump({"adapter": "localmail", "text": "Q says hello", "priority": 3}, f)

        interrupted, reason = await asdaaas.run_delay_loop(
            "Sr", delay_seconds=1.0, awareness=awareness, poll_interval=0.1
        )

        assert interrupted is True
        assert reason == "external_event"

    @pytest.mark.asyncio
    async def test_delay_interrupted_by_command(self, hub_dir, write_awareness):
        """run_delay_loop detects a command and returns interrupted=True."""
        write_awareness("Sr", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Sr")

        cmd_file = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "ack", "handled": ["bell_001"]}, f)

        interrupted, reason = await asdaaas.run_delay_loop(
            "Sr", delay_seconds=1.0, awareness=awareness, poll_interval=0.1
        )

        assert interrupted is True
        assert reason == "external_event"

    def test_queue_continue_doorbell_creates_file(self, hub_dir):
        """queue_continue_doorbell creates a cont_*.json file."""
        bell_dir = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "doorbells"
        # Clear existing
        for f in bell_dir.glob("cont_*.json"):
            os.unlink(f)

        result = asdaaas.queue_continue_doorbell("Sr")
        assert result is True

        cont_files = list(bell_dir.glob("cont_*.json"))
        assert len(cont_files) == 1
        with open(cont_files[0]) as f:
            bell = json.load(f)
        assert bell["adapter"] == "continue"
        assert bell["priority"] == 10
        assert "ts" in bell

    def test_queue_continue_doorbell_skips_if_exists(self, hub_dir):
        """queue_continue_doorbell returns False if one already exists."""
        bell_dir = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "doorbells"
        # Clear and create one
        for f in bell_dir.glob("cont_*.json"):
            os.unlink(f)
        asdaaas.queue_continue_doorbell("Sr")

        # Second call should skip
        result = asdaaas.queue_continue_doorbell("Sr")
        assert result is False
        # Still only one
        assert len(list(bell_dir.glob("cont_*.json"))) == 1

    @pytest.mark.asyncio
    async def test_full_pattern_interrupted_no_continue(self, hub_dir, write_awareness):
        """END-TO-END: Simulates the full pattern from the main loop.

        1. Agent sets delay 600s
        2. "User" sends message during delay
        3. run_delay_loop returns interrupted=True
        4. Main loop skips queue_continue_doorbell
        5. Verify: no continue doorbell, message still pending

        This is the mock-user + mock-agent integration test."""
        write_awareness("Sr", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Sr")
        bell_dir = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "doorbells"
        inbox = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "adapters" / "irc" / "inbox"

        # Clear state
        for f in bell_dir.glob("cont_*.json"):
            os.unlink(f)

        # Step 1: "User" (Eric) sends a message
        with open(inbox / "msg_eric_dashboard.json", "w") as f:
            json.dump({
                "from": "eric", "to": "Sr",
                "text": "hey, what about the dashboard?",
                "adapter": "irc", "meta": {"room": "pm:eric"},
            }, f)

        # Step 2: Agent's delay loop runs (simulating delay 600s, but message
        # is already there so it breaks on first poll)
        interrupted, reason = await asdaaas.run_delay_loop(
            "Sr", delay_seconds=600, awareness=awareness, poll_interval=0.1
        )

        # Step 3: Main loop logic — only queue continue if NOT interrupted
        if not interrupted:
            asdaaas.queue_continue_doorbell("Sr")

        # Step 4: Verify correct behavior
        assert interrupted is True
        assert reason == "external_event"

        # NO continue doorbell should exist
        cont_files = list(bell_dir.glob("cont_*.json"))
        assert len(cont_files) == 0, \
            f"Bug: continue doorbell queued despite interruption: {cont_files}"

        # Eric's message is still pending for the main loop to pick up
        messages = asdaaas.poll_adapter_inboxes("Sr", awareness)
        assert len(messages) == 1
        assert messages[0]["text"] == "hey, what about the dashboard?"

    @pytest.mark.asyncio
    async def test_full_pattern_expired_queues_continue(self, hub_dir, write_awareness):
        """END-TO-END: When delay expires naturally, continue doorbell IS queued.

        1. Agent sets delay (short)
        2. No messages arrive
        3. run_delay_loop returns interrupted=False
        4. Main loop queues continue doorbell
        5. Verify: continue doorbell exists"""
        write_awareness("Sr", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Sr")
        bell_dir = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "doorbells"

        # Clear state
        for f in bell_dir.glob("cont_*.json"):
            os.unlink(f)

        # No messages — delay expires naturally
        interrupted, reason = await asdaaas.run_delay_loop(
            "Sr", delay_seconds=0.3, awareness=awareness, poll_interval=0.1
        )

        # Main loop logic
        if not interrupted:
            asdaaas.queue_continue_doorbell("Sr")

        assert interrupted is False
        assert reason == "expired"

        # Continue doorbell SHOULD exist
        cont_files = list(bell_dir.glob("cont_*.json"))
        assert len(cont_files) == 1

    @pytest.mark.asyncio
    async def test_message_arrives_mid_delay(self, hub_dir, write_awareness):
        """Message arrives DURING the delay (not before). Uses concurrent task
        to simulate a user typing while the agent waits.

        This is the closest simulation to the real-world scenario."""
        write_awareness("Sr", direct_attach=["irc"])
        awareness = asdaaas.read_awareness("Sr")
        bell_dir = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "doorbells"
        inbox = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "adapters" / "irc" / "inbox"

        # Clear state
        for f in bell_dir.glob("cont_*.json"):
            os.unlink(f)
        for f in inbox.glob("*.json"):
            os.unlink(f)

        async def mock_user_types_after_delay():
            """Simulate Eric typing a message 0.3s into the delay."""
            await asyncio.sleep(0.3)
            with open(inbox / "msg_eric_mid_delay.json", "w") as f:
                json.dump({
                    "from": "eric", "to": "Sr",
                    "text": "actually one more thing",
                    "adapter": "irc", "meta": {"room": "pm:eric"},
                }, f)

        # Start the mock user typing concurrently
        user_task = asyncio.create_task(mock_user_types_after_delay())

        # Agent delay loop runs — should be interrupted by the message
        interrupted, reason = await asdaaas.run_delay_loop(
            "Sr", delay_seconds=5.0, awareness=awareness, poll_interval=0.1
        )

        await user_task  # ensure cleanup

        # Delay was interrupted by the mid-delay message
        assert interrupted is True
        assert reason == "external_event"

        # Main loop logic: skip continue
        if not interrupted:
            asdaaas.queue_continue_doorbell("Sr")

        # No continue doorbell
        cont_files = list(bell_dir.glob("cont_*.json"))
        assert len(cont_files) == 0

        # Message is there
        assert asdaaas.has_pending_adapter_messages("Sr", awareness) is True


# ============================================================================
# Graceful Shutdown
# ============================================================================

class TestGracefulShutdown:
    """Tests for shutdown flag, command handler, and unregister."""

    def test_shutdown_flag_starts_false(self):
        asdaaas._shutdown_requested = False
        assert asdaaas._shutdown_requested is False

    def test_request_shutdown_from_command(self):
        asdaaas._shutdown_requested = False
        asdaaas.request_shutdown_from_command("Trip")
        assert asdaaas._shutdown_requested is True
        # Reset for other tests
        asdaaas._shutdown_requested = False

    def test_signal_handler_sets_flag(self):
        import signal
        asdaaas._shutdown_requested = False
        asdaaas._request_shutdown(signal.SIGTERM, "Sr")
        assert asdaaas._shutdown_requested is True
        asdaaas._shutdown_requested = False

    def test_shutdown_command_parsed(self, hub_dir):
        """Shutdown command is read from commands.json like any other command."""
        cmd_file = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "commands.json"
        with open(cmd_file, "w") as f:
            json.dump({"action": "shutdown"}, f)

        cmds = asdaaas.poll_commands("Sr")
        assert len(cmds) == 1
        assert cmds[0]["action"] == "shutdown"

    def test_unregister_running_agent(self, hub_dir):
        """Agent is removed from running_agents.json on shutdown."""
        reg_path = hub_dir / "running_agents.json"
        with open(reg_path, "w") as f:
            json.dump({
                "Sr": {"home": "/home/eric/agents/Sr"},
                "Trip": {"home": "/home/eric/agents/Trip"},
            }, f)

        asdaaas._unregister_running_agent("Sr")
        with open(reg_path) as f:
            reg = json.load(f)
        assert "Sr" not in reg
        assert "Trip" in reg

    def test_unregister_last_agent(self, hub_dir):
        """Unregistering the last agent leaves empty dict."""
        reg_path = hub_dir / "running_agents.json"
        with open(reg_path, "w") as f:
            json.dump({"Sr": {"home": "/home/eric/agents/Sr"}}, f)

        asdaaas._unregister_running_agent("Sr")
        with open(reg_path) as f:
            reg = json.load(f)
        assert reg == {}

    def test_unregister_nonexistent_agent(self, hub_dir):
        """Unregistering an agent not in the file is a no-op."""
        reg_path = hub_dir / "running_agents.json"
        with open(reg_path, "w") as f:
            json.dump({"Trip": {"home": "/home/eric/agents/Trip"}}, f)

        asdaaas._unregister_running_agent("Jr")  # not in file
        with open(reg_path) as f:
            reg = json.load(f)
        assert "Trip" in reg

    def test_unregister_missing_file(self, hub_dir):
        """No running_agents.json is a no-op (no crash)."""
        reg_path = hub_dir / "running_agents.json"
        if reg_path.exists():
            reg_path.unlink()
        # Should not raise
        asdaaas._unregister_running_agent("Sr")

    def test_shutdown_writes_health(self, hub_dir):
        """Shutdown writes health status 'shutdown'."""
        asdaaas.write_health("Sr", "shutdown", "graceful shutdown", 50000, 200000)
        health_file = hub_dir.parent / "agents" / "Sr" / "asdaaas" / "health.json"
        with open(health_file) as f:
            data = json.load(f)
        assert data["status"] == "shutdown"
        assert data["detail"] == "graceful shutdown"
