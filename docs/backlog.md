# Backlog, scope control and validation plan

Status key: ✅ implemented + automated tests · 🟡 implemented, not externally verified · ⛔ blocked (external dependency) · ⬜ planned

## List 1 — Required for the first commercial release

The workflow: ingest authorised documents → extract and review evidence → search → link evidence to geography → project workspace → auditable assessment.

| # | Item | Status | Acceptance (measurable) |
|---|---|---|---|
| 1.1 | Repo, migrations, Docker, CI, env template | ✅ / 🟡 Docker | `alembic upgrade head` on empty DB; CI green; `docker compose up` healthy (**not yet run: no Docker daemon in build sandbox**) |
| 1.2 | Auth, orgs, roles, revocation | ✅ | Tests: token revocation, role limits, membership revocation, generic login failures, rate limits |
| 1.3 | Tenant isolation (app + RLS) | ✅ | Cross-tenant API test and direct-DB RLS test pass |
| 1.4 | Document classification & grants | ✅ | Confidential/project-restricted test passes; search respects visibility |
| 1.5 | Upload validation, dedupe, storage, checksum verify | ✅ | Type sniffing, size limit, empty-file, unauthorised-source tests |
| 1.6 | Malware scanning | 🟡 | clamd INSTREAM implemented; **needs a clamd instance + EICAR test in staging** |
| 1.7 | Async processing, retry, dead-letter, recovery | ✅ | Transient retry, permanent failure → dead → admin retry, stale-lock recovery tests |
| 1.8 | Text-layer + OCR (eng/fra), page persistence, versions | ✅ synthetic / 🟡 real | 2 real documents processed (73 pages). Prose OCR works well (80.6% mean confidence); maps and tables are weak. CER thresholds need a hand-transcribed sample (30–50 pages) |
| 1.8b | Station-table coordinates with reviewer-stated hemisphere | ✅ | 77% row recall, 1 silent OCR digit error in 20 checked rows; review against the image is enforced |
| 1.8c | Job heartbeat and fencing for long jobs | ✅ | Regression test; a live recovery after a killed worker was observed |
| 1.9 | Rule-based claim candidates with offsets | ✅ | Offsets reproduce excerpt exactly; coordinate parser tests (DMS, decimal, FR/EN hemispheres) |
| 1.10 | Review workflow + immutable decision history | ✅ | Comment required for reject; history append-only |
| 1.11 | Features: CRS transform, validity, uncertainty, conflicts | ✅ | UTM→WGS84 test; bow-tie rejected; conflict flag in evidence + map |
| 1.12 | Hybrid search (FTS en/fr + commodity expansion + trigram + spatial) | ✅ / ⛔ relevance | **Relevance thresholds need a judged query set from real documents** |
| 1.13 | Project workspace, AOI, risks, gaps, dossier | ✅ | Dossier lists features in AOI, conflicts, detected gaps, not-checked items |
| 1.14 | Hash-sealed, separation-of-duties assessments | ✅ | Tamper detection; author cannot approve; DB trigger blocks edits after approval |
| 1.15 | Audit log (append-only) | ✅ | App role cannot DELETE; key actions recorded |
| 1.16 | Backup + verified restore | ✅ dev | Scripted backup → restore → row counts + object SHA-256 verified |
| 1.17 | Web app: login, upload, document & page viewer, search, map, project, assessment | ⬜→ in progress | End-to-end user journey runs against the real API |
| 1.18 | Monitoring: structured logs, readiness, job health | ✅ basic | Error tracking service (e.g. Sentry) and alert routing still to choose |
| 1.19 | Staging environment + production deployment target | ⬜ | Needs a hosting decision (data-residency note in configuration-matrix.md) |

## List 2 — Next validated release (only after List 1 is used by real customers)

**Re-prioritised on 2026-10-21 from real archive documents** (see evaluation.md). These come first:
1. **Map-sheet path:** georeferencing from printed graticule corners (GeoTIFF output), region OCR limited to the legend and title block, and a zoomable tile viewer for large scans. The first two real documents included a map sheet; a 1:200,000 sheet is unreadable at page width.
2. **Layout-aware table OCR** (cell-level), benchmarked on the Hermans station table (78 rows, printed ground truth); evaluate managed OCR here first.
3. **Page-batch parallel OCR jobs:** at about 14 s per page, a single 72-page report holds one worker for 16 minutes.
4. **Per-block OCR confidence,** so noisy map-body text is down-weighted in search and flagged in review.

- Embedding-based semantic retrieval, gated on a relevance evaluation showing improvement.
- LLM-assisted extraction (tables, assay intervals, borehole collars) writing the same reviewed-claim schema.
- Historical map georeferencing (control points → GeoTIFF) and raster layers.
- Planet search UI and order persistence with budget guard and webhook/order polling (adapter exists).
- PVGIS solar screening in project dossiers (adapter exists; needs a networked live check).
- Mining-title overlays, **only** once written permission exists, with a last-checked timestamp and an official verification link.
- Field module MVP: observations, samples with QR IDs, custody transfers, lab result import (offline sync later).
- SSO (OIDC), MFA, email invitations.
- PDF export of an approved assessment with seal and evidence appendix.

## List 3 — Future, requires customer evidence or commercial justification

Environmental management module · mine-planning integrations (Datamine, Leapfrog, Deswik — need licensed API access and a customer that wants it) · financial scenario engine · supplier network / procurement · institutional archive services as a product · terrain analytics beyond basic slope · multi-region expansion.

Kept architecturally possible: tenant model, evidence/claim schema, provider framework and job queue all apply unchanged. **Nothing in List 3 has screens or placeholder endpoints.**

---

## Commercial validation template (fill per capability before building List 2/3 items)

| Question | Geodata Intelligence + Project DD (first workflow) |
|---|---|
| Target customer & decision | Junior explorers, consultants and investors deciding whether to pursue, acquire or drop a ground position in the ZM/DRC Copperbelt |
| Existing workflow | Consultants read data-room PDFs and archive scans by hand, keep notes in spreadsheets, plot points in QGIS, write a Word report |
| Measurable problem (hypothesis) | Hours to locate and cite all historical evidence for an area; untraceable claims in reports; conflicting historical positions silently merged |
| Evidence required | 5+ discovery interviews; a timed pilot on one customer's data room comparing hours-to-dossier and citation completeness against their current process |
| Delivery / operating cost | Hosting + OCR compute per page (measure on the pilot); reviewer time is the customer's |
| Willingness to pay | Unknown; test a per-project dossier price and an annual seat price in the pilot |
| Legal / licensing | Customer warrants rights to uploaded data; no third-party archive content without a licence |
| Continue / revise / abandon | Continue if the pilot cuts dossier time ≥40% with equal or better citation completeness **and** ≥2 customers commit to paid pilots; revise if time savings exist but WTP is low; abandon the archive angle if no licensed archive is obtainable within 6 months |

## Differentiation hypotheses — to be tested, not claimed

Each needs a side-by-side comparison against existing products before it can be called an advantage. Candidates to assess (capabilities **not yet verified** by us):
- General geoscience data management and modelling suites (Seequent Central/Leapfrog, Datamine, GeoSoftware-class tools)
- AI prospectivity / exploration services (for example KoBold Metals, VRIFY's DORA, GoldSpot-type consultancies)
- Mining market-intelligence databases (S&P Global Market Intelligence and similar)
- Plain QGIS + spreadsheet workflows (the real incumbent for small consultancies)

| Hypothesis | How to test |
|---|---|
| Claim-level citations to page + offset + review status | Show the same dossier made both ways to 5 reviewers; measure time to verify 20 statements |
| Conflicting findings preserved, not resolved | Find cases in pilot data where sources disagree; check whether competitors surface them |
| Risks/gaps tied to concrete verification actions | Ask customers whether gap lists change their field programme |
| Sealed, separation-of-duties assessments | Ask investors and lenders whether tamper-evidence matters to their due-diligence process |
