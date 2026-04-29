# Scout Slack App — Socket Mode Setup Guide

Scout is now running in Socket Mode. The bot is live and connected to Slack via a persistent WebSocket connection — no public URL or hosting required.

The only remaining step is to register the slash commands in the Slack App dashboard so employees can use them.

---

## Register Slash Commands

Go to [api.slack.com/apps](https://api.slack.com/apps), select your Scout app, and click **Slash Commands** in the left sidebar.

For each command below, click **Create New Command** and fill in the fields as shown. Because Socket Mode is enabled, you do **not** need to enter a Request URL — Slack will route the payloads over the WebSocket connection automatically.

| Command | Description | Usage Hint |
|---|---|---|
| `/scout` | Ask Scout anything (routes to the right data source) | `[CSAT\|VOC\|Errors\|help] [L7\|L30\|L180]` |
| `/csat` | Get CSAT performance | `[L7\|L30\|L180]` |
| `/voc` | Get Voice of Customer summary | `[L7\|L30\|L180]` |
| `/errors` | Get error-related ticket analysis | `[L7\|L30\|L180]` |
| `/scout-help` | See all available Scout commands | — |

---

## Enable Event Subscriptions (for Natural Language)

To allow employees to talk to Scout naturally (e.g., `@Scout what are our top complaints this month?`), enable Event Subscriptions:

1. In the left sidebar, click **Event Subscriptions**.
2. Toggle **Enable Events** to On.
3. Under **Subscribe to bot events**, click **Add Bot User Event** and add:
   - `app_mention` — so employees can @mention Scout in any channel
   - `message.im` — so employees can DM Scout directly
4. Click **Save Changes**.
5. If prompted, click **Reinstall to Workspace** to apply the new permissions.

---

## Required Bot Token Scopes

Confirm the following scopes are present under **OAuth & Permissions → Scopes → Bot Token Scopes**:

- `commands`
- `chat:write`
- `app_mentions:read`
- `im:history`
- `im:read`
- `im:write`

---

## How Employees Use Scout

Once the slash commands are registered and events are enabled, any employee can:

**Slash commands (structured):**
```
/csat L30
/voc L7
/errors L30
/scout-help
```

**Natural language (via @mention in any channel):**
```
@Scout what is our CSAT this week?
@Scout show me top customer complaints last month
@Scout are there any checkout errors?
```

**Natural language (via DM to the Scout bot):**
```
what are customers saying about returns?
show me CSAT for the last 30 days
```

Scout will immediately reply with ":mag: Scout is on it..." and post the full data-backed report a few seconds later once the Snowflake query completes.

---

## Keeping Scout Running

The bot process is currently running in this sandbox session. For a permanent deployment, run `scout_bot.py` on any always-on machine (a team server, an EC2 instance, a Heroku worker, etc.) using:

```bash
cd /path/to/scout
python3 scout_bot.py
```

The bot will automatically reconnect if the WebSocket drops.
