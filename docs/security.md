# Security model and threat assessment

## Assets
Customer documents (often confidential data rooms) · extracted evidence and assessments · credentials and provider keys · audit trail integrity.

## Trust boundaries
Browser ↔ API (untrusted client) · API ↔ database (app role, RLS) · worker ↔ uploaded files (untrusted content) · platform ↔ external providers (untrusted responses) · future: platform ↔ LLM (untrusted output).

## Threats and controls

| Threat | Control | Tested |
|---|---|---|
| Cross-tenant data access | App-layer `org_id` filters + PostgreSQL RLS; app role cannot bypass | ✅ API and direct-DB tests |
| Privilege escalation inside an org | Role → permission map; only owners grant admin; last owner protected | ✅ |
| Access to restricted or confidential documents | SQL visibility clause shared by list, get, search and claims; 404, never 403, to avoid an existence oracle | ✅ |
| Stolen or long-lived tokens | 30-min access tokens; refresh in httpOnly SameSite=Strict cookie; `token_version` revocation; membership checked every request | ✅ |
| CSRF | Bearer tokens are not ambient; refresh needs a custom header | ✅ |
| Credential stuffing / brute force | Rate limits per IP and per email; Argon2id; timing-equalised unknown-user path | ✅ (single instance) |
| Malicious uploads | Byte sniffing (not extension or header); size cap while streaming; ClamAV INSTREAM; quarantine status; PDF active-content flags; files are never executed or served inline | 🟡 scanner needs deployment |
| Path traversal via filenames/keys | Filenames sanitised; storage keys are content hashes; key validator rejects `..` | ✅ |
| Tampered stored originals | SHA-256 re-verified before processing; mismatch → permanent failure | ✅ |
| Tampered assessments | Canonical-JSON SHA-256 seal; DB trigger forbids edits/deletes after approval | ✅ |
| Audit log tampering | UPDATE/DELETE revoked from app role | ✅ |
| SQL injection | SQLAlchemy parameter binding throughout; no string-built SQL with user input | lint + review |
| XSS | API returns JSON only with `CSP: default-src 'none'`; frontend renders text, never HTML from documents | review |
| Prompt injection (future LLM) | ADR 0006: document text is quoted data, outputs schema-validated, no tool access | n/a yet |
| Secret leakage | Secrets only via environment/secret store; `SecretStr`; startup refuses the dev JWT secret outside development; no keys in frontend | ✅ startup guard |
| Billable-action abuse | Planet orders refused without a budget; idempotency key sent | ✅ |
| Dependency vulnerabilities | `pip-audit` and `npm audit` in CI | ✅ pip-audit clean on 2026-10-20 |

## Known gaps (tracked)
- Rate limiter is in-process; use Redis or the gateway for multiple API instances.
- No MFA or SSO yet.
- Encryption at rest depends on the deployment (managed Postgres encryption, S3 SSE). The local storage backend relies on disk encryption.
- Retention policy: soft-deleted documents keep their originals. A scheduled purge, with a configurable retention period and a legal-hold flag, is not built yet.
- A penetration test is needed before production.
