"""Ingest one real document through the running API and report what the pipeline produced.

Usage: python scripts/ingest_eval.py FILE "Title" --source-name NAME --licence-note TEXT [--year 1930] [--country CD]
Creates (or reuses) an evaluation organisation. Prints timings, per-page OCR stats and claim counts.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

BASE = os.environ.get("CBI_SMOKE_URL", "http://localhost:8000").rstrip("/") + "/api/v1"
EMAIL = os.environ.get("CBI_EVAL_EMAIL", "evaluation@example.com")
PASSWORD = os.environ.get("CBI_EVAL_PASSWORD", "Evaluation-Account-2026")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("title")
    ap.add_argument("--source-name", required=True)
    ap.add_argument("--licence-note", required=True)
    ap.add_argument("--access-status", default="authorised")
    ap.add_argument("--year", type=int)
    ap.add_argument("--country")
    ap.add_argument("--doc-type", default="map")
    a = ap.parse_args()

    c = httpx.Client(timeout=120, trust_env=False)
    r = c.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if r.status_code != 200:
        r = c.post(f"{BASE}/auth/register", json={"email": EMAIL, "full_name": "Evaluation", "password": PASSWORD,
                                                  "organisation_name": "Real-document evaluation"})
        r.raise_for_status()
    token = r.json()["access_token"]
    org = c.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["memberships"][0]["org_id"]
    h = {"Authorization": f"Bearer {token}", "X-Org-Id": org}

    src = next((s for s in c.get(f"{BASE}/sources", headers=h).json() if s["name"] == a.source_name), None)
    if src is None:
        src = c.post(f"{BASE}/sources", headers=h, json={"name": a.source_name, "access_status": a.access_status,
                                                       "licence": a.licence_note[:200], "notes": a.licence_note}).json()
    t0 = time.monotonic()
    with open(a.file, "rb") as f:
        data = {"source_id": src["id"], "title": a.title, "document_type": a.doc_type}
        if a.year:
            data["publication_year"] = str(a.year)
        if a.country:
            data["country"] = a.country
        up = c.post(f"{BASE}/documents", headers=h, data=data, files={"file": (os.path.basename(a.file), f, "application/pdf")})
    up.raise_for_status()
    doc = up.json()["document"]
    print(f"upload: {up.status_code} created={up.json()['created']} id={doc['id']} pages={doc['page_count']} "
          f"size={doc['size_bytes'] / 1e6:.1f} MB in {time.monotonic() - t0:.1f}s")
    t1 = time.monotonic()
    while True:
        st = c.get(f"{BASE}/documents/{doc['id']}/status", headers=h).json()
        if st["status"] in ("processed", "failed", "quarantined"):
            break
        if time.monotonic() - t1 > 1800:
            print("timeout waiting for processing")
            return 1
        time.sleep(3)
    print(f"processing: {st['status']} in {time.monotonic() - t1:.0f}s :: {st['last_processing'] or st['processing_error']}")
    if st["status"] != "processed":
        return 1
    for p in c.get(f"{BASE}/documents/{doc['id']}/pages", headers=h).json():
        print(f"  page {p['page_number']}: {p['extraction_method']} conf={p['ocr_confidence']} chars={p['char_count']}")
    claims = c.get(f"{BASE}/claims", headers=h, params={"document_id": doc["id"], "limit": 500}).json()
    by_type: dict[str, int] = {}
    for cl in claims["items"]:
        by_type[cl["claim_type"]] = by_type.get(cl["claim_type"], 0) + 1
    print("claims:", claims["total"], by_type)
    print(f"DOC_ID={doc['id']} ORG_ID={org}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
