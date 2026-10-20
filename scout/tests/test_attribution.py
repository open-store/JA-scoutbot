"""Attribution tiers, customer authorship, deduplication, and evidence-linked themes.

Snowflake, OpenAI, and order data are mocked. Products, orders, and emails
are fixture data.
"""
import json
import re
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from scout.formatting import format_voc
from scout.models.product_feedback import CONFIRMED, INFERRED, ORDER_LINKED, OTHER_PRODUCT, UNRESOLVED
from scout.repositories import order_repository as orders_repo
from scout.routing.command_parser import parse_command
from scout.services import order_lookup as lookup_mod
from scout.services import product_feedback_service
from scout.services.attribution_service import (
    Attributor,
    ConversationContext,
    ProductTarget,
    garments_in,
    order_numbers_in,
    verified_email,
)
from scout.services.evidence_service import (
    clean_message_body,
    collapse_repeats,
    process_evidence_with_stats,
    scrub_personal_details,
    strip_agent_text,
)
from scout.services.order_lookup import SafeOrderLookup, UnavailableOrderLookup, get_order_lookup
from scout.services.product_feedback_service import _link_themes
from scout.services.voc_service import run_voc_query
from scout.taxonomy.products import alias_only_catalog, resolve_product
from scout.tests.catalog_fixture import fixture_catalog
from scout.tests.fake_warehouse import TICKET_TIME, FakeOrderLookup, FakeWarehouse, message, order

CATALOG = fixture_catalog()
EMAIL = "pat@example.com"


def _target(name="Atlas Short"):
    return ProductTarget(resolve_product(name, CATALOG), CATALOG)


def _ctx(cid="c1", **kw):
    base = dict(conversation_id=cid, subject="", channel="email", created_at=TICKET_TIME,
                customer_email=EMAIL, first_from_customer=True)
    base.update(kw)
    return ConversationContext(**base)


def _attribute(bodies, target=None, contexts=None, orders=(), history=(), available=True):
    msgs = [{"MESSAGE_ID": f"m{i}", "CONVERSATION_ID": cid, "cleaned_body": body, "CREATED_AT": TICKET_TIME}
            for i, (cid, body) in enumerate(bodies)]
    contexts = contexts or {cid: _ctx(cid) for cid, _ in bodies}
    by_number = {o.order_number: o for o in orders}
    hist = {}
    for o in history:
        hist.setdefault(o.customer_email, []).append(o)
    return Attributor(target or _target(), contexts, by_number, hist, available).attribute(msgs)


def _one(body, **kw):
    return _attribute([("c1", body)], **kw)[0]


# ---------------------------------------------------------------------------
# Confirmed, other product, unresolved by mention
# ---------------------------------------------------------------------------

def test_customer_mention_is_confirmed():
    a = _one("My Atlas Shorts ride up after a wash.")
    assert (a.tier, a.method, a.product) == (CONFIRMED, "customer_mention", "Atlas Short")


def test_two_products_named_is_unresolved_not_double_counted():
    a = _one("The Atlas Short fits but the Charter Twill Pant runs small.")
    assert a.tier == UNRESOLVED and a.method == "multiple_products_named"
    assert set(a.candidates) == {"Atlas Short", "Charter Twill Pant"}
    assert a.product is None


def test_message_about_another_product_is_excluded():
    a = _one("The Charter Twill Pant waist is too tight.")
    assert a.tier == OTHER_PRODUCT


def test_longer_product_name_does_not_confirm_the_shorter_style():
    a = _one("The Atlas Short sleeve shirt collar curls.", available=False)
    assert a.tier != CONFIRMED


def test_family_target_confirms_any_family_style():
    a = _one("My Jetsetter Tech Pants are great but the hem frays.", target=_target("Jetsetter"))
    assert a.tier == CONFIRMED and a.product == "Jetsetter"


def test_same_thread_mention_confirms_follow_up():
    rows = _attribute([("c1", "The Atlas Short waistband rolls."), ("c1", "Also the pockets are shallow.")],
                      available=False)
    assert [a.method for a in rows] == ["customer_mention", "same_thread_mention"]
    assert all(a.tier == CONFIRMED for a in rows)


def test_thread_naming_several_products_leaves_follow_up_unresolved():
    rows = _attribute([("c1", "The Atlas Short is fine."), ("c1", "The Charter Twill Pant is not."),
                       ("c1", "It pills after one wash.")], available=False)
    assert rows[2].tier == UNRESOLVED and rows[2].method == "thread_names_several_products"


def test_subject_counts_only_when_the_customer_wrote_it():
    customer = _attribute([("c1", "It pills after one wash.")],
                          contexts={"c1": _ctx(subject="Atlas Short question")}, available=False)[0]
    agent = _attribute([("c1", "It pills after one wash.")],
                       contexts={"c1": _ctx(subject="Atlas Short question", first_from_customer=False)},
                       available=False)[0]
    assert customer.tier == CONFIRMED and customer.method == "subject_reference"
    assert agent.tier == UNRESOLVED


# ---------------------------------------------------------------------------
# Order-linked
# ---------------------------------------------------------------------------

def test_referenced_single_product_order_is_order_linked():
    a = _one("Order #10234 arrived and the waistband rolls.",
             orders=[order("o1", "10234", EMAIL, ["Atlas Short"])])
    assert (a.tier, a.method) == (ORDER_LINKED, "order_single_product")
    assert a.order_ids == ("o1",) and a.order_line_ids == ("o1-L0",)


def test_multi_item_order_needs_the_item_named():
    multi = order("o1", "10234", EMAIL, ["Atlas Short", "Charter Oxford Shirt"])
    vague = _one("Order #10234: the fit is off.", orders=[multi])
    named = _one("Order #10234: the shorts are too tight.", orders=[multi])
    assert (vague.tier, vague.method) == (UNRESOLVED, "multi_item_order")
    assert set(vague.candidates) == {"Atlas Short", "Charter Oxford Shirt"}
    assert (named.tier, named.method) == (ORDER_LINKED, "order_item_match")
    assert named.order_line_ids == ("o1-L0",)


def test_multi_item_order_with_two_matching_garments_stays_unresolved():
    two_pants = order("o1", "10234", EMAIL, ["Atlas Pant", "Charter Twill Pant"])
    a = _one("Order #10234: the pants are too long.", target=_target("Charter Twill Pant"), orders=[two_pants])
    assert a.tier == UNRESOLVED


def test_order_for_another_email_is_not_trusted():
    a = _one("Order #10234 is wrong.", orders=[order("o1", "10234", "someone@else.com", ["Atlas Short"])])
    assert (a.tier, a.method) == (UNRESOLVED, "order_identity_mismatch")


def test_named_product_conflicting_with_referenced_order_is_unresolved():
    a = _one("My Atlas Short from order #10234 tore.", orders=[order("o1", "10234", EMAIL, ["Charter Twill Pant"])])
    assert (a.tier, a.method) == (UNRESOLVED, "conflicting_order_reference")


def test_referenced_order_wins_over_purchase_history():
    a = _one("Order #10234: the waist is tight.",
             orders=[order("o1", "10234", EMAIL, ["Charter Twill Pant"])],
             history=[order("o2", "10111", EMAIL, ["Atlas Short"])])
    assert a.tier == OTHER_PRODUCT


def test_order_reference_without_order_data_is_unresolved():
    a = _one("Order #10234: the waist is tight.", available=False)
    assert (a.tier, a.method) == (UNRESOLVED, "order_data_unavailable")


# ---------------------------------------------------------------------------
# Inferred
# ---------------------------------------------------------------------------

def test_only_product_bought_before_ticket_is_inferred():
    a = _one("The waistband rolls over every time.", history=[order("o1", "1", EMAIL, ["Atlas Short"])])
    assert (a.tier, a.method) == (INFERRED, "purchase_history_single_product")
    assert "90 days before the ticket" in a.basis


def test_order_placed_after_the_ticket_is_ignored():
    later = order("o1", "1", EMAIL, ["Atlas Short"], placed=TICKET_TIME + timedelta(hours=2))
    a = _one("The waistband rolls over every time.", history=[later])
    assert (a.tier, a.method) == (UNRESOLVED, "no_matching_purchase")


def test_order_outside_lookback_is_ignored():
    old = order("o1", "1", EMAIL, ["Atlas Short"], placed=TICKET_TIME - timedelta(days=120))
    assert _one("The waistband rolls over.", history=[old]).tier == UNRESOLVED


def test_several_recent_products_are_not_guessed():
    hist = [order("o1", "1", EMAIL, ["Atlas Short"]), order("o2", "2", EMAIL, ["Clubhouse Polo"])]
    vague = _one("It started pilling right away.", history=hist)
    named = _one("The shorts started pilling right away.", history=hist)
    assert (vague.tier, vague.method) == (UNRESOLVED, "several_recent_products")
    assert (named.tier, named.method) == (INFERRED, "purchase_history_item_match")


@pytest.mark.parametrize("email", ["hello@jackarcher.com", "noreply@shop.com", "abc@marketplace.amazon.com", "", None])
def test_unverifiable_customer_identity_is_not_used(email):
    a = _attribute([("c1", "It pills.")], contexts={"c1": _ctx(customer_email=email)},
                   history=[order("o1", "1", email or "x@y.com", ["Atlas Short"])])[0]
    assert (a.tier, a.method) == (UNRESOLVED, "customer_identity_unverified")
    assert a.customer_identity_verified is False


def test_attribution_record_keeps_identifiers_but_no_email():
    a = _one("Order #10234 arrived and the waistband rolls.",
             orders=[order("o1", "10234", EMAIL, ["Atlas Short"])])
    record = a.to_dict()
    assert record["ticket_created_at"] == TICKET_TIME.isoformat()
    assert record["customer_identity_verified"] is True
    assert EMAIL not in json.dumps(record)


def test_helpers():
    assert order_numbers_in("order #10234 and Order number: 55512") == ["10234", "55512"]
    assert order_numbers_in("call 5551234567") == []
    assert verified_email(" Pat@Example.com ") == "pat@example.com"
    assert garments_in("the shorts are too short and the tee is long") == {"short", "tee"}
    assert garments_in("on top of that, in brief") == set()


# ---------------------------------------------------------------------------
# Customer authorship and deduplication
# ---------------------------------------------------------------------------

def test_quoted_agent_recommendation_is_not_customer_testimony():
    agent = "We recommend the Atlas Short in a size up for a relaxed fit through the thigh."
    body = f"Thanks, that helps.\n{agent}"
    text, removed = strip_agent_text(body, [agent])
    assert removed and "Atlas Short" not in text
    assert _one(text, available=False).tier != CONFIRMED


def test_gmail_quoted_chain_and_mobile_signature_are_removed():
    body = ("The waistband rolls.\n\nSent from my iPhone\n\n"
            "On Mon, Oct 5, 2026 at 9:14 AM Jack Archer <hello@jackarcher.com> wrote:\n> We recommend the Atlas Short")
    cleaned = clean_message_body(body)
    assert cleaned == "The waistband rolls."


def test_repeated_and_quoted_chain_messages_collapse():
    msgs = [
        {"AUTHOR_ID": "a", "cleaned_body": "The Atlas Short waistband rolls over after one wash."},
        {"AUTHOR_ID": "a", "cleaned_body": "Following up. The Atlas Short waistband rolls over after one wash. Any update?"},
        {"AUTHOR_ID": "a", "cleaned_body": "The Atlas Short waistband rolls over after one wash!"},
        {"AUTHOR_ID": "b", "cleaned_body": "The Atlas Short waistband rolls over after one wash."},
    ]
    kept, dropped = collapse_repeats(msgs)
    assert dropped == 2
    assert [m["AUTHOR_ID"] for m in kept] == ["a", "b"]


def test_pipeline_reports_what_it_removed():
    raw = [
        message("c1", "m1", "My Atlas Short waistband rolls over after one wash."),
        message("c1", "m2", "My Atlas Short waistband rolls over after one wash."),
        message("c1", "m3", "I am currently out of the office."),
        message("c2", "m4", "Thanks!\nWe recommend the Atlas Short in a size up for a relaxed fit through the thigh."),
    ]
    agent = [{"CONVERSATION_ID": "c2", "BODY": "We recommend the Atlas Short in a size up for a relaxed fit through the thigh."}]
    kept, stats = process_evidence_with_stats(raw, ["atlas short"], agent_messages=agent)
    assert stats.exact_duplicates == 1 and stats.autoreplies == 1
    assert stats.agent_text_removed == 1 and stats.too_short == 1
    assert len(kept) == 1


def test_scrub_personal_details():
    text = scrub_personal_details("Email me at pat@example.com or 555-123-4567 about order #10234.")
    assert "pat@example.com" not in text and "555-123-4567" not in text and "10234" not in text


# ---------------------------------------------------------------------------
# Evidence-linked themes
# ---------------------------------------------------------------------------

def test_theme_counts_come_from_cited_evidence():
    analysed = [
        {"MESSAGE_ID": "m1", "CONVERSATION_ID": "c1", "cleaned_body": "The waistband rolls over. Email pat@example.com"},
        {"MESSAGE_ID": "m2", "CONVERSATION_ID": "c1", "cleaned_body": "Still rolling after a second wash."},
        {"MESSAGE_ID": "m3", "CONVERSATION_ID": "c2", "cleaned_body": "The inseam is too short."},
    ]
    themes, dropped = _link_themes([
        {"label": "Waistband rolls", "evidence": [1, 2, 99], "mention_count": 40,
         "representative_quote": "The waistband rolls over. Email pat@example.com"},
        {"label": "Inseam", "evidence": [3], "representative_quote": "an invented quote that is not there"},
        {"label": "Invented", "evidence": [], "representative_quote": "x"},
    ], analysed)
    assert dropped == 1
    waist, inseam = themes
    assert (waist.mention_count, waist.message_count) == (1, 2)
    assert waist.evidence_message_ids == ("m1", "m2")
    assert waist.quote_message_id == "m1" and "pat@example.com" not in waist.representative_quote
    assert inseam.representative_quote is None


# ---------------------------------------------------------------------------
# Order lookup wiring
# ---------------------------------------------------------------------------

def test_order_lookup_is_off_by_default(monkeypatch):
    monkeypatch.delenv(lookup_mod.ORDER_LOOKUP_ENV, raising=False)
    lookup = get_order_lookup()
    assert lookup.available is False
    assert "not connected" in lookup.reason


def test_order_lookup_flag_selects_snowflake(monkeypatch):
    monkeypatch.setenv(lookup_mod.ORDER_LOOKUP_ENV, "snowflake")
    lookup = get_order_lookup()
    assert lookup.available is True
    assert lookup.source == orders_repo.ORDER_SOURCE


def test_order_lookup_failure_degrades_to_unavailable():
    inner = MagicMock(available=True, source="X", reason="")
    inner.orders_by_number.side_effect = RuntimeError("SQL compilation error: invalid identifier 'ORDER_NAME'")
    safe = SafeOrderLookup(inner)
    assert safe.orders_by_number(["10234"]) == []
    assert safe.available is False and "RuntimeError" in safe.reason


def test_order_sql_is_bounded_bound_and_read_only():
    by_number = orders_repo.build_orders_by_number_sql(2)
    by_email = orders_repo.build_orders_for_emails_sql(3)
    for sql in (by_number, by_email):
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT") and f"LIMIT {orders_repo.MAX_ROWS}" in upper
        assert "IS_TEST_ORDER" in upper and "IS_GWP_LINE" in upper
        for kw in ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE"):
            assert not re.search(rf"\b{kw}\b", upper)
    assert "%(n1)s" in by_number and "%(e2)s" in by_email and "IS_REPORTABLE_SALES_ORDER" in by_email
    with patch.object(orders_repo, "execute_query_dict", return_value=[]) as q:
        orders_repo.SnowflakeOrderLookup().orders_for_emails([EMAIL], TICKET_TIME, TICKET_TIME)
    sql, params = q.call_args.args
    assert EMAIL not in sql and params["e0"] == EMAIL


def test_rows_to_orders_groups_lines():
    rows = [
        {"ORDER_ID": "1", "ORDER_NAME": "#10234", "CUSTOMER_EMAIL": EMAIL, "ORDER_CREATED_AT": TICKET_TIME,
         "LINE_ID": "a", "PRODUCT_TITLE": "Atlas Short ", "QUANTITY": 2},
        {"ORDER_ID": "1", "ORDER_NAME": "#10234", "CUSTOMER_EMAIL": EMAIL, "ORDER_CREATED_AT": TICKET_TIME,
         "LINE_ID": "b", "PRODUCT_TITLE": "Clubhouse Polo", "QUANTITY": 1},
    ]
    (o,) = orders_repo.rows_to_orders(rows)
    assert o.order_number == "10234" and [l.product_title for l in o.lines] == ["Atlas Short", "Clubhouse Polo"]


# ---------------------------------------------------------------------------
# Service → formatter
# ---------------------------------------------------------------------------

def _llm_citing_all(n):
    client = MagicMock()
    payload = {"headline": "Customers describe waistband and fit problems.",
               "themes": [{"label": "Waistband rolls over", "evidence": list(range(1, n + 1)),
                           "representative_quote": "waistband rolls over"}],
               "so_what": "Construction drives the feedback.", "recommended_action": "Review the waistband."}
    client.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    return client


def _run(cmd, wh, lookup, llm):
    with patch("scout.repositories.product_feedback_repository.execute_query_dict", side_effect=wh.repo), \
         patch("queries.voc.execute_query_dict", side_effect=wh.voc), \
         patch.object(product_feedback_service, "get_client", return_value=llm), \
         patch("scout.services.voc_service.get_catalog", return_value=CATALOG), \
         patch("scout.services.voc_service.get_order_lookup", return_value=lookup):
        data = run_voc_query(cmd)
    return data, format_voc(data)


def _scenario():
    ids = ["conv-1", "conv-2", "conv-3", "conv-4"]
    messages = {
        "conv-1": [message("conv-1", "m1", "The Atlas Short waistband rolls over after one wash.")],
        "conv-2": [message("conv-2", "m2", "The Atlas Short fits, but the Charter Twill Pant runs small.")],
        "conv-3": [message("conv-3", "m3", "Order #10234 arrived and the waistband rolls over constantly.")],
        "conv-4": [message("conv-4", "m4", "Order #20555: the fit is off on these.")],
        "conv-9": [message("conv-9", "m9", "The waistband keeps rolling down when I walk.")],
    }
    contexts = {
        "conv-1": {"SUBJECT": "Atlas Short"}, "conv-2": {}, "conv-3": {"SUBJECT": "Atlas Short waistband"},
        "conv-4": {"SUBJECT": "Atlas Short", "FIRST_FROM_CUSTOMER": False},
        "conv-9": {},
    }
    lookup = FakeOrderLookup([
        order("o3", "10234", "conv-3@example.com", ["Atlas Short"]),
        order("o4", "20555", "conv-4@example.com", ["Atlas Short", "Clubhouse Polo"]),
        order("o9", "30999", "conv-9@example.com", ["Atlas Short"]),
    ])
    return ids, messages, contexts, lookup


def test_only_confirmed_and_order_linked_evidence_reaches_themes():
    cmd = parse_command('/voc L30 product:"Atlas Short"')
    ids, messages, contexts, lookup = _scenario()
    wh = FakeWarehouse(cmd, ids, [], messages=messages, contexts=contexts, outside_ids=["conv-9"])
    llm = _llm_citing_all(2)
    data, out = _run(cmd, wh, lookup, llm)

    pf = data["product_feedback"]
    tiers = {a["message_id"]: (a["tier"], a["method"]) for a in pf["attributions"]}
    assert tiers == {
        "m1": (CONFIRMED, "customer_mention"),
        "m2": (UNRESOLVED, "multiple_products_named"),
        "m3": (ORDER_LINKED, "order_single_product"),
        "m4": (UNRESOLVED, "multi_item_order"),
    }
    prompt = llm.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Charter Twill Pant runs small" not in prompt and "fit is off" not in prompt
    assert pf["sample_count"] == 2
    assert pf["themes"][0]["mention_count"] == 2
    assert [a["message_id"] for a in pf["inferred_attributions"]] == ["m9"]
    assert pf["total_conversations"] == 4  # inferred ticket is not added to the scope

    assert "*Confirmed:* 1 message from 1 ticket" in out
    assert "*Order-linked:* 1 message from 1 ticket" in out
    assert "*Inferred:* 0 messages from 0 tickets, plus 1 message from 1 other product-feedback ticket" in out
    assert "1 several products named" in out and "1 multi-item order, item not named" in out
    assert "Waistband rolls over* — 2 tickets" in out
    assert "2 customer messages from 2 of 4 product-related tickets" in out


def test_without_order_data_the_tiers_say_not_evaluated():
    cmd = parse_command('/voc L30 product:"Atlas Short"')
    ids, messages, contexts, _ = _scenario()
    wh = FakeWarehouse(cmd, ids, [], messages=messages, contexts=contexts)
    data, out = _run(cmd, wh, UnavailableOrderLookup(reason="Order data is not connected."), _llm_citing_all(1))
    tiers = {a["message_id"]: a["tier"] for a in data["product_feedback"]["attributions"]}
    assert tiers["m3"] == UNRESOLVED and tiers["m1"] == CONFIRMED
    assert "*Order-linked:* not evaluated, order data is not connected" in out
    assert "*Inferred:* not evaluated" in out
    assert not any("TAGS)) f" in s and "NOT IN" in s for s in wh.repo_sql)


def test_scope_requires_a_customer_authored_mention():
    cmd = parse_command('/voc L30 product:"Atlas Short"')
    wh = FakeWarehouse(cmd, [], [])
    _run(cmd, wh, UnavailableOrderLookup(reason="off"), _llm_citing_all(1))
    strict = wh.candidate_calls("current", "strict")[0]
    assert "(m.AUTHOR_ID = c.CUSTOMER_ID AND (LOWER(m.BODY) LIKE '%atlas short%'" in strict


def test_authorship_is_not_relaxed_for_small_samples():
    cmd = parse_command('/voc L30 product:"Atlas Short"')
    messages = {"conv-1": [message("conv-1", "m1", "The Atlas Short waistband rolls over after one wash.")]}
    wh = FakeWarehouse(cmd, ["conv-1"], [], messages=messages)
    with patch.object(product_feedback_service, "fetch_customer_messages",
                      wraps=product_feedback_service.fetch_customer_messages) as fetch:
        data, out = _run(cmd, wh, UnavailableOrderLookup(reason="off"), _llm_citing_all(1))
    assert all(call.kwargs.get("strict_customer", True) for call in fetch.call_args_list)
    assert data["product_feedback"]["low_sample"] is True
    assert "Low sample size" in out


def test_catalog_outage_still_attributes_typed_phrase():
    target = ProductTarget(resolve_product("Atlas Short", alias_only_catalog()), alias_only_catalog())
    a = _one("The atlas short waistband rolls.", target=target, available=False)
    assert a.tier == CONFIRMED
