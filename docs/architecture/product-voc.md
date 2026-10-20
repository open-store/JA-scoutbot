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
              customer messages → evidence cleaning → LLM themes
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
| `headline`, `themes`, `so_what`, `recommended_action` | Synthesis |
| `candidate_conversation_ids`, `evidence_message_ids` | Evidence references |
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
| Strict | `strict` | A product-feedback tag, and a product phrase in the message body or subject |
| Fallback | `fallback_body_only` | A product phrase in the body or subject, with no tag required |

Both passes use the same product phrases. The fallback drops only the tag requirement. An empty result uses mode `empty`. Each pass returns at most 500 conversations; a capped scope is disclosed as a lower bound.

The previous period gets its own scope over the previous window, with the same product, filters, and widest pass as the current period. It never reuses the current period's IDs. `run_voc` treats each ID list as that period's population and does not apply its own window predicate on top.

Tag names come from the `product_feedback` group in `scout/taxonomy/tags.py`. Those names are resolved to UUIDs through `scout.taxonomy.tag_ids.TAG_ID_TO_NAME`.

Customer-authored messages for synthesis use `AUTHOR_ID = CUSTOMER_ID`. Evidence cleaning (HTML leftovers, signatures, survey boilerplate) lives in `scout/services/evidence_service.py`. Theme synthesis calls the OpenAI client from `product_feedback_service.py`. A `topic` filter ("fit", "sleeve length") tells synthesis what the question asked about. It does not narrow retrieval or the ticket counts.

The product-feedback SQL uses unqualified `CONVERSATIONS` and `MESSAGES`. The other Richpanel queries use the fully qualified `FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR` names. Both depend on the Snowflake session pointing at that schema.
