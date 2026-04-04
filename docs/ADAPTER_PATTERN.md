# MikeyV Adapter Pattern

## Overview

The hub (mikeyv_hub.py) is evolving toward a **pure routing layer**. Transport-specific
code (IRC, Slack, voice, web) lives in standalone **adapter** processes that communicate
with the hub via a filesystem-based message bus (adapter_api.py).

## Architecture

```
  [IRC client]          [Slack API]          [Voice pipeline]
       |                     |                      |
  irc_adapter.py       slack_adapter.py       voice_adapter.py
       |                     |                      |
       +--------- adapter_api.py -------------------+
                   |                   |
            ~/asdaaas/inbox/   ~/asdaaas/outbox/<adapter>/
                   |                   |
              mikeyv_hub.py (routing layer)
                   |
            leader_callback_client.py
                   |
              [Agent sessions]
```

## Message Flow

### Inbound (user -> agent)
1. Adapter receives message from external transport (IRC PRIVMSG, Slack event, etc.)
2. Adapter calls `adapter_api.write_message(to, text, adapter, sender, meta)`
3. Hub polls `~/asdaaas/inbox/` every 500ms
4. Hub parses target agent, delivers via leader callback
5. Hub writes agent response to `~/asdaaas/outbox/<adapter>/`

### Outbound (agent -> user)
1. Hub writes response JSON to `~/asdaaas/outbox/<adapter>/`
2. Adapter polls its outbox via `adapter_api.poll_responses(adapter_name)`
3. Adapter posts response to external transport

## adapter_api.py Reference

| Function | Caller | Purpose |
|----------|--------|---------|
| `write_message(to, text, adapter, sender, meta)` | Adapter | Send message to hub inbox |
| `poll_responses(adapter_name, delete=True)` | Adapter | Read responses from outbox |
| `write_response(adapter_name, request_id, from_agent, text, meta)` | Hub | Write response to adapter outbox |
| `ensure_dirs(adapter_name)` | Both | Create required directories |

## Message Format

### Inbox message (adapter -> hub)
```json
{
  "id":      "uuid",
  "from":    "eric",
  "to":      "Sr",
  "text":    "message body",
  "adapter": "irc",
  "meta":    {"channel": "#standup", "nick": "eric"},
  "ts":      "2026-03-24T04:13:00"
}
```

### Outbox response (hub -> adapter)
```json
{
  "id":          "uuid",
  "request_id":  "original-msg-uuid",
  "from":        "Sr",
  "text":        "agent response text",
  "adapter":     "irc",
  "meta":        {},
  "ts":          "2026-03-24T04:13:05"
}
```

## Existing Adapters

### IRC Adapter (irc_adapter.py) -- RUNNING
- Nick: MikeyV-IRC
- Channel: #standup
- Connects to miniircd at 127.0.0.1:6667
- Parses target agent from message text (e.g., "Sr: hello" -> routes to Sr)
- Supports broadcast aliases ("gang", "everyone", "all", "team")
- Truncates long responses for IRC (line splitting)
- Batch window: 2s (collects multi-line responses before posting)

### Slack -- NOT YET EXTRACTED
Slack is still built into the hub (~150 lines in SlackClient class). See extraction plan below.

## Slack Adapter Extraction Plan

### What to extract from the hub
1. SlackClient class (lines ~163-275): token loading, API calls, DM polling, message posting
2. Slack polling loop in run_hub(): the slack_interval check and message processing
3. Slack CLI args: --no-slack, --slack-interval

### slack_adapter.py design
```
Standalone process, same pattern as irc_adapter.py:
1. Load Slack bot token from ~/.grok/creds/slack_bot_token
2. Poll Slack DM channel for new messages (conversations.history)
3. Parse target agent from message text
4. Write to hub inbox via adapter_api.write_message()
5. Poll hub outbox via adapter_api.poll_responses("slack")
6. Post responses to Slack DM via chat.postMessage
```

### Risk assessment
- LOW risk to build the adapter (it's additive)
- MEDIUM risk to remove Slack from the hub (breaks working system before hackathon)
- Recommendation: Build slack_adapter.py and test alongside hub's built-in Slack.
  Once verified, add --slack-adapter flag to hub that disables built-in Slack.
  Do NOT remove built-in Slack until after hackathon (March 28).

## Writing a New Adapter

1. Copy irc_adapter.py as a template
2. Replace transport-specific code (IRC socket -> your transport)
3. Use adapter_api.write_message() for inbound, adapter_api.poll_responses() for outbound
4. Add agent routing logic (parse target from message text)
5. Run as standalone process: setsid nohup python3 your_adapter.py > log 2>&1
6. Hub picks it up automatically via inbox polling -- no hub changes needed

## Collision Safety
- mkstemp() gives kernel-guaranteed unique filenames
- os.rename() is atomic on Linux
- Hub ignores .tmp files (only reads .json)
- Benchmarked: 109 microseconds per write+rename, 0 collisions in 1500-msg test
