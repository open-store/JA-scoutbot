# Slack app setup

Scout uses Socket Mode. Slack delivers events over a WebSocket. The app does not need a public request URL.

Create or select the app at [api.slack.com/apps](https://api.slack.com/apps).

## Socket Mode

Enable Socket Mode. The app-level token needs the `connections:write` scope. Put that token in `SLACK_APP_TOKEN`.

## Slash commands

Create each command under **Slash Commands**. Leave the request URL empty. Socket Mode routes the payload.

| Command | Description | Usage hint |
|---|---|---|
| `/scout` | Ask Scout anything | `[question or command]` |
| `/csat` | CSAT performance | `[L7\|L30\|L180]` |
| `/voc` | Voice of Customer summary | `[L7\|L30\|L180] [product:"name"]` |
| `/errors` | Error-related ticket analysis | `[L7\|L30\|L180]` |
| `/nps` | NPS by survey | `[L7\|L30\|L180]` |
| `/pps` | Returning-customer post-purchase survey | `[L7\|L30\|L180]` |
| `/attribution` | How new customers found the brand | `[L7\|L30\|L180]` |
| `/returns` | Return volume and mix | `[L7\|L30\|L180] [product:"name"]` |
| `/reviews` | Okendo review summary | `[L7\|L30\|L180] [product:"name"]` |
| `/daily` | Today's CS KPIs | — |
| `/scout-help` | Command reference | — |
| `/help` | Alias of `/scout-help` | — |

## Events

Under **Event Subscriptions**, turn events on. With Socket Mode you do not enter a request URL.

Subscribe to bot events:

- `app_mention` — @Scout in channels
- `message.im` — direct messages

Under **App Home → Messages Tab**, allow users to send slash commands and messages from the messages tab so DMs work.

Reinstall the app to the workspace after scope or event changes.

## Bot token scopes

Under **OAuth & Permissions → Bot Token Scopes**:

- `commands`
- `chat:write`
- `app_mentions:read`
- `im:history`
- `im:read`
- `im:write`

The bot token goes in `SLACK_BOT_TOKEN`. The signing secret goes in `SLACK_SIGNING_SECRET`.

## How people use it

Slash commands:

```
/csat L30
/voc L7 product:"anytime crewneck"
/nps L30
/attribution L30
/daily
/scout-help
```

@mention in a channel, or send a DM:

```
what is our CSAT this week?
show me top customer complaints last month
how are people finding us?
```

Scout replies `:mag: Scout is on it...` and then posts the report. Channel answers stay in the thread.

Running the process on the VM is covered in [deployment](deployment.md).
