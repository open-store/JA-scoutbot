"""
Scout Command Parser
Parses slash commands and natural language queries into structured intents.
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from typing import Optional

BUSINESS_TZ = ZoneInfo("America/New_York")

VALID_COMMANDS = {"csat", "voc", "errors", "nps", "pps", "attribution", "returns", "reviews", "help"}

TIMEFRAME_PATTERN = re.compile(r"\bL(\d+)\b", re.IGNORECASE)

FILTER_PATTERNS = {
    "tag": re.compile(r'tag:(?:"([^"]+)"|(\S+))', re.IGNORECASE),
    "channel": re.compile(r'channel:(?:"([^"]+)"|(\S+))', re.IGNORECASE),
    "product": re.compile(r'product:(?:"([^"]+)"|(\S+))', re.IGNORECASE),
    "agent": re.compile(r'agent:(?:"([^"]+)"|(\S+))', re.IGNORECASE),
}


@dataclass
class ParsedCommand:
    """Represents a parsed Scout command."""
    command: str  # csat, voc, errors, nps, pps, attribution, returns, reviews, help
    days: int = 7  # number of days for the lookback window
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    filters: dict = field(default_factory=dict)
    raw_input: str = ""
    is_valid: bool = True
    error_message: str = ""
    source: str = ""  # which data source to use

    def __post_init__(self):
        if self.start_date is None or self.end_date is None:
            self._compute_dates()
        self._assign_source()

    def _compute_dates(self):
        now = datetime.now(BUSINESS_TZ)
        self.end_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.start_date = self.end_date - timedelta(days=self.days)

    def _assign_source(self):
        source_map = {
            "csat": "snowflake",
            "voc": "snowflake",
            "errors": "snowflake",
            "nps": "knocommerce",
            "pps": "knocommerce",
            "attribution": "knocommerce",
            "returns": "snowflake",
            "reviews": "okendo",
            "help": "none",
        }
        self.source = source_map.get(self.command, "snowflake")

    @property
    def timeframe_label(self) -> str:
        if self.start_date and self.end_date:
            fmt = "%b %d"
            end_display = self.end_date - timedelta(days=1)
            return f"{self.start_date.strftime(fmt)}–{end_display.strftime(fmt)}, {end_display.year}"
        return f"L{self.days}"

    @property
    def previous_start_date(self) -> datetime:
        return self.start_date - timedelta(days=self.days)

    @property
    def previous_end_date(self) -> datetime:
        return self.start_date


def parse_command(raw_input: str) -> ParsedCommand:
    """
    Parse a Scout command string into a structured ParsedCommand.
    
    Supports formats like:
        /CSAT L7
        /VOC L30 tag:shipping
        /Errors L7
        /Help
        CSAT L30
    """
    raw_input = raw_input.strip()
    
    # Remove leading slash if present
    text = raw_input.lstrip("/").strip()
    
    if not text:
        return ParsedCommand(
            command="help",
            raw_input=raw_input,
            is_valid=False,
            error_message="Empty command. Try `/Help` to see available commands."
        )
    
    # Extract the command (first token)
    tokens = text.split()
    cmd = tokens[0].lower()
    
    if cmd not in VALID_COMMANDS:
        return ParsedCommand(
            command="unknown",
            raw_input=raw_input,
            is_valid=False,
            error_message=f"Unknown command `{tokens[0]}`. Supported commands: `/CSAT`, `/VOC`, `/Errors`, `/NPS`, `/PPS`, `/Attribution`, `/Returns`, `/Reviews`, `/Help`."
        )
    
    # Handle /Help immediately
    if cmd == "help":
        return ParsedCommand(command="help", raw_input=raw_input)
    
    # Extract timeframe
    remaining = " ".join(tokens[1:])
    timeframe_match = TIMEFRAME_PATTERN.search(remaining)
    
    days = 7  # default
    if timeframe_match:
        days = int(timeframe_match.group(1))
        if days <= 0:
            return ParsedCommand(
                command=cmd,
                raw_input=raw_input,
                is_valid=False,
                error_message="I can run that, but I need a valid timeframe like `L7`, `L30`, or `L180`."
            )
    
    # Extract filters
    filters = {}
    for filter_name, pattern in FILTER_PATTERNS.items():
        match = pattern.search(remaining)
        if match:
            filters[filter_name] = match.group(1) or match.group(2)
    
    return ParsedCommand(
        command=cmd,
        days=days,
        filters=filters,
        raw_input=raw_input,
    )


def parse_natural_language(text: str) -> ParsedCommand:
    """
    Attempt to map a natural language question to a Scout command.
    Returns a ParsedCommand with best-guess intent.
    """
    text_lower = text.lower().strip()
    
    # Attribution-related (new customer PPS — check before NPS)
    attribution_keywords = ["how are people finding", "how did they find", "attribution", "acquisition channel",
                             "where are customers coming from", "how did you hear", "new customer survey",
                             "first time buyer", "first-time buyer", "how they found us"]
    if any(kw in text_lower for kw in attribution_keywords):
        return ParsedCommand(command="attribution", raw_input=text)

    # PPS returning-related (check before NPS)
    pps_returning_keywords = ["returning customer survey", "why do people come back", "why did they come back",
                               "what made customers come back", "why do people repurchase", "repeat purchase",
                               "what almost stopped", "pps returning", "returning pps"]
    if any(kw in text_lower for kw in pps_returning_keywords):
        return ParsedCommand(command="pps", filters={"segment": "returning"}, raw_input=text)

    # NPS-related
    nps_keywords = ["nps", "promoter", "detractor", "passive", "survey comment", "knocommerce",
                    "net promoter", "nps score", "nps theme", "customer loyalty", "recommend"]
    if any(kw in text_lower for kw in nps_keywords):
        return ParsedCommand(command="nps", raw_input=text)
    
    # Returns-related
    return_keywords = ["return", "refund", "exchange", "redo", "fit issue", "damaged"]
    if any(kw in text_lower for kw in return_keywords):
        return ParsedCommand(command="returns", raw_input=text)
    
    # Reviews-related
    review_keywords = ["review", "star rating", "okendo", "product review"]
    if any(kw in text_lower for kw in review_keywords):
        return ParsedCommand(command="reviews", raw_input=text)
    
    # CSAT-related
    csat_keywords = ["csat", "satisfaction", "customer sat"]
    if any(kw in text_lower for kw in csat_keywords):
        return ParsedCommand(command="csat", raw_input=text)
    
    # Error-related
    error_keywords = ["error", "bug", "broken", "checkout issue", "payment issue", "discount code"]
    if any(kw in text_lower for kw in error_keywords):
        return ParsedCommand(command="errors", raw_input=text)
    
    # Default to VOC
    return ParsedCommand(command="voc", raw_input=text)
