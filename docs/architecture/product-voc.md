# Product VOC

Product-filtered `/voc` uses one conversation set for both the aggregate metrics and the written themes. `scout/services/voc_service.py` owns that orchestration.

```
User: "@Scout what are customers saying about the Atlas Short?"
        │
 nl_router → /voc L30 product:"Atlas Short"
        │
 voc_service.run_voc_query
        │
        ├─ taxonomy.products.resolve_product(product, catalog)
        │     style, family, ambiguous, or unknown
        │     ambiguous / unknown → disambiguation reply, nothing else runs
        │
        ├─ product_feedback_service.get_candidate_scope   (current window)
        ├─ product_feedback_service.get_candidate_scope   (previous window, same pass)
        │     ProductFeedbackScope(conversation_ids, window, filters, resolution)
        │
        ├─ queries/voc.run_voc(cmd, conversation_ids=current, previous_conversation_ids=previous)
        │     volume, channels, tags, status for each period's own IDs
        │
        └─ product_feedback_service.get_product_feedback_for_scope
              customer messages → cleaning and dedup → attribution tiers
              → confirmed + order-linked evidence → evidence-linked themes
```

General VOC (no `product` filter) does not enter this coordinator. It calls `run_voc()` directly.

## Contract

- `queries/voc.py` does not import `product_feedback_service`. It only aggregates IDs it is given.
- `product_feedback_service` does not import `voc.py`.
- `voc_service.py` is the only module that imports both.

The merged dict matches `run_voc()` and adds a `product_feedback` object built by `voc_service.product_feedback_payload`. `format_voc` reads these keys:

| Key | Meaning |
|---|---|
| `status` | `ok`, or `ambiguous` / `unknown` for a disambiguation reply |
| `product_name`, `product_level`, `family`, `styles`, `match_method`, `seasons` | How the product resolved |
| `catalog_source`, `catalog_available` | Where the catalog came from, and whether it loaded |
| `timezone`, `filters`, `topic` | The population and question focus |
| `total_conversations`, `previous_total_conversations` | Scope size for each period |
| `retrieved_message_count`, `cleaned_message_count`, `sample_count` | Messages returned, kept after cleaning, and sent to synthesis |
| `low_sample`, `retrieval_mode`, `retrieval_capped`, `caveats` | Disclosure |
| `headline`, `themes`, `so_what`, `recommended_action` | Synthesis. Each theme carries `evidence_message_ids`, `conversation_ids`, and `quote_message_id` |
| `candidate_conversation_ids`, `evidence_message_ids` | Evidence references |
| `sample_ticket_count` | Tickets the analysed messages came from |
| `attribution`, `attributions`, `inferred_attributions` | Tier counts, order-data status, and one record per message |
| `evidence_stats`, `unlinked_theme_count` | Cleaning and dedup counts; themes dropped for missing citations |
| `scope`, `previous_scope` | `ProductFeedbackScope.summary()` for each period |

`sample_count` is the number of messages the model received. It is never larger than the 150-message synthesis cap.

## Product resolution

Products resolve against the catalog loaded by `scout/services/product_catalog_service.py`. The catalog is distinct styles from `JA_ANALYTICS.REPORTING.SHOPIFY_AGG_POST_PURCHASE` (`STYLE_NAME`, `FRANCHISE`, `CATEGORY`, `CLASS`, `SUBCLASS`, `SEASON`, `LIFECYCLE`). It is read with one bounded query and cached for six hours. A style with no delivered orders is not in that table yet.

| Question names | Result |
|---|---|
| A style name or an explicit alias ("Atlas Shorts", "anytime crew neck") | That style |
| A family name ("Jetsetter", "the Jetsetter line") | The whole family, labelled family-level |
| Part of a name that fits one style ("charter twill") | That style, with a caveat saying how it was read |
| Part of a name that fits several styles ("Jetsetter pant") | A question listing the styles. No tickets are queried |
| Nothing in the catalog ("Atlas Jogger") | A reply that the product is not in the catalog, with close matches. No tickets are queried |

Unknown names are never broadened to their first word. Explicit aliases live in `PRODUCT_ALIASES` in `scout/taxonomy/products.py` and attach to the catalog style they match.

If the catalog cannot be read (missing credentials, no grant on the table, an empty result), Scout uses the alias list. A name outside it is matched as the exact phrase the user typed, and the reply says the catalog was not checked.

## Retrieval strategy

`get_candidate_scope` takes an explicit window (`start` inclusive, `end` exclusive, timezone-aware, America/New_York complete days) and the same channel, tag, and agent filters the metrics use (`scout/sql.py`). It tries two passes. It adds the second pass only when the first finds fewer than `min_sample_size` conversations (default 5).

| Pass | Mode | Criteria |
|---|---|---|
| Strict | `strict` | A product-feedback tag, and a product phrase in a customer-authored message or the subject |
| Fallback | `fallback_body_only` | A product phrase in a customer-authored message or the subject, with no tag required |

A product named only in an agent reply does not put a ticket in scope. Both passes use the same product phrases. The fallback drops only the tag requirement. An empty result uses mode `empty`. Each pass returns at most 500 conversations; a capped scope is disclosed as a lower bound.

The previous period gets its own scope over the previous window, with the same product, filters, and widest pass as the current period. It never reuses the current period's IDs. `run_voc` treats each ID list as that period's population and does not apply its own window predicate on top.

Tag names come from the `product_feedback` group in `scout/taxonomy/tags.py`. Those names are resolved to UUIDs through `scout.taxonomy.tag_ids.TAG_ID_TO_NAME`.

## Customer evidence

Only customer-authored messages (`AUTHOR_ID = CUSTOMER_ID`) are evidence. Authorship is never relaxed to reach a sample size; a small sample is reported as low-sample instead. `scout/services/evidence_service.py` cleans each message:

- HTML, CSAT survey boilerplate, agent signatures, "Sent from my iPhone", and quoted replies, including Gmail-style "On Mon, Oct 5, 2026 at 9:14 AM … wrote:" chains.
- Agent text the customer quoted back. Sentences that also appear in an agent message of the same ticket are removed, so an agent's product recommendation does not become testimony.
- Auto-replies and very short messages.
- Exact duplicates, and one customer's repeated or quoted-chain messages. A message contained in, or at least 90% alike, another message from the same customer is merged into the longer one.

The payload's `evidence_stats` reports each removal.

## Product attribution

`scout/services/attribution_service.py` gives every cleaned customer message one tier and the rule that decided it. Tiers are evidence categories, not probabilities. Routing confidence is separate.

| Tier | Methods | Rule |
|---|---|---|
| Confirmed | `customer_mention` | The message names this product and no other |
| | `same_thread_mention` | The customer named only this product elsewhere in the ticket |
| | `subject_reference` | The subject names this product and the customer wrote the first message |
| Order-linked | `order_single_product` | A referenced order belongs to this customer and contains only this product |
| | `order_item_match` | A referenced multi-item order; only this product's line matches the garment the customer describes ("the shorts") |
| Inferred | `purchase_history_single_product`, `purchase_history_item_match` | The same rules over the customer's orders placed before the ticket, within 90 days |
| Unresolved | `multiple_products_named`, `conflicting_order_reference`, `order_identity_mismatch`, `order_not_found`, `multi_item_order`, `several_recent_products`, `no_matching_purchase`, `customer_identity_unverified`, `order_data_unavailable` | Kept as unattributed feedback |

A message about a different product is `other_product` and is excluded. A referenced order is used before purchase history, and an order placed after the ticket never counts. An order reference alone does not identify an item in a multi-item order, and a complaint is never attached to every item.

Customer identity for order matching is the ticket's customer email, normalised. Store, relay, and no-reply addresses are rejected. A referenced order must carry the same email. Each `ProductAttribution` stores the tier, method, basis, canonical product, candidate products, Richpanel conversation and message IDs, Shopify order and line IDs, and timestamps. It never stores the email.

Only confirmed and order-linked evidence (`ProductFeedbackScope.attribution_tiers`) goes to synthesis. Inferred evidence is reported separately: inferred messages inside the scope, and messages from up to 200 other product-feedback tickets in the window whose customer bought this product before the ticket. Those extra tickets are not added to the ticket counts.

### Order data

The order-linked and inferred tiers read Shopify orders from Snowflake (`JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDERS` and `FCT_SHOPIFY__ORDER_LINES`) through `scout/services/order_lookup.py`. Each request runs at most two bounded, parameterised lookups: referenced order numbers (up to 20), then recent orders for up to 200 customer emails. History uses Rocky's eligibility flags (`IS_REPORTABLE_SALES_ORDER`, not `IS_TEST_ORDER`, not `IS_GWP_LINE`), so replacement and test orders do not add candidates.

The lookup is off unless `SCOUT_ORDER_LOOKUP=snowflake`. Until then, the reply says those two tiers were not evaluated. Before turning it on, confirm Scout's role can read both tables, and confirm the columns marked `unverified` in `ORDER_COLUMNS` and `LINE_COLUMNS` (`scout/repositories/order_repository.py`). A query failure turns order data off for that request and says so.

## Themes

Synthesis gets numbered messages and must cite the numbers behind each theme. Counts are computed in code: `mention_count` is distinct tickets among valid citations, and `message_count` is cited messages. A theme with no valid citation is dropped, and the caveats say how many were dropped. A quote is shown only if it appears in a cited message, with emails, phone numbers, and order numbers scrubbed. A theme from one ticket is labelled a single report.

Theme synthesis calls the OpenAI client from `product_feedback_service.py`. A `topic` filter ("fit", "sleeve length") tells synthesis what the question asked about. It does not narrow retrieval or the ticket counts.

The product-feedback SQL uses unqualified `CONVERSATIONS` and `MESSAGES`. The other Richpanel queries use the fully qualified `FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR` names. Both depend on the Snowflake session pointing at that schema.
