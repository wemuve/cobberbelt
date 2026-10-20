# ADR 0006 — AI as a controlled, replaceable service, never the source of truth

**Status:** accepted · 2026-10-20

**Decision.**
1. Machine extraction produces claim *candidates* with exact page offsets, an extractor version and `unreviewed` status. Nothing becomes a feature or enters an approved assessment without a human decision recorded in `review_decisions`.
2. The first extractor is deterministic (`ingestion/rules.py`), which makes it testable and free. An LLM extractor, when added, must write the same `Claim` schema, cite offsets that are validated against the page text, and be versioned (`extractor_version = llm:<provider>:<model>:<prompt-version>`).
3. Document text is untrusted input. It is passed to models only as quoted data and never concatenated into instructions, and model output is schema-validated. Models get no tool access that can change data.
4. LLM features stay **unconfigured** until a provider's data-retention terms are accepted and an evaluation set shows quality worth the cost.
