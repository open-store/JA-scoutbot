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

from command_parser import parse_command
from formatters import format_csat, format_voc, format_errors, format_help, format_not_available, format_nps, format_reviews, format_returns
from queries.csat import run_csat
from queries.voc import run_voc
from queries.errors import run_errors
from queries.nps import run_nps
from queries.reviews import run_reviews
from queries.returns import run_returns
from nl_router import route_natural_language, build_command_from_routing

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

def execute_scout_command(raw_input: str) -> str:
    """Parse and execute a Scout command string. Returns formatted Slack text."""
    cmd = parse_command(raw_input)

    if not cmd.is_valid:
        return cmd.error_message

    if cmd.command == "help":
        return format_help()

    if cmd.command == "csat":
        data = run_csat(cmd)
        return format_csat(data)

    if cmd.command == "voc":
        data = run_voc(cmd)
        return format_voc(data)

    if cmd.command == "errors":
        data = run_errors(cmd)
        return format_errors(data)

    if cmd.command == "nps":
        data = run_nps(cmd.days)
        return format_nps(data)

    if cmd.command == "reviews":
        product = cmd.filters.get("product") if cmd.filters else None
        data = run_reviews(cmd.days, product_filter=product)
        return format_reviews(data)

    if cmd.command == "returns":
        product = cmd.filters.get("product") if cmd.filters else None
        data = run_returns(cmd.days, product_filter=product)
        return format_returns(data)

    return (
        f"Unknown command `{cmd.command}`.\n"
        "Try `/scout-help` to see what Scout can do."
    )


def execute_natural_language(text: str) -> str:
    """Route a natural language question to the right Scout command via LLM."""
    routing = route_natural_language(text)
    command_str = build_command_from_routing(routing)
    confidence = routing.get("confidence", "?")
    reasoning = routing.get("reasoning", "")
    logger.info(f"NL '{text}' → '{command_str}' (confidence: {confidence}, reasoning: {reasoning})")

    result = execute_scout_command(command_str)

    # Only show disambiguation note for genuinely low-confidence routing
    # Medium confidence means the LLM understood the intent but the query was conversational
    if confidence == "low":
        result = (
            f"_:thinking_face: I wasn't fully sure what you meant, so I ran `{command_str}`. "
            f"If this isn't right, try being more specific or use a slash command directly._\n\n"
            + result
        )

    return result


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


@app.command("/nps")
def handle_nps(ack, respond, command):
    text = command.get("text", "").strip()
    raw_input = f"/NPS {text}".strip()
    _handle_slash(raw_input, ack, respond, command)


@app.command("/returns")
def handle_returns(ack, respond, command):
    text = command.get("text", "").strip()
    raw_input = f"/Returns {text}".strip()
    _handle_slash(raw_input, ack, respond, command)


@app.command("/reviews")
def handle_reviews(ack, respond, command):
    text = command.get("text", "").strip()
    raw_input = f"/Reviews {text}".strip()
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
            result = execute_natural_language(text)
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
def handle_dm(event, client):
    """Handle direct messages to Scout — only responds to @mentions in DMs."""
    # Only handle DMs (channel_type == "im"), skip bot messages and subtypes
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id") or event.get("subtype"):
        return

    raw_text = event.get("text", "").strip()
    user = event.get("user", "unknown")

    # Only respond if the message contains an @mention of the bot.
    # This prevents Scout from responding to every message in the DM thread.
    bot_mention_pattern = re.compile(r"<@[A-Z0-9]+>")
    if not bot_mention_pattern.search(raw_text):
        logger.debug(f"DM from {user} ignored (no @mention): '{raw_text[:60]}'")
        return

    # Strip the @mention to get the clean query text
    text = bot_mention_pattern.sub("", raw_text).strip()

    if not text:
        return

    # Use the channel from the event directly — this is the correct DM channel
    # where the user sent the message, and where Scout should reply.
    dm_channel = event.get("channel")

    logger.info(f"DM @mention from {user} in {dm_channel}: '{text}'")

    # Acknowledge immediately in the same DM channel
    try:
        client.chat_postMessage(
            channel=dm_channel,
            text=":mag: Scout is on it...",
            mrkdwn=True,
        )
    except Exception as e:
        logger.error(f"Failed to send DM ack to {dm_channel}: {e}")

    def _run():
        try:
            result = execute_natural_language(text)
        except Exception as e:
            logger.error(f"Error on DM query '{text}': {e}", exc_info=True)
            result = (
                "Scout hit an error processing your question.\n"
                f"Error: `{str(e)[:300]}`\n"
                "Try `/scout-help` for available commands."
            )
        try:
            client.chat_postMessage(
                channel=dm_channel,
                text=result,
                mrkdwn=True,
            )
        except Exception as e:
            logger.error(f"Failed to send DM result to {dm_channel}: {e}")

    run_in_background(_run)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Scout in Socket Mode...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
