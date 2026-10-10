# Product VOC

Product-filtered `/voc` uses one conversation set for both the aggregate metrics and the written themes. `scout/services/voc_service.py` owns that orchestration.

```
User: "@Scout what are customers saying about the Anytime Crewneck?"
        │
 nl_router → /voc L30 product:"anytime crewneck"
        │
 voc_service.run_voc_query
        │
        ├─ product_feedback_service.get_candidate_scope
        │     ProductFeedbackScope(conversation_ids, retrieval_mode)
        │
        ├─ queries/voc.run_voc(cmd, conversation_ids=...)
        │     volume, channels, tags, status for those IDs only
        │
        └─ product_feedback_service.get_product_feedback_for_scope
              customer messages → evidence cleaning → LLM themes
```

General VOC (no `product` filter) does not enter this coordinator. It calls `run_voc()` directly.

## Contract

- `queries/voc.py` does not import `product_feedback_service`. It only aggregates IDs it is given.
- `product_feedback_service` does not import `voc.py`.
- `voc_service.py` is the only module that imports both.

The merged dict matches `run_voc()` and adds a `product_feedback` object: headline, themes, so-what, recommended action, retrieval mode, and caveats.

## Retrieval strategy

`get_candidate_scope` tries three passes. It stops widening once it has at least `min_sample_size` conversations (default 5). Auto-replies, out-of-office subjects, and similar noise are excluded in SQL.

| Pass | Mode | Criteria |
|---|---|---|
| Strict | `strict` | A product-feedback tag, and the product alias in the message body or subject |
| Fallback 1 | `fallback_relaxed` | A product-feedback tag, and the first word of an alias in the body or subject |
| Fallback 2 | `fallback_body_only` | Alias in the body or subject, with no tag required |

An empty result uses mode `empty`.

Tag names come from the `product_feedback` group in `scout/taxonomy/tags.py`. Those names are resolved to UUIDs through `tag_mapping.TAG_ID_TO_NAME`. Product aliases come from `scout/taxonomy/products.py`.

Customer-authored messages for synthesis use `AUTHOR_ID = CUSTOMER_ID`. Evidence cleaning (HTML leftovers, signatures, survey boilerplate) lives in `scout/services/evidence_service.py`. Theme synthesis calls the OpenAI client from `product_feedback_service.py`.

The product-feedback SQL uses unqualified `CONVERSATIONS` and `MESSAGES`. The other Richpanel queries use the fully qualified `FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR` names. Both depend on the Snowflake session pointing at that schema.
