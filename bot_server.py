#!/usr/bin/env python3
"""
Scout Slackbot Server
FastAPI-based webhook handler for Slack slash commands and natural language messages.
Handles the 3-second Slack timeout via async background processing.
"""

import os
import sys
import hmac
import hashlib
import time
import threading
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Add scout directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from command_parser import parse_command, parse_natural_language
from formatters import format_csat, format_voc, format_errors, format_help, format_not_available
from queries.csat import run_csat
from queries.voc import run_voc
from queries.errors import run_errors
from nl_router import route_natural_language, build_command_from_routing

load_dotenv("/home/ubuntu/scout/.env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("scout")

app = FastAPI(title="Scout Slackbot", version="1.0.0")

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]

slack_client = WebClient(token=SLACK_BOT_TOKEN)


# ---------------------------------------------------------------------------
# Slack request signature verification
# ---------------------------------------------------------------------------

def verify_slack_signature(request_body: bytes, timestamp: str, signature: str) -> bool:
    """Verify that the request genuinely came from Slack."""
    # Reject requests older than 5 minutes
    if abs(time.time() - int(timestamp)) > 300:
        return False
    sig_basestring = f"v0:{timestamp}:{request_body.decode('utf-8')}"
    computed = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    # Note: hmac.new is the correct call in Python's hmac module
    return hmac.compare_digest(computed, signature)


# ---------------------------------------------------------------------------
# Core Scout execution
# ---------------------------------------------------------------------------

def execute_scout_command(raw_input: str) -> str:
    """Parse and execute a Scout command. Returns formatted Slack message."""
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

    if cmd.command in ("nps", "returns", "reviews"):
        return format_not_available(cmd.command)

    return f"Unknown command `{cmd.command}`. Try `/scout-help` for available commands."


def execute_natural_language(text: str) -> str:
    """Route a natural language message to the appropriate Scout command using LLM."""
    # Use LLM router first, fall back to keyword matching
    routing = route_natural_language(text)
    command_str = build_command_from_routing(routing)
    logger.info(f"NL '{text}' → '{command_str}' (confidence: {routing.get('confidence')})")
    return execute_scout_command(command_str)


# ---------------------------------------------------------------------------
# Async response helpers
# ---------------------------------------------------------------------------

def post_to_response_url(response_url: str, message: str):
    """Post the final result back to Slack via the response_url (async)."""
    payload = {
        "response_type": "in_channel",
        "text": message,
    }
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(response_url, json=payload)
            resp.raise_for_status()
            logger.info(f"Posted result to response_url: {resp.status_code}")
    except Exception as e:
        logger.error(f"Failed to post to response_url: {e}")


def post_to_channel(channel_id: str, message: str, thread_ts: Optional[str] = None):
    """Post a message to a Slack channel via the bot token."""
    try:
        kwargs = {"channel": channel_id, "text": message, "mrkdwn": True}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        slack_client.chat_postMessage(**kwargs)
        logger.info(f"Posted to channel {channel_id}")
    except SlackApiError as e:
        logger.error(f"Slack API error posting to {channel_id}: {e.response['error']}")


def run_command_async(raw_input: str, response_url: str, channel_id: str,
                      user_id: str, thread_ts: Optional[str] = None):
    """Execute Scout command in background thread and post result."""
    logger.info(f"Running command: {raw_input} for user {user_id}")
    try:
        result = execute_scout_command(raw_input)
    except Exception as e:
        logger.error(f"Error executing command '{raw_input}': {e}")
        result = (
            f"Scout hit an error processing `{raw_input}`.\n"
            f"Error: `{str(e)[:200]}`\n"
            f"Try `/scout-help` for available commands."
        )
    post_to_response_url(response_url, result)


def run_nl_async(text: str, channel_id: str, user_id: str, thread_ts: Optional[str] = None):
    """Execute natural language query in background thread and post result."""
    logger.info(f"Running NL query: '{text}' for user {user_id}")
    try:
        result = execute_natural_language(text)
    except Exception as e:
        logger.error(f"Error executing NL query '{text}': {e}")
        result = (
            f"Scout hit an error processing your question.\n"
            f"Error: `{str(e)[:200]}`\n"
            f"Try `/scout-help` for available commands."
        )
    post_to_channel(channel_id, result, thread_ts=thread_ts)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "scout"}


# ---------------------------------------------------------------------------
# Slash command handler: /scout
# ---------------------------------------------------------------------------

@app.post("/slack/commands/scout")
async def handle_scout_command(
    request: Request,
    background_tasks: BackgroundTasks,
    command: str = Form(default=""),
    text: str = Form(default=""),
    user_id: str = Form(default=""),
    channel_id: str = Form(default=""),
    response_url: str = Form(default=""),
):
    # Verify Slack signature
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    # Normalize: combine command + text into a single input string
    # e.g. command="/scout" text="CSAT L30" → "/CSAT L30"
    # e.g. command="/csat" text="L30" → "/CSAT L30"
    cmd_name = command.lstrip("/").upper()  # e.g. "SCOUT", "CSAT", "VOC"

    if cmd_name == "SCOUT":
        # /scout CSAT L30  or  /scout VOC L7  or  /scout help
        raw_input = f"/{text.strip()}" if text.strip() else "/Help"
    else:
        # Direct slash commands like /csat L30, /voc L7
        raw_input = f"/{cmd_name} {text.strip()}".strip()

    logger.info(f"Slash command from {user_id} in {channel_id}: {raw_input}")

    # Immediately acknowledge (Slack requires response within 3 seconds)
    ack_message = f":mag: Scout is pulling your data for `{raw_input}`..."

    # Run the actual query in background
    background_tasks.add_task(
        run_command_async,
        raw_input=raw_input,
        response_url=response_url,
        channel_id=channel_id,
        user_id=user_id,
    )

    return JSONResponse(content={
        "response_type": "ephemeral",
        "text": ack_message,
    })


# ---------------------------------------------------------------------------
# Slash command handlers: /csat, /voc, /errors, /scout-help
# ---------------------------------------------------------------------------

@app.post("/slack/commands/csat")
async def handle_csat(request: Request, background_tasks: BackgroundTasks,
                      text: str = Form(default=""), user_id: str = Form(default=""),
                      channel_id: str = Form(default=""), response_url: str = Form(default="")):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")
    raw_input = f"/CSAT {text.strip()}".strip()
    background_tasks.add_task(run_command_async, raw_input=raw_input,
                               response_url=response_url, channel_id=channel_id, user_id=user_id)
    return JSONResponse(content={"response_type": "ephemeral",
                                  "text": f":mag: Scout is pulling CSAT data..."})


@app.post("/slack/commands/voc")
async def handle_voc(request: Request, background_tasks: BackgroundTasks,
                     text: str = Form(default=""), user_id: str = Form(default=""),
                     channel_id: str = Form(default=""), response_url: str = Form(default="")):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")
    raw_input = f"/VOC {text.strip()}".strip()
    background_tasks.add_task(run_command_async, raw_input=raw_input,
                               response_url=response_url, channel_id=channel_id, user_id=user_id)
    return JSONResponse(content={"response_type": "ephemeral",
                                  "text": f":mag: Scout is pulling VOC data..."})


@app.post("/slack/commands/errors")
async def handle_errors(request: Request, background_tasks: BackgroundTasks,
                        text: str = Form(default=""), user_id: str = Form(default=""),
                        channel_id: str = Form(default=""), response_url: str = Form(default="")):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")
    raw_input = f"/Errors {text.strip()}".strip()
    background_tasks.add_task(run_command_async, raw_input=raw_input,
                               response_url=response_url, channel_id=channel_id, user_id=user_id)
    return JSONResponse(content={"response_type": "ephemeral",
                                  "text": f":mag: Scout is analyzing error tickets..."})


@app.post("/slack/commands/scout-help")
async def handle_help(request: Request,
                      user_id: str = Form(default=""),
                      response_url: str = Form(default="")):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")
    return JSONResponse(content={
        "response_type": "ephemeral",
        "text": format_help(),
    })


# ---------------------------------------------------------------------------
# Event handler: app_mention and direct messages (natural language)
# ---------------------------------------------------------------------------

@app.post("/slack/events")
async def handle_events(request: Request, background_tasks: BackgroundTasks):
    body_bytes = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(body_bytes, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    import json
    payload = json.loads(body_bytes)

    # URL verification challenge (required during Slack app setup)
    if payload.get("type") == "url_verification":
        return JSONResponse(content={"challenge": payload["challenge"]})

    event = payload.get("event", {})
    event_type = event.get("type")

    # Handle app_mention: @Scout what is our CSAT this week?
    if event_type == "app_mention":
        text = event.get("text", "")
        channel_id = event.get("channel")
        user_id = event.get("user")
        thread_ts = event.get("thread_ts") or event.get("ts")

        # Strip the bot mention from the text
        import re
        text_clean = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        logger.info(f"app_mention from {user_id} in {channel_id}: '{text_clean}'")

        # Post acknowledgment immediately
        try:
            slack_client.chat_postMessage(
                channel=channel_id,
                text=":mag: Scout is on it...",
                thread_ts=thread_ts,
                mrkdwn=True,
            )
        except SlackApiError as e:
            logger.error(f"Failed to post ack: {e}")

        background_tasks.add_task(
            run_nl_async,
            text=text_clean,
            channel_id=channel_id,
            user_id=user_id,
            thread_ts=thread_ts,
        )

    # Handle direct messages to the bot
    elif event_type == "message" and event.get("channel_type") == "im" and not event.get("bot_id"):
        text = event.get("text", "").strip()
        channel_id = event.get("channel")
        user_id = event.get("user")
        thread_ts = event.get("ts")

        if not text:
            return JSONResponse(content={"ok": True})

        logger.info(f"DM from {user_id}: '{text}'")

        try:
            slack_client.chat_postMessage(
                channel=channel_id,
                text=":mag: Scout is on it...",
                thread_ts=thread_ts,
                mrkdwn=True,
            )
        except SlackApiError as e:
            logger.error(f"Failed to post DM ack: {e}")

        background_tasks.add_task(
            run_nl_async,
            text=text,
            channel_id=channel_id,
            user_id=user_id,
            thread_ts=thread_ts,
        )

    return JSONResponse(content={"ok": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting Scout bot server on port {port}")
    uvicorn.run("bot_server:app", host="0.0.0.0", port=port, reload=False)
