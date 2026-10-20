# Configuration and approvals matrix

What each integration needs before it can move from *unconfigured* to *live_verified*. Credentials go in the deployment's secret store and `.env` — **never in chat, tickets or the repository**.

| Integration | Settings | Credentials | Commercial / legal approval | Owner | Status |
|---|---|---|---|---|---|
| PostgreSQL + PostGIS + pgvector | `CBI_DATABASE_URL`, `CBI_DATABASE_OWNER_URL` | Owner role (BYPASSRLS, migrations/backups), app role (no BYPASSRLS) | — | Ops | Working (dev) |
| JWT signing | `CBI_JWT_SECRET` (≥32 chars) | Random secret per environment | — | Ops | Required before staging |
| Object storage | `CBI_STORAGE_BACKEND`, `CBI_S3_*` | IAM user limited to one bucket; SSE enabled; versioning on | Data residency decision (see Zambia hosting requirement note) | Ops / Adam | Local works; S3 adapter untested |
| Malware scanning | `CBI_CLAMAV_HOST`, `CBI_REQUIRE_MALWARE_SCAN=true` | — | — | Ops | Protocol implemented; no clamd in this sandbox |
| Planet | `CBI_PLANET_API_KEY`, `CBI_PLANET_MONTHLY_BUDGET_USD` | Planet API key | Contract terms for storage, analytics, customer display, redistribution | Adam | Blocked: key + contract |
| PVGIS | `CBI_PVGIS_BASE_URL` | None | Attribution only | — | Needs a networked live check |
| LLM extraction / drafting | `CBI_LLM_PROVIDER`, `CBI_LLM_API_KEY`, `CBI_LLM_MODEL` | Provider key | Provider data-retention terms acceptable for customer documents | Adam | Not enabled by design until evaluated |
| Embeddings (semantic search) | `CBI_EMBEDDING_*` | Provider key | Same as LLM | Adam | Not implemented |
| Zambia Mining Cadastre | (future) | Possibly a portal account | Written permission for automated access | Adam | Blocked |
| DRC CAMI | (future) | — | Written permission; note current suspension | Adam | Blocked |
| Email (invitations, alerts) | (future) | SMTP / provider key | — | Ops | Not started; admins set initial passwords |

## Data-residency note
Zambia's 2025 cadastre disruption was publicly linked to a "must be hosted in Zambia" data-privacy requirement (the Ministry did not confirm the link). Before onboarding Zambian government or institutional data, get legal advice on where the database and object store may be hosted. The storage layer is region-configurable; the decision is a business/legal one.
