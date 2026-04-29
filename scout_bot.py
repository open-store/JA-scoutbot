#!/usr/bin/env python3
"""
Scout — Socket Mode Slackbot
Uses slack_bolt with Socket Mode — no public URL or hosting required.
Handles slash commands and natural language @mentions / DMs.
"""

import os
import sys
import re
import logging
import threading

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Add scout directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from formatters import format_help
from scout_service import execute_scout_command, execute_natural_language

load_dotenv("/home/ubuntu/scout/.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("scout")

# Initialise the Bolt app with the bot token and signing secret
app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)


# ---------------------------------------------------------------------------
# Core execution helpers
# ---------------------------------------------------------------------------

def run_in_background(fn, *args, **kwargs):
    """Fire-and-forget a function in a daemon thread."""
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------

def _handle_slash(command_text: str, ack, respond, command):
    """Generic slash command handler — ack immediately, respond async."""
    ack()  # Must acknowledge within 3 seconds
    user = command.get("user_id", "unknown")
    channel = command.get("channel_id", "unknown")
    logger.info(f"Slash '{command_text}' from {user} in {channel}")

    def _run():
        try:
            result = execute_scout_command(command_text)
        except Exception as e:
            logger.error(f"Error executing '{command_text}': {e}", exc_info=True)
            result = (
                f"Scout hit an error processing `{command_text}`.\n"
                f"Error: `{str(e)[:300]}`\n"
                "Try `/scout-help` for available commands."
            )
        respond({"response_type": "in_channel", "text": result})

    run_in_background(_run)


@app.command("/scout")
def handle_scout(ack, respond, command):
    """
    /scout <COMMAND> <TIMEFRAME>
    e.g. /scout CSAT L30  |  /scout VOC L7  |  /scout help
    """
    text = command.get("text", "").strip()
    raw_input = f"/{text}" if text else "/Help"
    _handle_slash(raw_input, ack, respond, command)


@app.command("/csat")
def handle_csat(ack, respond, command):
    text = command.get("text", "").strip()
    raw_input = f"/CSAT {text}".strip()
    _handle_slash(raw_input, ack, respond, command)


@app.command("/voc")
def handle_voc(ack, respond, command):
    text = command.get("text", "").strip()
    raw_input = f"/VOC {text}".strip()
    _handle_slash(raw_input, ack, respond, command)


@app.command("/errors")
def handle_errors(ack, respond, command):
    text = command.get("text", "").strip()
    raw_input = f"/Errors {text}".strip()
    _handle_slash(raw_input, ack, respond, command)


@app.command("/scout-help")
def handle_help(ack, respond, command):
    ack()
    respond({"response_type": "ephemeral", "text": format_help()})


@app.command("/help")
def handle_help_alias(ack, respond, command):
    """Alias: /help also shows Scout help (in case Slack routes it here)."""
    ack()
    respond({"response_type": "ephemeral", "text": format_help()})


# ---------------------------------------------------------------------------
# Natural language: @mentions in channels
# ---------------------------------------------------------------------------

@app.event("app_mention")
def handle_mention(event, say, client):
    """Handle @Scout mentions in channels."""
    raw_text = event.get("text", "")
    # Strip the bot mention tag
    text = re.sub(r"<@[A-Z0-9]+>", "", raw_text).strip()
    channel = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    user = event.get("user", "unknown")

    logger.info(f"@mention from {user} in {channel}: '{text}'")

    # Acknowledge immediately in thread
    ack_resp = say(
        text=":mag: Scout is on it...",
        thread_ts=thread_ts,
    )

    def _run():
        try:
            result, routing = execute_natural_language(text)
            confidence = routing.get("confidence", "?")
            logger.info(f"NL '{text}' → confidence: {confidence}")
        except Exception as e:
            logger.error(f"Error on NL query '{text}': {e}", exc_info=True)
            result = (
                "Scout hit an error processing your question.\n"
                f"Error: `{str(e)[:300]}`\n"
                "Try `/scout-help` for available commands."
            )
        client.chat_postMessage(
            channel=channel,
            text=result,
            thread_ts=thread_ts,
            mrkdwn=True,
        )

    run_in_background(_run)


# ---------------------------------------------------------------------------
# Natural language: Direct Messages to the bot
# ---------------------------------------------------------------------------

@app.event("message")
def handle_dm(event, say, client):
    """Handle direct messages to Scout."""
    # Only handle DMs (channel_type == "im"), skip bot messages
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id") or event.get("subtype"):
        return

    text = event.get("text", "").strip()
    channel = event.get("channel")
    thread_ts = event.get("ts")
    user = event.get("user", "unknown")

    if not text:
        return

    logger.info(f"DM from {user}: '{text}'")

    # Acknowledge immediately
    say(text=":mag: Scout is on it...", thread_ts=thread_ts)

    def _run():
        try:
            result, routing = execute_natural_language(text)
            confidence = routing.get("confidence", "?")
            logger.info(f"NL '{text}' → confidence: {confidence}")
        except Exception as e:
            logger.error(f"Error on DM query '{text}': {e}", exc_info=True)
            result = (
                "Scout hit an error processing your question.\n"
                f"Error: `{str(e)[:300]}`\n"
                "Try `/scout-help` for available commands."
            )
        client.chat_postMessage(
            channel=channel,
            text=result,
            thread_ts=thread_ts,
            mrkdwn=True,
        )

    run_in_background(_run)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Scout in Socket Mode...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
