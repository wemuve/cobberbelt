"""Post-deployment smoke test against a running API + worker.

Usage:  CBI_SMOKE_URL=http://localhost:8000 python scripts/smoke.py
Creates a throwaway organisation and uploads a SYNTHETIC fixture document.
Exits non-zero on any failure. Does not call billable providers.
"""

from __future__ import annotations

import os
import sys
import time
import uuid

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
from fixtures import ENGLISH_PAGE, FRENCH_PAGE, make_text_pdf  # noqa: E402

BASE = os.environ.get("CBI_SMOKE_URL", "http://localhost:8000").rstrip("/") + "/api/v1"


def main() -> int:
    c = httpx.Client(timeout=30, trust_env=False)
    ready = c.get(f"{BASE}/ready")
    print("ready:", ready.status_code, ready.json()["status"])
    if ready.status_code != 200:
        return 1
    email = f"smoke-{uuid.uuid4().hex[:6]}@example.com"
    r = c.post(f"{BASE}/auth/register", json={"email": email, "full_name": "Smoke", "password": "Smoke-Test-Pass-42",
                                              "organisation_name": "Smoke Org"})
    r.raise_for_status()
    token = r.json()["access_token"]
    org = c.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["memberships"][0]["org_id"]
    h = {"Authorization": f"Bearer {token}", "X-Org-Id": org}
    src = c.post(f"{BASE}/sources", headers=h, json={"name": "Synthetic smoke source", "access_status": "public_domain"}).json()["id"]
    up = c.post(f"{BASE}/documents", headers=h, data={"source_id": src, "title": "SYNTHETIC smoke report"},
                files={"file": ("smoke.pdf", make_text_pdf([FRENCH_PAGE, ENGLISH_PAGE]), "application/pdf")})
    up.raise_for_status()
    doc = up.json()["document"]["id"]
    print("uploaded:", doc)
    st: dict = {}
    for _ in range(60):
        st = c.get(f"{BASE}/documents/{doc}/status", headers=h).json()
        if st["status"] in ("processed", "failed"):
            break
        time.sleep(1)
    print("processing:", st["status"], st.get("last_processing"))
    if st["status"] != "processed":
        print("FAIL: worker did not process the document (is the worker running?)")
        return 1
    res = c.post(f"{BASE}/search", headers=h, json={"q": "cuivre"}).json()
    print("search hits:", res["pages"]["total"], "modes:", res["retrieval_modes"])
    if res["pages"]["total"] < 2:
        print("FAIL: expected both fixture pages to match")
        return 1
    print("providers:", [(p["id"], p["status"]) for p in c.get(f"{BASE}/providers", headers=h).json()["providers"]])
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
