"""Tests for evidence cleaning, filtering, dedup, and ranking."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scout.services.evidence_service import (
    clean_message_body,
    is_autoreply_body,
    is_too_short,
    deduplicate_messages,
    rank_by_product_relevance,
    process_evidence,
)


def test_html_stripping():
    html = "<p>I love the <b>Anytime Crewneck</b> but the <i>sleeves</i> are too long.</p>"
    cleaned = clean_message_body(html)
    assert "<" not in cleaned
    assert "Anytime Crewneck" in cleaned
    assert "sleeves" in cleaned


def test_csat_survey_removal():
    body = "Great product!\n\nWe value your feedback and would love to hear more. Sincerely, Jack Archer"
    cleaned = clean_message_body(body)
    assert "Great product" in cleaned
    # Survey template should be removed
    assert "value your feedback" not in cleaned or len(cleaned) < len(body)


def test_quoted_reply_removal():
    body = "Thanks for the update.\n\nOn Mon Jan 5 2026, support@jackarcher.com wrote: previous message here"
    cleaned = clean_message_body(body)
    assert "Thanks for the update" in cleaned
    assert "previous message here" not in cleaned


def test_autoreply_detection():
    assert is_autoreply_body("I am currently out of the office and will return on Monday.")
    assert is_autoreply_body("This is an automated response.")
    assert is_autoreply_body("Automatic reply: I'm on vacation until Jan 10.")
    assert not is_autoreply_body("I love the new Clubhouse Polo, fits great!")


def test_too_short():
    assert is_too_short("Thanks!")
    assert is_too_short("ok")
    assert not is_too_short("The Anytime Crewneck runs a bit large in the shoulders.")


def test_dedup():
    messages = [
        {"cleaned_body": "The crewneck is great but runs large."},
        {"cleaned_body": "the crewneck is great but runs large."},  # near-dup
        {"cleaned_body": "Sleeves are too long for my taste."},
    ]
    unique = deduplicate_messages(messages)
    assert len(unique) == 2


def test_ranking():
    messages = [
        {"cleaned_body": "I had a shipping issue with my order."},
        {"cleaned_body": "The Anytime Crewneck fabric is amazing."},
        {"cleaned_body": "Anytime Crewneck runs large, had to exchange."},
    ]
    ranked = rank_by_product_relevance(messages, ["anytime crewneck"])
    # Messages mentioning the product should come first
    assert "Anytime Crewneck" in ranked[0]["cleaned_body"]


def test_full_pipeline():
    raw = [
        {"BODY": "<p>I love the Anytime Crewneck but sleeves are long.</p>"},
        {"BODY": "I am currently out of the office."},
        {"BODY": "ok"},
        {"BODY": "<p>The Anytime Crewneck fabric feels premium.</p>"},
        {"BODY": "<p>I love the Anytime Crewneck but sleeves are long.</p>"},  # dup
    ]
    result = process_evidence(raw, aliases=["anytime crewneck"])
    # Should remove: autoreply, too-short, and dedup
    assert len(result) == 2
    # Both should have cleaned_body
    for msg in result:
        assert "cleaned_body" in msg
        assert "<" not in msg["cleaned_body"]


if __name__ == "__main__":
    test_html_stripping()
    test_csat_survey_removal()
    test_quoted_reply_removal()
    test_autoreply_detection()
    test_too_short()
    test_dedup()
    test_ranking()
    test_full_pipeline()
    print("All evidence service tests passed.")
