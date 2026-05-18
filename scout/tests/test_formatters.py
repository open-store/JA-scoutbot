"""Tests for the product feedback section of format_voc."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from formatters import format_voc


def _base_voc_data(**overrides):
    """Build a minimal VOC data dict for testing the formatter."""
    data = {
        "volume": {"TOTAL_CONVERSATIONS": 100, "TOTAL_RATED": 50, "CSAT_PCT": 95.0},
        "prev_volume": {"TOTAL_CONVERSATIONS": 110},
        "by_channel": [{"CHANNEL": "email", "CNT": 60}, {"CHANNEL": "chat", "CNT": 40}],
        "tags": [{"TAG_UUID": "abc", "TAG_NAME": "inquiry", "CNT": 30}],
        "prev_tags": [{"TAG_UUID": "abc", "CNT": 25}],
        "status": [{"STATUS": "CLOSED", "CNT": 90}],
        "timeframe_label": "Apr 01–Apr 30, 2026",
        "days": 30,
        "filter_label": "",
        "product_feedback": None,
    }
    data.update(overrides)
    return data


def test_no_product_feedback():
    """When product_feedback is None, no product section should appear."""
    result = format_voc(_base_voc_data())
    assert "What customers are saying" not in result


def test_product_feedback_with_themes():
    """Full product feedback with themes should render correctly."""
    pf = {
        "headline": "Customers praise the fabric but note sizing runs large.",
        "themes": [
            {"label": "Sizing runs large", "mention_count": 8, "representative_quote": "Runs about a size too big"},
            {"label": "Fabric quality praised", "mention_count": 5, "representative_quote": "Love the feel"},
        ],
        "so_what": "Feedback is concentrated around sizing expectations.",
        "recommended_action": "Review PDP sizing language.",
        "sample_count": 25,
        "total_conversations": 18,
        "retrieval_mode": "strict",
        "low_sample": False,
        "caveats": [],
        "source": "Richpanel tickets via Snowflake",
    }
    result = format_voc(_base_voc_data(
        product_feedback=pf,
        filter_label="product: Anytime Crewneck",
    ))
    assert "What customers are saying" in result
    assert "Sizing runs large" in result
    assert "25 customer messages from 18 product-related tickets" in result
    assert "Feedback is concentrated" in result
    assert "Review PDP sizing language" in result
    # Should NOT have low sample warning
    assert "Low sample size" not in result


def test_product_feedback_low_sample():
    """Low sample should show caveat."""
    pf = {
        "headline": "Limited data available.",
        "themes": [{"label": "Fit issue", "mention_count": 2, "representative_quote": "Too tight"}],
        "so_what": "Insufficient data for strong conclusions.",
        "recommended_action": "Try a longer timeframe.",
        "sample_count": 3,
        "total_conversations": 2,
        "retrieval_mode": "fallback_body_only",
        "low_sample": True,
        "caveats": ["Low sample size (3 messages). Results are directional only."],
        "source": "Richpanel tickets via Snowflake",
    }
    result = format_voc(_base_voc_data(product_feedback=pf, filter_label="product: Clubhouse Polo"))
    assert "Low sample size" in result
    assert "directional" in result


def test_product_feedback_zero_messages():
    """Zero messages should show the no-data message."""
    pf = {
        "headline": "No customer conversations found for 'Test Product'.",
        "themes": [],
        "so_what": "No data available.",
        "recommended_action": "Try a longer timeframe.",
        "sample_count": 0,
        "total_conversations": 0,
        "retrieval_mode": "strict",
        "low_sample": True,
        "caveats": [],
        "source": "Richpanel tickets via Snowflake",
    }
    result = format_voc(_base_voc_data(product_feedback=pf, filter_label="product: Test Product"))
    assert "What customers are saying" in result
    assert "No customer conversations found" in result


if __name__ == "__main__":
    test_no_product_feedback()
    test_product_feedback_with_themes()
    test_product_feedback_low_sample()
    test_product_feedback_zero_messages()
    print("All formatter tests passed.")
