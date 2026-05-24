#!/usr/bin/env python3
"""Quick end-to-end upload test — no Blender needed."""
import json, os, sys, tempfile, urllib.request, urllib.error

API_BASE = "https://renderfarm-web.vercel.app/api"

# ── 1. Login ──────────────────────────────────────────────────────────────────
email    = input("Email: ").strip()
password = input("Password: ").strip()

print("\n[1/4] Logging in…")
req = urllib.request.Request(
    f"{API_BASE}/auth/login",
    data=json.dumps({"email": email, "password": password}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    token = data.get("access_token") or data.get("token") or data.get("accessToken")
    print(f"     ✓ Logged in  (token length: {len(token)})")
except urllib.error.HTTPError as e:
    print(f"     ✗ Login failed: {e.code} {e.read().decode()}")
    sys.exit(1)

# ── 2. Get upload URL / client token ─────────────────────────────────────────
print("\n[2/4] Requesting upload URL…")
req = urllib.request.Request(
    f"{API_BASE}/upload-url",
    data=json.dumps({"filename": "test_scene.zip"}).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {token}"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    print(f"     ✓ Got upload URL")
    print(f"       pathname   : {resp.get('pathname')}")
    print(f"       uploadUrl  : {resp.get('uploadUrl','')[:80]}…")
    client_token = resp.get("clientToken")
    upload_url   = resp.get("uploadUrl")
    pathname     = resp.get("pathname")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"     ✗ upload-url failed: {e.code} {body}")
    sys.exit(1)

# ── 3. Upload a tiny fake zip ─────────────────────────────────────────────────
print("\n[3/4] Uploading test file to Vercel Blob…")
import zipfile, io
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("test.blend", b"fake blend file content for upload test")
fake_zip = buf.getvalue()
print(f"       file size: {len(fake_zip)} bytes")

req = urllib.request.Request(
    upload_url,
    data=fake_zip,
    headers={
        "Authorization": f"Bearer {client_token}",
        "Content-Type": "application/zip",
        "x-api-version": "7",
    },
    method="PUT",
)
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        result = json.loads(r.read())
    blob_url = result.get("url", upload_url)
    print(f"     ✓ Uploaded!")
    print(f"       blob URL: {blob_url}")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"     ✗ Blob upload failed: {e.code} {body[:300]}")
    sys.exit(1)

# ── 4. Create a test job pointing at the blob ─────────────────────────────────
print("\n[4/4] Creating test job…")
req = urllib.request.Request(
    f"{API_BASE}/jobs",
    data=json.dumps({
        "title":        "test_scene.blend",
        "frames":       "1-1",
        "software":     "blender-4-1",
        "blender_file": blob_url,
    }).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {token}"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        job = json.loads(r.read())
    print(f"     ✓ Job created: {job.get('jobNumber')}  (id={job.get('id')})")
except urllib.error.HTTPError as e:
    print(f"     ✗ Job creation failed: {e.code} {e.read().decode()}")
    sys.exit(1)

print("\n" + "="*55)
print("  ALL STEPS PASSED — upload pipeline is working!")
print("="*55)
