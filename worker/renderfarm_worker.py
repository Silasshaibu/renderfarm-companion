#!/usr/bin/env python3
"""
Renderfarm Local Worker  v2.2
==============================
Polls the Renderfarm API for queued/pending jobs, renders them with the
local Blender installation, uploads frames back via /api/upload, and
marks the job done.

Submission modes supported
--------------------------
  v7 (current) -- assets uploaded individually with SHA-256 dedup;
                  manifest stored in job with blob_url per asset.
  v6 (legacy)  -- single .blend zip stored in blenderFile URL.

Quick start
-----------
  python renderfarm_worker.py             # render worker (default)
  python renderfarm_worker.py --companion # auto-download daemon

Companion mode
--------------
  Polls for jobs in status "success", downloads every frame to the local
  output_path recorded in the job manifest (or ~/RenderFarm/downloads/<jobNum>/
  if the manifest path is not set), then marks the job "downloaded".

  Useful when rendering on a remote/headless machine and you want frames
  automatically pulled to your local workstation.

Auth token is read from the Blender addon's .rf_token file, or set the
RF_TOKEN environment variable manually.

Environment variables
---------------------
  BLENDER_PATH        Override the auto-detected Blender executable.
  RF_TOKEN            JWT auth token (overrides .rf_token file).
  RF_DOWNLOAD_DIR     Default download root for companion mode.
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
API_BASE      = os.environ.get("RF_API_BASE", "https://renderfarm.swade-art.com/api")
POLL_INTERVAL = 15   # seconds between polls when idle

# Common Windows Blender installation paths (newest first)
BLENDER_SEARCH_PATHS = [
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
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


def fetch_wrangler_settings(token):
    """Fetch the wrangler settings dict; returns {} on any error."""
    try:
        return _api("GET", "/wrangler-settings", token)
    except Exception:
        return {}


def post_wrangler_event(job_num, wrangler, action, detail, token):
    """Write a wrangler action event to the dashboard (best-effort)."""
    try:
        _api("POST", "/wrangler-events", token, {
            "wrangler":   wrangler,
            "job_number": str(job_num),
            "action":     action,
            "detail":     detail,
        })
    except Exception:
        pass  # non-critical — don't interrupt the render flow


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
    Legacy v6 mode: job.blenderFile is either a URL (zip) or a local file path.
    - Local .blend path  → use it directly (worker on same machine as submitter)
    - Local .zip path    → extract it into work_dir
    - Remote URL         → download zip, then extract
    Returns the absolute path to the .blend file.
    """
    blend_url = job.get("blenderFile") or job.get("blender_file", "")

    # ── Local file path (C:\... or /path/to/file) ─────────────────────────────
    if os.path.isabs(blend_url) or (len(blend_url) > 1 and blend_url[1] == ':'):
        if blend_url.lower().endswith(".blend") and os.path.isfile(blend_url):
            print(f"  [1/3] Using local .blend directly: {blend_url}")
            return blend_url
        elif blend_url.lower().endswith(".zip") and os.path.isfile(blend_url):
            print(f"  [1/3] Extracting local zip: {blend_url}")
            with zipfile.ZipFile(blend_url) as z:
                z.extractall(work_dir)
            blend_files = list(Path(work_dir).glob("**/*.blend"))
            if not blend_files:
                raise RuntimeError("No .blend file found in local zip")
            return str(blend_files[0])
        else:
            raise RuntimeError(f"Local file not found: {blend_url}")

    # ── Remote URL → download zip ─────────────────────────────────────────────
    zip_path = os.path.join(work_dir, "scene.zip")
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


# ── Frame-range parser ────────────────────────────────────────────────────────
def _parse_frames(frames_str):
    """
    Parse a frames string into a sorted list of frame numbers.

      "1-100"     → [1, 2, ..., 100]   contiguous range  (is_contiguous=True)
      "1,25,100"  → [1, 25, 100]       sparse / scout    (is_contiguous=False)
      "50"        → [50]               single frame      (is_contiguous=True)

    Returns:
        (frame_list: list[int], is_contiguous: bool)
    """
    clean = frames_str.replace(" ", "")
    if "," in clean:
        frame_list = sorted(set(int(f) for f in clean.split(",") if f.isdigit()))
        return frame_list, False
    if "-" in clean:
        parts = clean.split("-")
        start = int(parts[0])
        end   = int(parts[1]) if len(parts) > 1 else start
        return list(range(start, end + 1)), True
    return [int(clean)], True


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

    # ── Fetch wrangler settings (best-effort) ────────────────────────────────
    ws = fetch_wrangler_settings(token)
    max_runtime_on  = bool(ws.get("maxRuntimeOn", True))
    max_runtime_hrs = float(ws.get("maxRuntime", 2))     # hours; default 2h
    runtime_action  = str(ws.get("runtimeAction", "Kill"))

    # Hard cap: 12 hours; minimum: 10 minutes
    max_runtime_secs = max(600, min(43200, int(max_runtime_hrs * 3600)))
    effective_timeout = max_runtime_secs if max_runtime_on else 7200

    print(f"\n{'='*60}")
    print(f"  Job  : {job_num}")
    print(f"  Title: {job.get('title', '?')}")
    print(f"  Frames: {frames}")
    print(f"  Mode : {'v7 (manifest)' if use_v7 else 'v6 (zip)'}")
    print(f"  Instance: {instance_type}")
    print(f"  Max runtime: {max_runtime_hrs}h ({effective_timeout}s)")
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
            post_wrangler_event(
                job_num, "GPU Hold", "Job held",
                f"No GPU available on worker '{WORKER_HOSTNAME}' — job put on hold.",
                token,
            )
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

    # Parse frame list FIRST — handles both "1-100" ranges and "1,25,100" scout lists
    frame_list, is_contiguous = _parse_frames(frames)
    total_frames = len(frame_list)
    start = str(frame_list[0])
    end   = str(frame_list[-1])

    # Mark running — record which worker picked up this job
    job_update(job_id, token,
               status="running",
               worker_host=WORKER_HOSTNAME,
               status_description=f"Rendering on {WORKER_HOSTNAME}")

    # Create per-frame task rows with started_at = NOW()
    for fi, frame_num in enumerate(frame_list):
        upsert_task(job_num, fi, token,
                    status="running",
                    frame_number=frame_num,
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

        all_log_lines = []

        if is_contiguous:
            # ── Normal mode: one Blender call for the full range ──────────────
            print(f"  [2/3] Rendering frames {start}–{end} with Blender…")
            cmd = [
                blender_path,
                "--background", blend_file,
                "--render-output", output_pattern,
                "--render-format", "PNG",
                "--frame-start", start,
                "--frame-end",   end,
                "--render-anim",
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=effective_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                hrs = effective_timeout / 3600
                msg = (f"Blender exceeded the {hrs:.1f}h max runtime "
                       f"({runtime_action}) — job killed.")
                print(f"\n  ⚠  {msg}")
                post_wrangler_event(
                    job_num, "Max Frame/Task Runtime", "Task killed",
                    msg, token,
                )
                raise RuntimeError(msg) from exc
            all_log_lines = (proc.stdout or "").strip().splitlines()
            for line in all_log_lines[-20:]:
                print(f"    {line}")
            if proc.returncode != 0:
                raise RuntimeError(f"Blender exited with code {proc.returncode}")

        else:
            # ── Scout mode: one Blender call per specific frame ───────────────
            # Per-frame timeout = effective_timeout / total_frames (min 10 min)
            per_frame_timeout = max(600, effective_timeout // max(total_frames, 1))
            print(f"  [2/3] Scout render — {total_frames} frame(s): {', '.join(str(f) for f in frame_list)}")
            for fi, frame_num in enumerate(frame_list):
                print(f"        [{fi + 1}/{total_frames}] frame {frame_num}…")
                cmd = [
                    blender_path,
                    "--background", blend_file,
                    "--render-output", output_pattern,
                    "--render-format", "PNG",
                    "--render-frame", str(frame_num),
                ]
                try:
                    proc = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=per_frame_timeout,
                    )
                except subprocess.TimeoutExpired as exc:
                    hrs = per_frame_timeout / 3600
                    msg = (f"Frame {frame_num} exceeded {hrs:.1f}h limit — killed.")
                    print(f"\n  ⚠  {msg}")
                    post_wrangler_event(
                        job_num, "Max Frame/Task Runtime", "Task killed", msg, token,
                    )
                    raise RuntimeError(msg) from exc
                lines = (proc.stdout or "").strip().splitlines()
                all_log_lines.extend(lines)
                for line in lines[-5:]:
                    print(f"    {line}")
                if proc.returncode != 0:
                    raise RuntimeError(f"Blender exited {proc.returncode} on frame {frame_num}")

        # POST Blender output to task-log API (task index 0, best-effort)
        if all_log_lines:
            info_lines  = [l for l in all_log_lines
                           if not any(k in l.lower() for k in ("error", "exception", "traceback"))]
            error_lines = [l for l in all_log_lines
                           if any(k in l.lower() for k in ("error", "exception", "traceback"))]
            post_logs(job_num, 0, info_lines,  token, level="info")
            post_logs(job_num, 0, error_lines, token, level="error")

        # ── Upload frames ─────────────────────────────────────────────
        # Collect rendered files in frame-number order (Blender names them frame_XXXX.*)
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
            frame_idx = i - 1  # 0-based position in this job
            # Use the actual Blender frame number from frame_list (not start+fi)
            actual_frame_num = frame_list[frame_idx] if frame_idx < len(frame_list) else frame_list[-1]
            upsert_task(job_num, frame_idx, token,
                        status="done",
                        frame_number=actual_frame_num,
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


# ── Companion auto-download daemon ────────────────────────────────────────────
COMPANION_INTERVAL = 20   # seconds between polls in companion mode


def _default_download_root():
    """Return RF_DOWNLOAD_DIR env var, or ~/RenderFarm/downloads."""
    env = os.environ.get("RF_DOWNLOAD_DIR", "")
    if env:
        return env
    return os.path.join(str(Path.home()), "RenderFarm", "downloads")


def companion_loop(token):
    """
    Companion daemon: poll for success jobs, download frames, mark downloaded.
    Runs forever until interrupted.
    """
    dl_root = _default_download_root()
    print("=" * 60)
    print("  Renderfarm Companion  v2.2  (auto-download daemon)")
    print("=" * 60)
    print(f"\n  API:           {API_BASE}")
    print(f"  Download root: {dl_root}")
    print(f"  Polling every {COMPANION_INTERVAL}s for 'success' jobs…")
    print()

    # Track per-job retry state: { job_id: (attempts, next_retry_time) }
    already_downloaded: set = set()
    retry_state: dict = {}   # job_id -> {'attempts': int, 'next_retry': float}

    MAX_RETRIES     = 5
    RETRY_DELAYS    = [30, 60, 120, 300, 600]  # seconds between retries (exponential)

    while True:
        try:
            all_jobs = jobs_list(token)
            now = time.time()
            success_jobs = [
                j for j in all_jobs
                if j.get("status") == "success"
                and str(j.get("id", "")) not in already_downloaded
                and now >= retry_state.get(str(j.get("id", "")), {}).get("next_retry", 0)
            ]

            if success_jobs:
                for job in success_jobs:
                    job_id  = str(job.get("id", ""))
                    job_num = job.get("jobNumber") or job.get("job_number", job_id)
                    title   = job.get("title", "Untitled")

                    rs       = retry_state.get(job_id, {"attempts": 0, "next_retry": 0})
                    attempts = rs["attempts"]

                    if attempts >= MAX_RETRIES:
                        print(f"  ! Job {job_num}: max retries reached — skipping permanently")
                        already_downloaded.add(job_id)
                        continue

                    # Determine local output directory
                    manifest    = job.get("manifest") or {}
                    output_path = manifest.get("output_path", "").strip()
                    local_dir   = output_path if output_path else os.path.join(dl_root, job_num)

                    # Frame URLs stored in job.outputs[]
                    outputs = job.get("outputs") or []
                    if isinstance(outputs, str):
                        try:
                            outputs = json.loads(outputs)
                        except Exception:
                            outputs = []

                    if not outputs:
                        print(f"  Job {job_num} ({title}): no output URLs — skipping")
                        already_downloaded.add(job_id)
                        continue

                    if attempts > 0:
                        print(f"\n  Retrying job {job_num} (attempt {attempts + 1}/{MAX_RETRIES}) — {title}")
                    else:
                        print(f"\n  Downloading job {job_num} — {title}")
                    print(f"    {len(outputs)} frame(s) -> {local_dir}")
                    os.makedirs(local_dir, exist_ok=True)

                    failed = 0
                    for i, url in enumerate(outputs, 1):
                        fname = f"frame_{i:04d}{_ext_from_url(url)}"
                        dest  = os.path.join(local_dir, fname)
                        # Skip frames already successfully downloaded
                        if os.path.exists(dest) and os.path.getsize(dest) > 0:
                            print(f"    [{i}/{len(outputs)}] {fname}  (already present)")
                            continue
                        try:
                            download_file(url, dest, desc=f"frame {i}/{len(outputs)}")
                            size_mb = os.path.getsize(dest) / (1024 * 1024)
                            print(f"    [{i}/{len(outputs)}] {fname}  ({size_mb:.1f} MB)")
                        except Exception as e:
                            print(f"    [{i}/{len(outputs)}] ERROR: {e}")
                            failed += 1

                    if failed == 0:
                        # Mark job as downloaded
                        try:
                            job_update(int(job_id), token, status="downloaded",
                                       status_description=(
                                           f"Downloaded {len(outputs)} frame(s) "
                                           f"to {local_dir} via Companion"))
                            print(f"  ✓ Job {job_num} marked 'downloaded'")
                        except Exception as e:
                            print(f"  ! Could not mark job {job_num} downloaded: {e}")
                        already_downloaded.add(job_id)
                        retry_state.pop(job_id, None)
                    else:
                        delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS) - 1)]
                        retry_state[job_id] = {
                            "attempts":   attempts + 1,
                            "next_retry": time.time() + delay,
                        }
                        print(f"  ! {failed} frame(s) failed — retrying in {delay}s "
                              f"(attempt {attempts + 1}/{MAX_RETRIES})")
            else:
                counts = {s: sum(1 for j in all_jobs if j.get("status") == s)
                          for s in ("running", "pending", "success", "downloaded")}
                summary = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
                print(f"  Idle — {summary or 'no jobs'}. Next check in {COMPANION_INTERVAL}s…",
                      end="\r")

        except urllib.error.HTTPError as e:
            print(f"\n  API error: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            print(f"\n  Network error: {e.reason}")
        except Exception as e:
            print(f"\n  Unexpected error: {e}")

        time.sleep(COMPANION_INTERVAL)


def _ext_from_url(url: str) -> str:
    """Extract the file extension from a URL, defaulting to .png."""
    path = url.split("?")[0]           # strip query string
    ext  = os.path.splitext(path)[1].lower()
    return ext if ext in (".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff") else ".png"


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    companion_mode = "--companion" in sys.argv or "-c" in sys.argv

    if companion_mode:
        token, _email = load_token()
        if not token:
            print("\nERROR: No auth token found.")
            print("  Set RF_TOKEN or sign in via the Blender addon first.")
            sys.exit(1)
        try:
            companion_loop(token)
        except KeyboardInterrupt:
            print("\n\n  Companion stopped.")
        return

    print("=" * 60)
    print("  Renderfarm Local Worker  v2.2  (supports v6 + v7 jobs)")
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
