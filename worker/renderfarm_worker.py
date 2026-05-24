#!/usr/bin/env python3
"""
Renderfarm Local Worker  v2.1
==============================
Polls the Renderfarm API for queued/pending jobs, renders them with the
local Blender installation, uploads frames back via /api/upload, and
marks the job done.

Submission modes supported
--------------------------
  v7 (current) — assets uploaded individually with SHA-256 dedup;
                 manifest stored in job with blob_url per asset.
  v6 (legacy)  — single .blend zip stored in blenderFile URL.

Quick start
-----------
  python renderfarm_worker.py

Auth token is read from the Blender addon's .rf_token file, or set the
RF_TOKEN environment variable manually.

Environment variables
---------------------
  BLENDER_PATH   Override the auto-detected Blender executable.
  RF_TOKEN       JWT auth token (overrides .rf_token file).
"""

import os
import sys
import json
import time
import socket
import zipfile
import tempfile
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

# Worker identity — sent to the API so the dashboard can show which machine is rendering
WORKER_HOSTNAME = socket.gethostname()

# ── Config ────────────────────────────────────────────────────────────────────
API_BASE      = "https://renderfarm-web.vercel.app/api"
POLL_INTERVAL = 15   # seconds between polls when idle

# Common Windows Blender installation paths (newest first)
BLENDER_SEARCH_PATHS = [
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 3.5\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 3.4\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 3.3\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 3.1\blender.exe",
]

# ── Token loading ─────────────────────────────────────────────────────────────
def _find_token_file():
    """Search for the .rf_token file written by the Blender addon."""
    candidates = [
        # Relative to this script
        os.path.join(os.path.dirname(__file__), "..", "blender-addon", ".rf_token"),
        # Blender addon scripts folders (Windows)
        os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\4.2\scripts\addons\.rf_token"),
        os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\4.1\scripts\addons\.rf_token"),
        os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\3.6\scripts\addons\.rf_token"),
        os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\3.5\scripts\addons\.rf_token"),
        os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\3.4\scripts\addons\.rf_token"),
        os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\3.3\scripts\addons\.rf_token"),
        os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\3.1\scripts\addons\.rf_token"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def load_token():
    # Env var overrides everything
    if "RF_TOKEN" in os.environ:
        return os.environ["RF_TOKEN"], os.environ.get("RF_EMAIL", "env-user")

    token_file = _find_token_file()
    if token_file:
        try:
            with open(token_file) as f:
                d = json.load(f)
                return d.get("token"), d.get("email")
        except Exception:
            pass
    return None, None


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _api(method, path, token, payload=None):
    data = json.dumps(payload).encode() if payload else None
    headers = {"Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def jobs_list(token):
    return _api("GET", "/jobs", token)


def job_update(job_id, token, **fields):
    return _api("PATCH", f"/jobs?id={job_id}", token, fields)


def download_file(url, dest_path, desc=""):
    """Stream-download a file from a URL to dest_path."""
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=600) as r:
        total = int(r.headers.get("Content-Length", 0))
        received = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                if total and desc:
                    pct = received * 100 // total
                    print(f"\r        {desc}: {pct}%  ", end="", flush=True)
    if total and desc:
        print()  # newline after progress


def upload_frame(frame_path, job_num, token):
    """Upload a rendered frame to Vercel Blob via our /api/upload route."""
    filename = os.path.basename(frame_path)
    # Prefix with job number so frames are grouped
    enc = urllib.parse.quote(f"{job_num}/{filename}")
    url = f"{API_BASE}/upload?filename={enc}"

    ext = os.path.splitext(filename)[1].lower()
    ct  = {".png": "image/png", ".jpg": "image/jpeg",
           ".exr": "image/x-exr"}.get(ext, "application/octet-stream")

    with open(frame_path, "rb") as f:
        data = f.read()

    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": ct},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        result = json.loads(r.read())
    return result["url"]


def upsert_task(job_num, frame_idx, token, status, frame_number=None, output_url="", worker_host=""):
    """PUT a task row to record per-frame timing and status (best-effort)."""
    try:
        path = f"/jobs/{job_num}/tasks/{frame_idx:03d}"
        _api("PUT", path, token, {
            "status":       status,
            "frame_number": frame_number if frame_number is not None else frame_idx + 1,
            "output_url":   output_url,
            "worker_host":  worker_host,
        })
    except Exception:
        pass  # non-critical


def post_logs(job_num, frame_idx, lines, token, level="info"):
    """POST log lines for a task to the task-logs endpoint (best-effort)."""
    if not lines:
        return
    try:
        path = f"/jobs/{job_num}/tasks/{frame_idx:03d}/logs"
        _api("POST", path, token, {"lines": lines, "level": level})
    except Exception:
        pass  # non-critical — console output is the source of truth


# ── GPU detection ─────────────────────────────────────────────────────────────
def detect_gpu():
    """
    Return a dict: { available: bool, names: [str], type: "nvidia"|"amd"|"none" }
    Checks NVIDIA via nvidia-smi, then AMD/Intel via wmic on Windows.
    """
    # ── NVIDIA ────────────────────────────────────────────────────────────────
    try:
        r = subprocess.run(
            ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            names = [n.strip() for n in r.stdout.strip().splitlines() if n.strip()]
            if names:
                return {'available': True, 'names': names, 'type': 'nvidia'}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # ── AMD / Intel (Windows wmic) ────────────────────────────────────────────
    try:
        r = subprocess.run(
            ['wmic', 'path', 'win32_VideoController', 'get', 'name'],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            lines = [l.strip() for l in r.stdout.strip().splitlines()
                     if l.strip() and l.strip().lower() != 'name']
            gpu_lines = [l for l in lines
                         if any(k in l.lower() for k in ('nvidia', 'radeon', 'amd', 'rtx', 'gtx', 'arc'))]
            if gpu_lines:
                gpu_type = 'nvidia' if any('nvidia' in g.lower() or 'rtx' in g.lower() or 'gtx' in g.lower()
                                           for g in gpu_lines) else 'amd'
                return {'available': True, 'names': gpu_lines, 'type': gpu_type}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    return {'available': False, 'names': [], 'type': 'none'}


# ── Blender detection ─────────────────────────────────────────────────────────
def find_blender():
    if "BLENDER_PATH" in os.environ:
        return os.environ["BLENDER_PATH"]
    for path in BLENDER_SEARCH_PATHS:
        if os.path.exists(path):
            return path
    import shutil
    return shutil.which("blender")


# ── Scene preparation: v6 (zip) ───────────────────────────────────────────────
def prepare_scene_v6(job, work_dir):
    """
    Legacy v6 mode: job.blenderFile is a zip URL.
    Downloads the zip and extracts it into work_dir.
    Returns the absolute path to the .blend file.
    """
    blend_url = job.get("blenderFile") or job.get("blender_file", "")
    zip_path  = os.path.join(work_dir, "scene.zip")

    print("  [1/3] Downloading scene zip…  (v6 mode)")
    download_file(blend_url, zip_path, "scene.zip")
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"        {size_mb:.1f} MB downloaded")

    print("  [2/3] Unzipping…")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work_dir)
    os.remove(zip_path)

    blend_files = list(Path(work_dir).glob("**/*.blend"))
    if not blend_files:
        raise RuntimeError("No .blend file found in the zip archive")
    blend_file = str(blend_files[0])
    print(f"        Blend: {os.path.basename(blend_file)}")
    return blend_file


# ── Scene preparation: v7 (per-asset manifest) ───────────────────────────────
def prepare_scene_v7(job, work_dir):
    """
    v7 mode: job.manifest.assets[] lists each file with its blob_url.
    Downloads each asset to work_dir/<relative_path>.
    Returns the absolute path to the .blend file.
    """
    manifest = job.get("manifest") or {}
    assets   = manifest.get("assets", [])

    if not assets:
        raise RuntimeError("Job manifest has no assets — cannot prepare scene")

    # Find the .blend entry
    blend_asset = next((a for a in assets if a.get("type") == "blend"), None)
    if not blend_asset:
        raise RuntimeError("No blend asset found in job manifest")

    total = len(assets)
    print(f"  [1/3] Downloading {total} assets…  (v7 mode)")

    for i, asset in enumerate(assets, 1):
        blob_url  = asset.get("blob_url", "")
        rel_path  = asset.get("path", "")
        fname     = os.path.basename(rel_path) or asset.get("name", f"asset_{i}")
        asset_type = asset.get("type", "?")

        if not blob_url:
            print(f"        [{i}/{total}] SKIP (no blob_url): {fname}")
            continue

        # Normalise Blender's // prefix to just the filename
        rel_clean = rel_path.lstrip("/").lstrip("\\")
        if not rel_clean:
            rel_clean = fname

        dest = os.path.join(work_dir, rel_clean)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        size_bytes = asset.get("size_bytes", 0)
        size_str   = (
            f"{size_bytes / 1024 / 1024:.1f} MB" if size_bytes >= 1024 * 1024
            else f"{size_bytes / 1024:.1f} KB" if size_bytes >= 1024
            else f"{size_bytes} B"
        ) if size_bytes else ""

        print(f"        [{i}/{total}] {asset_type}: {fname} {size_str}")
        download_file(blob_url, dest)

    # Locate the downloaded .blend file
    blend_rel  = blend_asset.get("path", "").lstrip("/").lstrip("\\")
    blend_name = blend_asset.get("name", os.path.basename(blend_rel))
    blend_file = os.path.join(work_dir, blend_rel) if blend_rel else None

    if not blend_file or not os.path.isfile(blend_file):
        # Fallback: search for any .blend
        found = list(Path(work_dir).glob("**/*.blend"))
        if not found:
            raise RuntimeError("Could not locate downloaded .blend file")
        blend_file = str(found[0])

    print(f"        Blend: {os.path.basename(blend_file)}")
    return blend_file


# ── Job rendering ─────────────────────────────────────────────────────────────
def render_job(job, blender_path, token):
    job_id  = job["id"]
    # Accept both camelCase (API) and snake_case (raw DB)
    job_num   = job.get("jobNumber") or job.get("job_number", "?")
    blend_url = job.get("blenderFile") or job.get("blender_file", "")
    frames    = job.get("frames", "1-1")

    # v7 manifest takes precedence for frame range
    manifest = job.get("manifest") or {}
    if manifest and "frame_start" in manifest and "frame_end" in manifest:
        frames = f"{manifest['frame_start']}-{manifest['frame_end']}"

    # Detect submission mode
    assets       = manifest.get("assets", [])
    has_blob_url = any(a.get("blob_url") for a in assets)
    use_v7       = bool(assets and has_blob_url)

    # Instance type from manifest (set by Blender addon v7+)
    instance_type = manifest.get("instance_type", "CPU").upper()  # "GPU" or "CPU"
    requires_gpu  = instance_type == "GPU"

    print(f"\n{'='*60}")
    print(f"  Job  : {job_num}")
    print(f"  Title: {job.get('title', '?')}")
    print(f"  Frames: {frames}")
    print(f"  Mode : {'v7 (manifest)' if use_v7 else 'v6 (zip)'}")
    print(f"  Instance: {instance_type}")
    print(f"{'='*60}")

    # ── GPU availability check ────────────────────────────────────────────────
    if requires_gpu:
        gpu = detect_gpu()
        if not gpu['available']:
            print(f"  ⚠  Job {job_num} requires GPU but none detected on this machine.")
            print(f"     Putting job on hold — start a GPU-capable worker to release it.")
            job_update(job_id, token,
                       status="holding",
                       worker_host=WORKER_HOSTNAME,
                       status_description=f"No GPU available on worker '{WORKER_HOSTNAME}'. "
                                          f"Waiting for a GPU-capable render node.")
            return
        else:
            print(f"  GPU: {', '.join(gpu['names'])} ✓")

    if not use_v7 and not blend_url:
        print("  ERROR: no blenderFile URL and no manifest assets — skipping")
        job_update(job_id, token,
                   status="failed",
                   worker_host=WORKER_HOSTNAME,
                   status_description="No scene file found — blenderFile URL and manifest are both empty.")
        return

    # Mark running — record which worker picked up this job
    job_update(job_id, token,
               status="running",
               worker_host=WORKER_HOSTNAME,
               status_description=f"Rendering on {WORKER_HOSTNAME}")

    # Create per-frame task rows with started_at = NOW()
    # (all frames share one Blender process, so start time is approximate)
    total_frames = int(end) - int(start) + 1
    for fi in range(total_frames):
        upsert_task(job_num, fi, token,
                    status="running",
                    frame_number=int(start) + fi,
                    worker_host=WORKER_HOSTNAME)

    with tempfile.TemporaryDirectory(prefix="rf_work_") as work_dir:
        # ── Download scene ────────────────────────────────────────────
        if use_v7:
            blend_file = prepare_scene_v7(job, work_dir)
        else:
            blend_file = prepare_scene_v6(job, work_dir)

        # ── Render ────────────────────────────────────────────────────
        output_dir = os.path.join(work_dir, "renders")
        os.makedirs(output_dir, exist_ok=True)
        output_pattern = os.path.join(output_dir, "frame_####")

        parts = frames.replace(" ", "").split("-")
        start = parts[0]
        end   = parts[-1] if len(parts) > 1 else parts[0]

        print(f"  [2/3] Rendering frames {start}–{end} with Blender…")
        cmd = [
            blender_path,
            "--background", blend_file,
            "--render-output", output_pattern,
            "--render-format", "PNG",   # explicit — don't rely on scene setting
            "--frame-start", start,
            "--frame-end",   end,
            "--render-anim",
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=7200,   # 2-hour safety limit
        )

        # Print last 20 lines of Blender output for debugging
        blender_out = proc.stdout or ""
        all_log_lines = blender_out.strip().splitlines()
        for line in all_log_lines[-20:]:
            print(f"    {line}")

        # POST full Blender output to the task-log API (frame index 0)
        # The dashboard task-log page will display these lines live.
        if all_log_lines:
            info_lines  = [l for l in all_log_lines
                           if not any(k in l.lower() for k in ("error", "exception", "traceback"))]
            error_lines = [l for l in all_log_lines
                           if any(k in l.lower() for k in ("error", "exception", "traceback"))]
            # Frame index 0 = the first task row in the dashboard
            frame_start_idx = int(start) - (int(start) if manifest.get("frame_start") else 0)
            frame_start_idx = 0  # post to task 0 — all frames share one render process
            post_logs(job_num, frame_start_idx, info_lines,  token, level="info")
            post_logs(job_num, frame_start_idx, error_lines, token, level="error")

        if proc.returncode != 0:
            raise RuntimeError(f"Blender exited with code {proc.returncode}")

        # ── Upload frames ─────────────────────────────────────────────
        frame_files = sorted(
            f for f in Path(output_dir).iterdir()
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".exr")
        )

        if not frame_files:
            raise RuntimeError("Blender produced no output frames")

        print(f"  [3/3] Uploading {len(frame_files)} frame(s) to Vercel Blob…")
        frame_urls = []
        for i, frame_file in enumerate(frame_files, 1):
            url = upload_frame(str(frame_file), job_num, token)
            frame_urls.append(url)
            frame_idx = i - 1  # 0-based
            # Record per-frame completed_at (time of upload = proxy for render done)
            upsert_task(job_num, frame_idx, token,
                        status="done",
                        frame_number=int(start) + frame_idx,
                        output_url=url,
                        worker_host=WORKER_HOSTNAME)
            print(f"        [{i}/{len(frame_files)}] {frame_file.name}")

        # ── Done ──────────────────────────────────────────────────────
        # "success" = all tasks rendered; "downloaded" = Companion App pulled frames
        job_update(job_id, token,
                   status="success",
                   outputs=frame_urls,
                   status_description=f"Completed on {WORKER_HOSTNAME} — {len(frame_urls)} frames rendered.")
        print(f"\n  ✓ Job {job_num} complete — {len(frame_urls)} frames uploaded")


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  Renderfarm Local Worker  v2.0  (supports v6 + v7 jobs)")
    print("=" * 60)

    token, email = load_token()
    if not token:
        print("\nERROR: No auth token found.")
        print("  1. Sign in via the Blender addon (click Connect) first, or")
        print("  2. Set the RF_TOKEN environment variable manually.")
        sys.exit(1)

    blender = find_blender()
    if not blender:
        print("\nERROR: Blender executable not found.")
        print("  Install Blender, or set the BLENDER_PATH environment variable.")
        print(f"  Searched: {BLENDER_SEARCH_PATHS[0]}")
        sys.exit(1)

    print(f"\n  API:     {API_BASE}")
    print(f"  User:    {email}")
    print(f"  Blender: {blender}")
    print(f"  Polling every {POLL_INTERVAL}s for pending/queued jobs…")
    print()

    # Detect GPU once at startup for reporting
    gpu_info = detect_gpu()
    if gpu_info['available']:
        print(f"  GPU:     {', '.join(gpu_info['names'])}")
    else:
        print(f"  GPU:     None detected — GPU jobs will be put on hold")
    print()

    while True:
        try:
            all_jobs = jobs_list(token)

            # If this worker has a GPU, unhold any jobs that are holding because
            # of GPU unavailability on a previous worker run
            if gpu_info['available']:
                holding = [j for j in all_jobs if j.get("status") == "holding"]
                for hj in holding:
                    mf = hj.get("manifest") or {}
                    if mf.get("instance_type", "CPU").upper() == "GPU":
                        jnum = hj.get("jobNumber") or hj.get("job_number", hj["id"])
                        print(f"  Releasing held GPU job {jnum} -> pending")
                        job_update(hj["id"], token, status="pending")

            # Pick up pending (12-status canonical) or queued (legacy addon v7.0)
            PICKUP = {"pending", "queued"}
            ready = [j for j in all_jobs if j.get("status") in PICKUP]

            if ready:
                # Oldest job first (lowest id)
                job = sorted(ready, key=lambda j: int(j["id"]))[0]
                try:
                    render_job(job, blender, token)
                except Exception as e:
                    jnum = job.get("jobNumber") or job.get("job_number", job["id"])
                    print(f"\n  ERROR rendering {jnum}: {e}")
                    import traceback; traceback.print_exc()
                    try:
                        job_update(job["id"], token,
                                   status="failed",
                                   worker_host=WORKER_HOSTNAME,
                                   status_description=f"Failed on {WORKER_HOSTNAME}: {str(e)[:200]}")
                    except Exception:
                        pass
            else:
                running  = sum(1 for j in all_jobs if j.get("status") == "running")
                holding_ = sum(1 for j in all_jobs if j.get("status") == "holding")
                total    = len(all_jobs)
                parts_   = [f"{total} jobs total", f"{running} running"]
                if holding_:
                    parts_.append(f"{holding_} holding")
                parts_.append("0 ready")
                print(f"  Idle — {', '.join(parts_)}. Next check in {POLL_INTERVAL}s…", end="\r")

        except urllib.error.HTTPError as e:
            print(f"\n  API error: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            print(f"\n  Network error: {e.reason}")
        except Exception as e:
            print(f"\n  Unexpected error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
