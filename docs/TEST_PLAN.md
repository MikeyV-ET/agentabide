# ASDAAAS Test Coverage Overview

**Last updated:** 2026-04-06
**Total tests:** 534 (0 regressions)
**Production code:** ~10,274 lines across 17 files in `live/comms/`
**Test code:** ~6,685 lines across 9 test files + conftest + mock_agent

---

## 1. Coverage Overview

### Source Files (live/comms/)

| File | Lines | Test File | Tests | Notes |
|------|------:|-----------|------:|-------|
| `asdaaas.py` | 2,221 | `test_asdaaas.py` | 220 | Core engine. Best covered |
| `adapter_api.py` | 1,119 | `test_adapter_api.py` | 58 | Filesystem message passing |
| `irc_adapter.py` | 628 | `test_irc_adapter.py` | 40 | Extractable functions only |
| `localmail.py` | 342 | `test_localmail.py` | 28 | Agent-to-agent messaging |
| `remind_adapter.py` | 264 | `test_remind_adapter.py` | 20 | Self-nudge control adapter |
| `tmux_control.py` | 364 | `test_tmux_control.py` | 23 | Tmux session management |
| `context_adapter.py` | 339 | `test_phases_4_7.py` | 93* | Context threshold tracking |
| `session_adapter.py` | 323 | `test_phases_4_7.py` | shared | Compact/status doorbells |
| `heartbeat_adapter.py` | 344 | `test_phases_4_7.py` | shared | Idle tracking and nudges |
| `slack_adapter.py` | 695 | -- | 0 | **No tests** |
| `irc_bridge.py` | 252 | -- | 0 | **No tests** |
| `reliable_send.py` | 292 | -- | 0 | **No tests** |
| `control_adapter_template.py` | 253 | -- | 0 | **No tests** |
| `impress_control_adapter.py` | 969 | -- | 0 | **No tests** |
| `meet_control_adapter.py` | 1,243 | -- | 0 | **No tests** |
| `slack_research_adapter.py` | 385 | -- | 0 | **No tests** |
| `health_check.py` | 221 | -- | 0 | **No tests** |
| `irc_agent.py` | 338 | -- | 0 | **No tests** |
| `mikeyv_hub.py` | 1,234 | -- | 0 | **No tests** (legacy hub) |
| `leader_callback_client.py` | 466 | -- | 0 | **No tests** (legacy) |
| `leader_crash_test.py` | 323 | -- | 0 | Test harness |
| `stress_test_adapter.py` | 244 | -- | 0 | Test harness |

*`test_phases_4_7.py` (93 tests) covers context_adapter, session_adapter, heartbeat_adapter, CommandWatchdog, gaze matching, PendingQueue, and adapter registration.

### Test Files

| File | Lines | Tests | Scope |
|------|------:|------:|-------|
| `test_asdaaas.py` | 2,886 | 220 | Core: gaze, awareness, outbox, inbox, doorbells, commands, health, profiling, JSON-RPC, attention, collect_response, streaming thoughts, context tags, default doorbell, delay, piggyback ack, graceful shutdown, gaze command, awareness command |
| `test_phases_4_7.py` | 853 | 93 | CommandWatchdog, gaze matching, background mode, PendingQueue, context thresholds, session adapter, heartbeat idle tracker, adapter registration, per-agent prefs |
| `test_adapter_api.py` | 572 | 58 | write_message, poll_responses, per-adapter inbox/outbox, adapter registration, payloads, attention declarations, request_compact, set_gaze, set_awareness |
| `test_e2e.py` | 464 | 18 | Multi-agent multi-user end-to-end: gaze routing, background channels, PM isolation, meeting scenarios, localmail |
| `test_integration.py` | 466 | 34 | MockAgent: collect_response, speech/thoughts assembly, tool calls, context tags, prompt construction, doorbell format |
| `test_irc_adapter.py` | 265 | 40 | clean_response, parse_irc_commands, MessageBatcher, nick suppression, thought channels |
| `test_localmail.py` | 262 | 28 | send_mail, read_mail, peek_mail, ring_doorbell, get_asdaaas_agents, round-trip integration |
| `test_remind_adapter.py` | 230 | 20 | deliver_doorbell, TimerPool, process_command, integration with asdaaas doorbell format |
| `test_tmux_control.py` | 215 | 23 | TmuxSession lifecycle, send/capture, wait_for, dead session errors, list_sessions (requires tmux) |
| `conftest.py` | 225 | -- | Shared fixtures: hub_dir, write_gaze, write_awareness, write_attention_file, write_health |

---

## 2. Feature-to-Test Mapping

### Gaze (agent attention direction)
- **TestReadGaze** (5 tests): split format, null thoughts, legacy format, missing file defaults, corrupt JSON defaults
- **TestWriteToOutbox** (5 tests): per-adapter routing, thoughts content type, null target discards, params passthrough, creates outbox dir
- **TestMatchesGaze** (11 tests): room match/mismatch, PM rooms, adapter mismatch, no room in gaze/msg, null speech, Slack DMs, mesh agents
- **TestGazeLabel** (6 tests): IRC PM, IRC channel, Slack, no speech, empty gaze, adapter-only
- **TestSetGaze** (4 tests): writes file, PM rooms, custom thoughts, overwrites
- **TestGazeConstruction** (1 test): default gaze used for thoughts

### Awareness (what the agent listens to)
- **TestReadAwareness** (3 tests): custom awareness, missing file defaults, corrupt JSON defaults
- **TestGetBackgroundMode** (6 tests): explicit doorbell, explicit drop, default fallback, default when unset, PM room as key, no room
- **TestSetAwareness** (3 tests): writes file, drop default, multiple channels
- **TestFormatBackgroundDoorbell** (3 tests): with room, without room, long text truncated

### Doorbells (persistent notifications)
- **TestPollDoorbells** (12 tests): reads and persists, delivery count increment, ID from filename, ID preserved, TTL expiry, TTL=0 persists, TTL per source, TTL default fallback, no awareness no TTL, priority ordering, default priority, empty dir
- **TestFormatDoorbell** (6 tests): with command, without command, includes ID, delivery count on redelivery, no count on first, no ID no meta
- **TestAckDoorbells** (7 tests): removes matching, preserves unmatched, multiple ack, nonexistent ID, empty list, uses ID from file, no dir
- **TestHasPendingDoorbells** (3 tests): has pending, no pending, empty dir

### Commands (agent-to-engine control)
- **TestPollCommands** (7 tests): legacy file, no command, consumed on read, queue directory, legacy before queue, write_command helper, ordering
- **TestHasPendingCommands** (6 tests): detects pending, no pending, non-destructive, survives for poll, queue directory, either source

### Default Doorbell and Delay
- **TestDefaultDoorbell** (8 tests): delay parsed, until_event, consumed on read, awareness flag present/absent, continue format, lowest priority, delay zero, TTL in awareness, delay coexists with compact

### Piggyback Ack
- **TestPiggybackAck** (5 tests): delay+ack preserves both, ack clears doorbells, no ack is noop, compact with ack, queue solves single-slot race

### Inbox/Outbox (message passing)
- **TestWriteMessage** (10 tests): creates JSON, returns UUID, custom ID, sender defaults, custom sender, meta, expect_response, timestamp, unique files, atomic write
- **TestPollResponses** (6 tests): reads and deletes, no-delete option, chronological order, empty, skips corrupt, ignores tmp
- **TestWriteToAdapterInbox** (4 tests): correct path, sender defaults, creates dir, meta
- **TestPollAdapterInbox** (5 tests): reads and deletes, no-delete, empty, nonexistent agent, chronological
- **TestWriteToAdapterOutbox / TestPollAdapterOutbox** (4 tests): correct path, thoughts type, reads and deletes, empty
- **TestPollInbox** (5 tests, legacy): reads for agent, ignores others, reads broadcast, deletes after read, empty
- **TestPollAdapterInboxes** (6 tests): from direct_attach, multiple adapters, empty awareness, nonexistent dir, deletes after read, second poll empty
- **TestHasPendingAdapterMessages** (7 tests): detects pending, no pending, empty dir, nonexistent adapter, non-destructive, survives for poll, multiple adapters

### Attention (expect_response / timeout)
- **TestPollAttentions** (5 tests): empty dir, no dir, reads file, FIFO ordering, skips corrupt
- **TestCheckAttentionTimeouts** (3 tests): no timeout when fresh, expired produces timeout + deletes file, mixed
- **TestMatchAttention** (6 tests): by sender, wrong sender, case insensitive, FIFO match, different targets, empty
- **TestResolveAttention** (3 tests): creates response doorbell, truncates long response, deletes file
- **TestWriteAttention** (4 tests): creates file, truncates long text, creates dir, atomic write
- **TestSendWithAttention** (2 tests): creates message + attention, sender override

### Payloads (reference passing for large messages)
- **TestPayloads** (7 tests): write and read, read by path, nonexistent, format reference short/truncates, cleanup old, keeps recent

### Adapter Registration
- **TestAdapterRegistration** (4 tests in test_adapter_api): register, heartbeat, deregister, list
- **TestReadAdapterRegistrations** (5 tests in test_asdaaas): reads files, dead PID, skips dirs, corrupt JSON, empty
- **TestAdapterRegistration** (3 tests in test_phases_4_7): register and list, heartbeat update, deregister

### collect_response (stdio protocol parsing)
- **TestCollectResponseMeta** (6 tests): meta after prompt_complete, from streaming, zero when absent, updates throughout, on_meta callback, ID match without prompt_complete
- **TestCollectResponseKeepalive** (4 tests): resets on frame, fires on silence, tightens after prompt_complete, backward compat
- **TestCollectResponseStreamingCallbacks** (4 tests): on_speech_chunk fires per chunk, on_tool_call fires with title, multiple tool calls, no callbacks backward compat

### Streaming Thoughts
- **TestStreamingThoughts** (7 tests): accumulates and flushes on tool call, flush at end, null thoughts discards, multiple flushes, final speech not duplicated, empty buffer no write, chunk count

### Context Tags
- **TestContextLeftTag** (18 tests): basic format, large/small/very small/zero remaining, over threshold, zero window/tokens, boundary values, compaction status (just/1-turn/available/many/none), gaze in tag (IRC PM/channel/Slack/no status/just compacted/none/no speech/adapter only)

### Delay Interruption (regression tests for message drop bug)
- **TestDelayInterruptionPreservesMessages** (5 tests): has_pending then poll reads, old bug documented, multiple messages, command during delay, doorbell during delay
- **TestContinueDoorbellOnInterruption** (2 tests in test_asdaaas): continue doorbell written on delay interruption, not written when no delay

### Graceful Shutdown
- **TestGracefulShutdown** (8 tests): flag starts false, request from command, signal handler, shutdown command parsed, unregister running agent, unregister last/nonexistent/missing file, shutdown writes health

### Health and Profiling
- **TestWriteHealth** (1 test): creates health file with correct fields
- **TestMessageTimer** (4 tests): mark and elapsed, summary, log_line, missing marks
- **TestWriteProfile** (1 test): writes JSONL and latest file

### JSON-RPC Helpers
- **TestRpcHelpers** (3 tests): request format, notification no ID, request increments ID

### CommandWatchdog (control adapter timeout tracking)
- **TestCommandWatchdog** (7 tests): track and acknowledge, unknown returns false, expired returns timed out, not expired stays, delivers timeout doorbells, multiple independent, default timeout

### PendingQueue (room-based message queuing)
- **TestPendingQueue** (5 tests): add and drain, wrong room empty, PM room, multiple same room, multiple rooms independent

### Context Adapter (threshold tracking)
- **TestThresholdTracker** (9 tests): below first, fire at 45%, multiple at once, no double fire, reset after compaction, independent agents, zero window, all fire, priority ordering
- **TestContextDoorbellWriting** (1 test): ring_context_doorbell
- **TestContextHealthReading** (2 tests): read agent health, missing agent
- **TestContextThresholdPrefs** (10 tests): present, missing, empty, invalid, filters bad, level assignment, level_for_pct, tracker override, doesn't affect other, read awareness

### Session Adapter (compact/status)
- **TestSessionHandleStatus** (2 tests): returns health, missing agent
- **TestSessionDoorbellWriting** (3 tests): compact success, compact error, status
- **TestSessionInboxPolling** (2 tests): reads and deletes, empty
- **TestRequestCompact / TestRequestStatus** (3 tests): writes command, creates dir

### Heartbeat Adapter (idle nudging)
- **TestIdleTracker** (7 tests): no nudge when active, nudge when idle, no double nudge, no nudge when active again, interval survives reset, independent agents, missing last_activity
- **TestFormatIdleTime** (5 tests): seconds, minutes, one minute, hours, hours+minutes
- **TestHeartbeatDoorbellWriting** (1 test): ring_heartbeat_doorbell
- **TestHeartbeatPrefs** (10 tests): present, partial, missing, empty, invalid type, negative values, threshold override, nudge interval, read awareness, missing awareness

### Remind Adapter (self-nudge)
- **TestDeliverDoorbell** (4 tests): creates file, custom priority, timestamp, multiple
- **TestTimerPool** (5 tests): immediate, delayed, cleanup, fractional, multiple
- **TestProcessCommand** (8 tests): immediate, delayed, custom priority, unknown command, missing text, invalid delay, negative delay, default delay
- **TestIntegration** (3 tests): doorbell readable by asdaaas, priority ordering, agent writes via adapter_api

### IRC Adapter (extractable functions)
- **TestCleanResponse** (16 tests): normal text, strips headers (FROM/TO/VIA/bold/multiple), suppresses note/noted (case insensitive, with punctuation), does not suppress in sentence, empty/none/whitespace, long messages pass, header only
- **TestParseIrcCommands** (13 tests): nick, msg, join, part, me, mixed+text, no commands, empty, command only, multiline, msg no text, nick empty ignored, unknown slash
- **TestMessageBatcher** (6 tests): add and flush, batches within window, per-agent buckets, flush empties, nonexistent agent, quiet window resets
- **TestNickSuppression** (2 tests): all nicks present, case insensitive
- **TestThoughtChannels** (1 test): all agents have channels

### Localmail (agent-to-agent messaging)
- **TestSendMail** (8 tests): creates file, default/custom priority, meta, creates dir, multiple, timestamp, atomic
- **TestReadMail** (5 tests): reads and deletes, chronological, empty, nonexistent, skips corrupt
- **TestPeekMail** (2 tests): reads without deleting, empty
- **TestRingDoorbell** (5 tests): creates file, inline short, truncates long, preserves priority, atomic
- **TestGetAsdaaasAgents** (4 tests): detects healthy, excludes stale, excludes error, empty
- **TestRoundTrip** (4 tests): send then read, bidirectional, send and doorbell, TUI agent stays

### Tmux Control (session management)
- **TestTmuxSessionLifecycle** (5 tests): launch and kill, duplicate raises, context manager, kill nonexistent, exists before launch
- **TestSendAndCapture** (4 tests): send and capture, without enter, ctrl-c, multiline
- **TestWaitFor** (3 tests): immediate, timeout, stable
- **TestCapture** (2 tests): strips trailing, scrollback
- **TestOperationsOnDeadSession** (4 tests): send/capture/wait_for/send_keys all raise
- **TestListSessions** (2 tests): includes launched, empty
- **TestRepr** (3 tests): local, remote, alive

---

## 3. Known Gaps

### Untested Source Files (~7,000 lines with 0 tests)

| File | Lines | Risk | Notes |
|------|------:|------|-------|
| `slack_adapter.py` | 695 | **High** | Active development. Extractable: `parse_slack_commands()`, file download, emoji reactions, channel routing |
| `meet_control_adapter.py` | 1,243 | Medium | Control adapter for Google Meet. Needs mocking. Has extractable command parsing |
| `impress_control_adapter.py` | 969 | Medium | Control adapter for LibreOffice Impress. Has extractable UNO command parsing |
| `reliable_send.py` | 292 | Medium | Retry logic for message delivery. Has extractable pure functions |
| `control_adapter_template.py` | 253 | Medium | Base class for control adapters. Testing would cover meet/impress by proxy |
| `irc_bridge.py` | 252 | Low | IRC server connection. Needs network mocking |
| `health_check.py` | 221 | Low | Simple health file reader |
| `irc_agent.py` | 338 | Low | IRC agent wrapper. Mostly orchestration |
| `mikeyv_hub.py` | 1,234 | Low | Legacy hub, being replaced by asdaaas. Not worth investing |
| `leader_callback_client.py` | 466 | Low | Legacy leader protocol. Being deprecated |
| `slack_research_adapter.py` | 385 | Low | Research/exploration tool |

### Lightly Tested Areas

- **asdaaas.py main loop** -- The async `run_agent()` orchestration is not tested. Tests cover extractable functions only. The main loop handles: polling, prompt construction, gaze routing, delay handling, compaction, and the continue doorbell. Would require mocking the grok subprocess.
- **IRC adapter main loop** -- Connection handling, message routing, chunking, and the poll/post cycle are untested. Only extractable functions (clean_response, parse_irc_commands, MessageBatcher) are covered.
- **IRC message chunking** -- Messages exceeding 400 chars are chunked by the adapter. Chunking logic is untested.
- **Compaction flow end-to-end** -- Self-compaction command -> session adapter receives -> executes compact -> writes doorbell -> agent resumes. Doorbell writing is tested; the full flow is not.
- **Multi-agent hub-mediated delivery** -- Agent A sends to Agent B via the hub. Localmail round-trip is tested; hub-mediated delivery is not.
- **Gaze-routed speech delivery end-to-end** -- Agent speaks -> asdaaas reads gaze -> writes to correct adapter outbox -> adapter posts. Tested in pieces but not as a single flow.

### Missing Edge Cases

- **Concurrent writes** -- Multiple adapters writing to the same agent's inbox simultaneously. Atomic write (tmp + rename) is used but not stress-tested.
- **Filesystem permissions** -- Tests run in tmp_path with full permissions. Real deployment may hit permission issues.
- **Clock skew** -- Attention timeouts and heartbeat intervals depend on `time.time()`. Not tested with mocked clocks.

---

## 4. Test Conventions

### Framework
- **pytest** with `tmp_path` fixture for filesystem isolation
- All tests run without network, subprocess, or real filesystem access (except `test_tmux_control.py` which requires tmux and is auto-skipped if tmux is not installed)

### Fixtures (conftest.py)
- **`hub_dir`** -- Creates full agent-centric directory structure under `tmp_path`. Monkeypatches all module-level path constants (`ASDAAAS_DIR`, `AGENTS_HOME_DIR`, `HUB_DIR`, etc.) across asdaaas, adapter_api, localmail, context_adapter, session_adapter, heartbeat_adapter, and remind_adapter. Returns the engine dir.
- **`write_gaze(agent, speech_target, speech_params, thoughts_target, thoughts_params)`** -- Helper to write gaze files
- **`write_awareness(agent, direct_attach, control_watch, notify_watch, accept_from)`** -- Helper to write awareness files
- **`write_attention_file(agent, expecting_from, msg_id, timeout_s, created_at)`** -- Helper to write attention declarations
- **`write_health(agent, status, total_tokens)`** -- Helper to write health files

### Naming
- Test classes: `TestFeatureName` (e.g., `TestPollDoorbells`, `TestCleanResponse`)
- Test methods: `test_specific_behavior` (e.g., `test_reads_and_persists`, `test_suppresses_noted`)
- Descriptive docstrings on non-obvious tests

### Organization
- Tests grouped by feature, then by class/function
- Section headers with `# ===...===` separators
- Integration tests at the bottom of each file or in dedicated classes (e.g., `TestRoundTrip`, `TestIntegration`)

### Running Tests

```bash
# All tests (534)
cd ~/projects/mikeyv-infra
pytest tests/ -v

# Quick smoke test
pytest tests/ -x -q

# Specific file
pytest tests/test_asdaaas.py -v

# Specific class
pytest tests/test_asdaaas.py::TestPollDoorbells -v

# Skip tmux tests (if tmux not installed)
pytest tests/ -v --ignore=tests/test_tmux_control.py
```
