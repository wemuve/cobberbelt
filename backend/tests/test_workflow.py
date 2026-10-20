"""End-to-end first-release workflow with synthetic fixtures:
ingest → process (text layer + OCR) → claims → review → feature → search → project → assessment."""

from __future__ import annotations

from conftest import add_member, make_source, upload
from fixtures import ENGLISH_PAGE, FRENCH_PAGE, SYNTHETIC_MARKER, make_png, make_scanned_pdf, make_text_pdf

from copperbelt.jobs.queue import drain


def test_text_pdf_is_processed_with_page_level_evidence(owner):
    src = make_source(owner)
    up = upload(owner, src, make_text_pdf([FRENCH_PAGE, ENGLISH_PAGE]), publication_year=1962, country="CD")
    doc_id = up["document"]["id"]
    assert up["created"] and up["job_id"]
    assert up["document"]["status"] == "queued"
    assert up["document"]["page_count"] == 2

    assert drain() == 1
    status = owner.get(f"/api/v1/documents/{doc_id}/status").json()
    assert status["status"] == "processed", status
    assert status["job"]["status"] == "succeeded"
    assert status["last_processing"]["pages"] == 2
    assert status["last_processing"]["ocr_pages"] == 0
    assert status["last_processing"]["malware_scan"] == "not_configured"

    p1 = owner.get(f"/api/v1/documents/{doc_id}/pages/1").json()
    assert "Kambove Fictif" in p1["text"] and p1["extraction_method"] == "text_layer"
    assert p1["detected_language"] in ("fr", None)

    claims = owner.get("/api/v1/claims", params={"document_id": doc_id}).json()
    types = {c["claim_type"] for c in claims["items"]}
    assert {"coordinate", "mineral_occurrence", "drilling_reference"} <= types
    coord = next(c for c in claims["items"] if c["claim_type"] == "coordinate" and c["page_number"] == 1)
    assert coord["attributes"]["lat"] == -10.8 and coord["attributes"]["lon"] == 26.6
    assert coord["attributes"]["datum"] == "unknown"
    assert coord["review_status"] == "unreviewed" and coord["extraction_method"] == "rule"
    page_text = p1["text"]
    assert coord["statement"] == page_text[coord["char_start"] : coord["char_end"]]


def test_duplicate_upload_is_detected_by_checksum(owner):
    src = make_source(owner)
    data = make_text_pdf([FRENCH_PAGE])
    first = upload(owner, src, data)
    second = upload(owner, src, data, title="Same bytes again")
    assert second["created"] is False
    assert second["document"]["id"] == first["document"]["id"]


def test_scanned_pdf_goes_through_ocr(owner):
    src = make_source(owner)
    lines = [SYNTHETIC_MARKER, "Copper occurrence with malachite", "near the old shaft at 10°50' S, 26°40' E"]
    doc = upload(owner, src, make_scanned_pdf(lines), filename="scan.pdf")["document"]
    drain()
    page = owner.get(f"/api/v1/documents/{doc['id']}/pages/1").json()
    assert page["extraction_method"] == "ocr"
    assert page["ocr_confidence"] is not None and page["ocr_confidence"] > 50
    assert "malachite" in page["text"].lower()


def test_png_upload_is_ocr_d(owner):
    src = make_source(owner)
    doc = upload(owner, src, make_png([SYNTHETIC_MARKER, "Cobalt heterogenite showing"]), filename="photo.png")["document"]
    assert doc["content_type"] == "image/png"
    drain()
    assert "heterogenite" in owner.get(f"/api/v1/documents/{doc['id']}/pages/1").json()["text"].lower()


def test_full_first_release_workflow(owner):
    reviewer = add_member(owner, "reviewer")
    src = make_source(owner)
    doc_id = upload(owner, src, make_text_pdf([FRENCH_PAGE, ENGLISH_PAGE]), country="CD")["document"]["id"]
    drain()

    # --- search in French finds English pages via commodity expansion, and vice versa
    res = owner.post("/api/v1/search", json={"q": "cuivre"}).json()
    pages = {(h["document_id"], h["page_number"]) for h in res["pages"]["items"]}
    assert (doc_id, 1) in pages and (doc_id, 2) in pages
    assert "full_text" in res["retrieval_modes"]
    assert any("semantic:unavailable" in m for m in res["retrieval_modes"])
    hit = res["pages"]["items"][0]
    assert hit["evidence_url"].startswith(f"/api/v1/documents/{hit['document_id']}/pages/")

    # --- review coordinate claims: one supports, the later survey gives a different position
    claims = owner.get("/api/v1/claims", params={"document_id": doc_id, "claim_type": "coordinate"}).json()["items"]
    c1 = next(c for c in claims if c["page_number"] == 1)
    c2 = next(c for c in claims if c["page_number"] == 2)
    r = reviewer.post(f"/api/v1/claims/{c1['id']}/review", json={"decision": "accepted"})
    assert r.status_code == 200 and r.json()["review_status"] == "accepted"
    assert reviewer.post(f"/api/v1/claims/{c2['id']}/review", json={"decision": "rejected"}).status_code == 422  # comment required

    # Unreviewed claims cannot become features.
    assert owner.post(f"/api/v1/claims/{c2['id']}/promote", json={"name": "x"}).status_code == 409

    feat = owner.post(f"/api/v1/claims/{c1['id']}/promote", json={"name": "Kambove Fictif", "commodities": ["cuivre", "cobalt"]})
    assert feat.status_code == 201, feat.text
    f = feat.json()
    assert f["geometry"]["type"] == "Point" and f["geometry"]["coordinates"] == [26.6, -10.8]
    assert f["commodities"] == ["cobalt", "copper"]
    assert f["uncertainty_m"] == 1850.0  # minute-level stated precision
    assert f["location_precision"] == "reported_coordinates"
    assert any("datum unknown" in w.lower() for w in f["warnings"])

    # Preserve the conflicting later location rather than resolving it silently.
    r = owner.post(
        f"/api/v1/geological-features/{f['id']}/evidence",
        json={"claim_id": c2["id"], "relation": "contradicts", "note": "Later survey gives a position ~2 km away"},
    )
    assert r.status_code == 201
    evidence = r.json()
    assert evidence["summary"]["has_conflict"] is True
    assert evidence["summary"]["contradicting"] == 1 and evidence["summary"]["independently_verified"] == 0
    assert all(i["evidence_url"] for i in evidence["items"])

    # Map layer carries uncertainty and conflict flags.
    fc = owner.get("/api/v1/geo/features").json()
    props = fc["features"][0]["properties"]
    assert props["has_conflict"] is True and props["uncertainty_known"] is True

    # --- project workspace with AOI
    proj = owner.post("/api/v1/projects", json={"name": "Kambove screening", "country": "CD", "commodities": ["copper"]}).json()
    aoi = owner.post(
        "/api/v1/areas-of-interest",
        json={
            "name": "Licence area (synthetic)",
            "project_id": proj["id"],
            "geometry": {"type": "Polygon", "coordinates": [[[26.5, -10.9], [26.7, -10.9], [26.7, -10.7], [26.5, -10.7], [26.5, -10.9]]]},
        },
    )
    assert aoi.status_code == 201, aoi.text
    assert 400 < aoi.json()["area_km2"] < 500  # ~0.2° × 0.2° at 10.8°S
    owner.post(f"/api/v1/projects/{proj['id']}/documents", json={"ids": [doc_id]})

    risk = owner.post(
        f"/api/v1/projects/{proj['id']}/risks",
        json={
            "title": "Occurrence position uncertain",
            "category": "data_quality",
            "severity": 3,
            "likelihood": 4,
            "claim_ids": [c1["id"], c2["id"]],
            "verification_action": "Field GPS check of the old workings",
        },
    )
    assert risk.status_code == 201, risk.text
    gap = owner.post(
        f"/api/v1/projects/{proj['id']}/gaps",
        json={"topic": "Current licence status", "verification_action": "Request extract from the Cadastre Minier"},
    )
    assert gap.status_code == 201

    dossier = owner.get(f"/api/v1/projects/{proj['id']}/dossier").json()
    assert dossier["features"][0]["name"] == "Kambove Fictif" and dossier["features"][0]["in_areas"] == ["Licence area (synthetic)"]
    assert dossier["features"][0]["has_conflict"] is True
    assert dossier["risks"][0]["score"] == 12 and dossier["risks"][0]["band"] == "high"
    assert any("conflicting" in g for g in dossier["detected_gaps"])
    assert any("historical or unverified" in g for g in dossier["detected_gaps"])
    assert any("cadastre" in n.lower() for n in dossier["not_checked"])

    # --- assessment: draft → review → approve by a different user; then immutable
    a = owner.post(f"/api/v1/projects/{proj['id']}/assessments", json={"title": "Screening v1"}).json()
    assert a["version"] == 1 and len(a["content_sha256"]) == 64
    assert "not a mineral resource" in a["snapshot"]["disclaimer"]
    base = f"/api/v1/projects/{proj['id']}/assessments/{a['id']}"
    assert owner.post(f"{base}/status", json={"status": "in_review"}).status_code == 200
    # author cannot approve own work
    assert owner.post(f"{base}/status", json={"status": "approved", "reviewer_conclusion": "ok"}).status_code == 403

    admin = add_member(owner, "admin")
    owner.post(f"/api/v1/projects/{proj['id']}/members", json={"user_id": admin.user_id, "role": "contributor"})
    assert admin.post(f"{base}/status", json={"status": "approved"}).status_code == 422  # conclusion required
    ok = admin.post(
        f"{base}/status", json={"status": "approved", "reviewer_conclusion": "Position must be field-verified before further work."}
    )
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    got = owner.get(base).json()
    assert got["integrity_verified"] is True
    assert admin.post(f"{base}/status", json={"status": "in_review"}).status_code == 409

    # Audit trail recorded the chain.
    actions = {e["action"] for e in owner.get("/api/v1/audit-events", params={"limit": 200}).json()["items"]}
    assert {
        "document.uploaded",
        "document.processed",
        "claim.reviewed",
        "feature.promoted_from_claim",
        "assessment.created",
        "assessment.approved",
    } <= actions
