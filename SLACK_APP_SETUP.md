# Scout Slack App Configuration Guide

The Scout bot server is now running and exposed publicly. To connect it to your Slack workspace so employees can use the slash commands and talk to the bot, you need to configure the Slack App settings.

## 1. Base Webhook URL

Your temporary public webhook URL is:
**`https://8000-inrage80sty8c0g8p7ean-10ed2051.us2.manus.computer`**

*(Note: This URL is temporary while the sandbox is running. For production, you will need to host the `bot_server.py` script on a platform like Heroku, AWS, or Render.)*

---

## 2. Configure Slash Commands

Go to your Slack App settings at [api.slack.com/apps](https://api.slack.com/apps), select the Scout app, and click **Slash Commands** in the left sidebar.

Create the following commands. For each one, use the exact Request URL provided below:

| Command | Request URL | Short Description |
|---|---|---|
| `/scout` | `https://8000-inrage80sty8c0g8p7ean-10ed2051.us2.manus.computer/slack/commands/scout` | Ask Scout a question (e.g., `/scout CSAT L30`) |
| `/csat` | `https://8000-inrage80sty8c0g8p7ean-10ed2051.us2.manus.computer/slack/commands/csat` | Get CSAT performance |
| `/voc` | `https://8000-inrage80sty8c0g8p7ean-10ed2051.us2.manus.computer/slack/commands/voc` | Get Voice of Customer summary |
| `/errors` | `https://8000-inrage80sty8c0g8p7ean-10ed2051.us2.manus.computer/slack/commands/errors` | Get error-related ticket analysis |
| `/scout-help` | `https://8000-inrage80sty8c0g8p7ean-10ed2051.us2.manus.computer/slack/commands/scout-help` | See available Scout commands |

---

## 3. Configure Event Subscriptions (Natural Language)

To allow employees to talk to Scout naturally (e.g., `@Scout what is our CSAT this week?`), you need to enable Event Subscriptions.

1. Go to **Event Subscriptions** in the left sidebar.
2. Toggle **Enable Events** to On.
3. In the **Request URL** field, enter:
   `https://8000-inrage80sty8c0g8p7ean-10ed2051.us2.manus.computer/slack/events`
4. Wait for it to say "Verified" (the server is already running and will respond to the challenge).
5. Under **Subscribe to bot events**, add the following events:
   - `app_mention` (Allows users to @mention the bot in channels)
   - `message.im` (Allows users to DM the bot directly)
6. Click **Save Changes**.

---

## 4. Required Bot Scopes

Ensure your bot has the following OAuth scopes under **OAuth & Permissions**:

- `commands` (Required for slash commands)
- `chat:write` (Required to post responses)
- `app_mentions:read` (Required for natural language mentions)
- `im:history` (Required for direct messages)
- `im:read` (Required for direct messages)

If you added any new scopes or events, Slack will prompt you to **Reinstall to Workspace**. Click the prompt to apply the changes.

---

## 5. Testing the Bot

Once configured, go to any Slack channel where the bot is invited (or DM the bot directly) and try:

1. **Slash command:** Type `/csat L7`
2. **Natural language:** Type `@Scout what are the top customer complaints this month?`

The bot will immediately reply with ":mag: Scout is on it..." and then post the full report a few seconds later once the Snowflake query finishes.
