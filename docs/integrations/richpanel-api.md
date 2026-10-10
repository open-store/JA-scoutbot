# Richpanel API

`/daily` is the only command that calls the Richpanel HTTP API. The client is `queries/richpanel.py`.

Base URL: `https://api.richpanel.com/v1`

Auth: request header `x-richpanel-key` set from `RICHPANEL_API_KEY`. GET requests send `accept` only. POST requests also send `content-type: application/json`. The process raises if the variable is unset.

## Endpoints

| Call | Path | Used for |
|---|---|---|
| POST | `/v1/reports/query` | Volume, response times, CSAT, negative-rating count |
| GET | `/v1/users` | Agent id to display name. Cached for the process lifetime |
| GET | `/v1/tickets` | Up to five negative-CSAT tickets (subject, thread, URL) for the note |

Report queries send `timezone: America/New_York`. The default report date is today in that timezone. `/daily` does not take an `L7` style window.

## Metrics

Volume and time, dimension `agent`:

- `closed_conversations_all_agent`
- `first_response_time_bh` and `p50_first_response_time_bh`
- `time_to_first_resolution_bh` and `p50_time_to_first_resolution_bh`

CSAT, dimension `agent`:

- `csat_score` (average on the 1–5 scale)
- `csat_surveys_sent`
- `csat_surveys_rated`
- `csat_score_percentage`

Time metrics arrive in milliseconds and are formatted for Slack. The `_bh` suffix means business hours, as Richpanel defines them.

Negative CSAT is a third query: `csat_surveys_rated` filtered to ratings `1`, `2`, and `3`. Labels used when reading tickets are Amazing=5, Great=4, Neutral=3, Bad=2, Terrible=1.

## Agent filtering

`GET /v1/users` keeps users whose role is one of `TENANT_AGENT_FULL`, `TENANT_ADMIN`, or `TENANT_COLLABORATOR`, and drops a hardcoded set of Richpanel staff addresses. Those addresses stay in `queries/richpanel.py`. Do not copy them into docs, issues, or logs.

A hardcoded set of core CS agent display names is used only to count how many of those agents closed at least one ticket, which feeds the average-tickets figure. If none of them closed a ticket, the code falls back to everyone who closed a ticket. The names stay in code.

Per-agent rows are still returned on the result dict for a future agent breakdown. `format_daily` is what Slack actually prints.
