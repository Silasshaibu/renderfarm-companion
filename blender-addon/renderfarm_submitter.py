bl_info = {
    "name":        "Renderfarm Render Submitter",
    "author":      "Renderfarm",
    "version":     (2, 0, 3),
    "blender":     (3, 0, 0),
    "location":    "Properties > Render > Renderfarm Render Submitter",
    "description": "Submit render jobs to Renderfarm directly from Blender",
    "category":    "Render",
}

import bpy
import json
import urllib.request
import urllib.error
import urllib.parse
import http.client
import http.server
import ssl
import hashlib
import threading
import webbrowser
import os
import sys
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import StringProperty, CollectionProperty
from bpy.utils import register_class, unregister_class

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
API_BASE      = "https://renderfarm-web.vercel.app/api"
WEB_BASE      = "https://renderfarm-web.vercel.app"
CALLBACK_PORT = 8989
_TOKEN_FILE   = os.path.join(os.path.dirname(__file__), ".rf_token")

# ──────────────────────────────────────────────────────────────────────────────
# Token persistence
# ──────────────────────────────────────────────────────────────────────────────
def _save_token(token, email):
    with open(_TOKEN_FILE, "w") as f:
        json.dump({"token": token, "email": email}, f)

def _load_token():
    if not os.path.exists(_TOKEN_FILE):
        return None, None
    try:
        with open(_TOKEN_FILE) as f:
            d = json.load(f)
            return d.get("token"), d.get("email")
    except Exception:
        return None, None

def _clear_token():
    if os.path.exists(_TOKEN_FILE):
        os.remove(_TOKEN_FILE)

# ──────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────────────────────────────────────
def _get(path, token):
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def _post(path, payload, token=None):
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def _patch(path, payload, token):
    data = json.dumps(payload).encode()
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, headers=headers, method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# ──────────────────────────────────────────────────────────────────────────────
# Browser-auth local callback server
# ──────────────────────────────────────────────────────────────────────────────
_auth_server        = None
_auth_server_thread = None

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Receives the token redirect from the Renderfarm web page."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        token = params.get("token", [None])[0]
        email = params.get("email", [None])[0]

        if parsed.path == "/callback" and token and email:
            html = b"""<!DOCTYPE html><html><head>
            <meta charset="utf-8">
            <title>Renderfarm - Connected</title>
            <style>
              body{margin:0;min-height:100vh;display:flex;align-items:center;
                   justify-content:center;background:#0d0d1a;
                   font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#fff}
              .card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:16px;
                    padding:48px;text-align:center;max-width:380px}
              .icon{font-size:56px;color:#22d3ee;display:block;margin-bottom:16px}
              h1{margin:0 0 8px;font-size:22px}
              p{color:#8888aa;margin:0;font-size:14px}
            </style></head><body>
            <div class="card">
              <span class="icon">&#9889;</span>
              <h1>Blender Connected!</h1>
              <p>You can close this tab and return to Blender.</p>
            </div></body></html>"""

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

            def _apply():
                _on_auth_success(token, email)
                _stop_auth_server()

            bpy.app.timers.register(_apply, first_interval=0.1)

        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress terminal spam


def _start_auth_server():
    global _auth_server, _auth_server_thread
    if _auth_server:
        return
    try:
        _auth_server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
        _auth_server_thread = threading.Thread(target=_auth_server.serve_forever, daemon=True)
        _auth_server_thread.start()
    except OSError:
        pass


def _stop_auth_server():
    global _auth_server, _auth_server_thread
    if _auth_server:
        threading.Thread(target=_auth_server.shutdown, daemon=True).start()
        _auth_server        = None
        _auth_server_thread = None


def _on_auth_success(token, email):
    """Called on the main thread after the browser callback delivers the token."""
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        prefs.access_token = token
        prefs.user_email   = email
    except Exception:
        pass

    _save_token(token, email)

    try:
        scene    = bpy.context.scene
        projects = _get("/projects", token)
        _populate_project_menu(projects)
        _populate_machine_menu(scene.rf_instance_type)
        _populate_camera_menu(bpy.context)
        _set_resolution(bpy.context)
        scene.rf_job_title   = _get_job_title()
        scene.rf_frame_range = _get_scene_frame_range()
        if not scene.rf_output_folder:
            scene.rf_output_folder = _get_output_folder()
        _populate_frame_info()
        scene.rf_status_msg  = f"Connected as {email}"
        scene.rf_status_type = "OK"
        _refresh_ui()
        _refresh_properties()
    except Exception as e:
        try:
            bpy.context.scene.rf_status_msg  = f"Connected but sync failed: {e}"
            bpy.context.scene.rf_status_type = "ERROR"
        except Exception:
            pass

# ──────────────────────────────────────────────────────────────────────────────
# UI helpers
# ──────────────────────────────────────────────────────────────────────────────
def _refresh_ui():
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)

def _refresh_properties():
    for area in bpy.context.screen.areas:
        if area.type == "PROPERTIES":
            area.tag_redraw()

def _get_scene_frame_range():
    s = bpy.context.scene
    return f"{s.frame_start}-{s.frame_end}"

def _get_token():
    try:
        return bpy.context.preferences.addons[__name__].preferences.access_token or None
    except Exception:
        return None

def _get_email():
    try:
        return bpy.context.preferences.addons[__name__].preferences.user_email or ""
    except Exception:
        return ""

# ──────────────────────────────────────────────────────────────────────────────
# Software / instance data
# ──────────────────────────────────────────────────────────────────────────────
BLENDER_VERSIONS = [
    ("blender-4-2-lts", "Blender 4.2 LTS", ""),
    ("blender-4-1",     "Blender 4.1",     ""),
    ("blender-4-0",     "Blender 4.0",     ""),
    ("blender-3-6-lts", "Blender 3.6 LTS", ""),
    ("blender-3-5",     "Blender 3.5",     ""),
    ("blender-3-4",     "Blender 3.4",     ""),
    ("blender-3-3-lts", "Blender 3.3 LTS", ""),
]

INSTANCE_TYPES = {
    "GPU": [
        ("a100-80gb-1", "A100 80GB · 12 vCPU · 85 GB",  ""),
        ("a100-40gb-1", "A100 40GB · 12 vCPU · 85 GB",  ""),
        ("l4-1",        "L4 24GB · 8 vCPU · 32 GB",     ""),
        ("t4-1",        "T4 16GB · 4 vCPU · 15 GB",     ""),
        ("v100-1",      "V100 16GB · 8 vCPU · 52 GB",   ""),
    ],
    "CPU": [
        ("n1-highcpu-64", "n1-highcpu-64 · 64 vCPU · 57.6 GB", ""),
        ("n1-highcpu-32", "n1-highcpu-32 · 32 vCPU · 28.8 GB", ""),
        ("n1-highcpu-16", "n1-highcpu-16 · 16 vCPU · 14.4 GB", ""),
        ("n1-highcpu-8",  "n1-highcpu-8 · 8 vCPU · 7.2 GB",   ""),
    ],
}

# ──────────────────────────────────────────────────────────────────────────────
# Populate helpers
# ──────────────────────────────────────────────────────────────────────────────
def _populate_project_menu(projects):
    items = [(p["id"], p["name"], "") for p in projects if p.get("isActive", True)]
    if not items:
        items = [("none", "No Projects", "")]
    bpy.types.Scene.rf_project = bpy.props.EnumProperty(
        name="Project",
        description="Renderfarm project to run the job in.",
        items=items,
    )

def _populate_machine_menu(instance_type="GPU"):
    items = INSTANCE_TYPES.get(instance_type, INSTANCE_TYPES["GPU"])
    bpy.types.Scene.rf_machine_type = bpy.props.EnumProperty(
        name="Machine Type",
        description="Select the machine for your render.",
        items=items,
    )

def _update_instance_type(self, context):
    _populate_machine_menu(context.scene.rf_instance_type)
    _refresh_ui()

def _populate_camera_menu(context):
    scene = context.scene
    items = [(c.name, c.name, "") for c in scene.objects if c.type == "CAMERA"]
    if not items:
        items = [("none", "No Cameras", "")]
    def _update_cam(self, context):
        obj = context.scene.objects.get(self.rf_camera_override)
        if obj:
            context.scene.camera = obj
    bpy.types.Scene.rf_camera_override = bpy.props.EnumProperty(
        name="Camera", items=items, update=_update_cam)
    if scene.camera:
        try:
            scene.rf_camera_override = scene.camera.name
        except Exception:
            pass

def _populate_frame_info():
    scene = bpy.context.scene
    try:
        frame_range = scene.rf_frame_range if scene.rf_use_custom_range else _get_scene_frame_range()
        chunk  = max(1, scene.rf_chunk_size)
        parts  = frame_range.replace(" ", "").split("-")
        start  = int(parts[0])
        end    = int(parts[-1]) if len(parts) > 1 else start
        count  = max(0, end - start + 1)
        tasks  = max(1, (count + chunk - 1) // chunk)

        # Multiply by tile count when tiles are enabled
        tile_count = 1
        if scene.rf_use_tiles and scene.rf_tiles.strip():
            tp = scene.rf_tiles.replace(" ", "").split("-")
            try:
                ts = int(tp[0]); te = int(tp[-1]) if len(tp) > 1 else ts
                tile_count = max(1, te - ts + 1)
            except ValueError:
                pass
        tasks *= tile_count

        scene.rf_frame_spec  = frame_range
        scene.rf_frame_count = str(count)
        scene.rf_task_count  = str(tasks)
    except Exception:
        pass

def _on_chunk_updated(self, context):  _populate_frame_info()
def _on_range_updated(self, context):  _populate_frame_info()
def _on_tiles_updated(self, context):  _populate_frame_info()

def _set_resolution(context):
    scene = context.scene
    render = scene.render
    try:
        if scene.rf_resolution_x   <= 0: scene.rf_resolution_x   = render.resolution_x
        if scene.rf_resolution_y   <= 0: scene.rf_resolution_y   = render.resolution_y
        if scene.rf_resolution_pct <= 0: scene.rf_resolution_pct = render.resolution_percentage
        if scene.rf_samples <= 0 and scene.render.engine == "CYCLES":
            scene.rf_samples = scene.cycles.samples
    except Exception:
        pass

def _get_output_folder():
    try:
        fp     = bpy.context.blend_data.filepath
        folder = os.path.dirname(fp) if fp else os.path.expanduser("~")
        out    = os.path.join(folder, "render")
        os.makedirs(out, exist_ok=True)
        return out
    except Exception:
        return ""

def _get_job_title():
    try:
        fp   = bpy.context.blend_data.filepath
        name = os.path.splitext(os.path.basename(fp))[0] if fp else "Untitled"
        v    = bpy.app.version
        return f"Blender {v[0]}.{v[1]}.{v[2]} Linux Render {name}"
    except Exception:
        return "Blender Linux Render"

# ──────────────────────────────────────────────────────────────────────────────
# Payload builder
# ──────────────────────────────────────────────────────────────────────────────
def _build_payload(context):
    scene       = context.scene
    frame_range = scene.rf_frame_range if scene.rf_use_custom_range else _get_scene_frame_range()
    env         = {v.variable_name: v.variable_value
                   for v in scene.rf_env_vars.variables if v.variable_name}
    return {
        "provider":            scene.rf_provider,
        "title":               scene.rf_job_title,
        "project_id":          scene.get("rf_project", ""),
        "instance_type":       scene.rf_instance_type,
        "machine_type":        scene.get("rf_machine_type", ""),
        "preemptible":         scene.rf_preemptible,
        "preemptible_retries": scene.rf_preemptible_retries,
        "software":            scene.rf_blender_version,
        "render_software":     scene.rf_render_software,
        "frames":              frame_range,
        "chunk_size":          scene.rf_chunk_size,
        "use_scout_frames":    scene.rf_use_scout_frames,
        "scout_frames":        scene.rf_scout_frames,
        "use_tiles":           scene.rf_use_tiles,
        "tiles":               scene.rf_tiles if scene.rf_use_tiles else "",
        "resolution_x":        scene.rf_resolution_x,
        "resolution_y":        scene.rf_resolution_y,
        "resolution_pct":      scene.rf_resolution_pct,
        "camera":              scene.get("rf_camera_override", ""),
        "samples":             scene.rf_samples,
        "output_folder":       scene.rf_output_folder,
        "disable_audio":       scene.rf_disable_audio,
        "extra_env":           env,
        # blender_file intentionally omitted — the blob URL is set via manifest after upload
    }

# ──────────────────────────────────────────────────────────────────────────────
# Preferences  (no email/password — auth happens in the browser)
# ──────────────────────────────────────────────────────────────────────────────
class RFPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    access_token: StringProperty(default="")
    user_email:   StringProperty(default="")

    def draw(self, context):
        layout = self.layout
        if self.user_email:
            row = layout.row()
            row.label(text=f"Signed in as: {self.user_email}", icon="CHECKMARK")
            row.operator("rf.disconnect", text="Disconnect", icon="PANEL_CLOSE")
        else:
            layout.label(text="Click Connect in the Render Properties panel to sign in.", icon="INFO")

# ──────────────────────────────────────────────────────────────────────────────
# Property groups
# ──────────────────────────────────────────────────────────────────────────────
class RFExtraFileAsset(PropertyGroup):
    file_path: StringProperty(name="File Path", subtype="FILE_PATH")

class RFExtraDirAsset(PropertyGroup):
    dir_path: StringProperty(name="Dir Path", subtype="DIR_PATH")

class RFEnvVar(PropertyGroup):
    variable_name:  StringProperty(name="Variable")
    variable_value: StringProperty(name="Value")

class RFEnvVarList(PropertyGroup):
    variables: CollectionProperty(type=RFEnvVar)

class RFAddonProp(PropertyGroup):
    enabled:       bpy.props.BoolProperty(name="Enabled", default=False)
    name:          StringProperty(name="Name")
    version_items: StringProperty()

    def _get_versions(self, context):
        versions = self.version_items.split(",") if self.version_items else []
        return [(v, v, "") for v in versions] or [("none", "None", "")]

    menu_option: bpy.props.EnumProperty(name="Version", items=_get_versions)

# ──────────────────────────────────────────────────────────────────────────────
# Submission state machine
# ──────────────────────────────────────────────────────────────────────────────
_sub = {
    "state": "IDLE",   # IDLE | PREFLIGHT | SUBMITTING | COMPLETE | ERROR
    # PREFLIGHT
    "deps":    [],     # list of dep dicts from _scan_deps()
    "missing": [],     # deps where exists=False
    # SUBMITTING — 4 steps
    "step": 0,
    "step_states": {1: "pending", 2: "pending", 3: "pending", 4: "pending"},
    "step_progress": 0.0,
    "file_idx": 0,
    "file_total": 0,
    "file_name": "",
    "file_progress": 0.0,
    "log": [],          # last 5 lines
    # COMPLETE
    "job_number": "",
    "job_id":     "",
    "uploaded":   0,
    "skipped":    0,
    # ERROR
    "error":       "",
    "failed_step": 0,
    # control
    "cancel": False,
}

_sub_lock = threading.Lock()


def _log(msg):
    """Append a message to the in-dialog log (max 5 lines)."""
    with _sub_lock:
        _sub["log"].append(msg)
        if len(_sub["log"]) > 5:
            _sub["log"].pop(0)


def _redraw():
    """Force Blender to repaint all regions — called from a timer on the main thread."""
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Dependency scanner  (fast — reads bpy.data, no file I/O)
# ──────────────────────────────────────────────────────────────────────────────
def _scan_deps():
    """
    Scan bpy.data for all external file dependencies.
    Returns a list of dep dicts:
      { type, name, abs_path, rel_path, exists, size }
    Also includes the .blend file itself as type="blend".
    """
    deps = []

    def _add(dep_type, name, filepath):
        if not filepath:
            return
        abs_path = bpy.path.abspath(filepath)
        rel_path = filepath
        exists   = os.path.isfile(abs_path)
        size     = os.path.getsize(abs_path) if exists else 0
        deps.append({
            "type":     dep_type,
            "name":     name,
            "abs_path": abs_path,
            "rel_path": rel_path,
            "exists":   exists,
            "size":     size,
            "sha256":   None,   # filled in during SUBMITTING/Step 2
        })

    # Images
    for img in bpy.data.images:
        if img.packed_file:
            continue   # packed — no external file
        if img.filepath:
            _add("image", img.name, img.filepath)

    # Linked libraries
    for lib in bpy.data.libraries:
        if lib.filepath:
            _add("library", lib.name, lib.filepath)

    # Fonts
    for font in bpy.data.fonts:
        if font.filepath in ("", "<builtin>"):
            continue
        _add("font", font.name, font.filepath)

    # Sounds
    for snd in bpy.data.sounds:
        if snd.filepath:
            _add("sound", snd.name, snd.filepath)

    # Cache files (Alembic, VDB, etc.)
    for cf in bpy.data.cache_files:
        if cf.filepath:
            _add("cache", cf.name, cf.filepath)

    # The .blend itself
    blend_path = bpy.context.blend_data.filepath
    if blend_path:
        abs_blend = os.path.abspath(blend_path)
        exists    = os.path.isfile(abs_blend)
        size      = os.path.getsize(abs_blend) if exists else 0
        deps.append({
            "type":     "blend",
            "name":     os.path.basename(abs_blend),
            "abs_path": abs_blend,
            "rel_path": blend_path,
            "exists":   exists,
            "size":     size,
            "sha256":   None,
        })

    return deps


# ──────────────────────────────────────────────────────────────────────────────
# SHA-256 helper
# ──────────────────────────────────────────────────────────────────────────────
def _sha256_file(path):
    """Return hex SHA-256 digest of a file, reading in 64 KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# Asset uploader
# ──────────────────────────────────────────────────────────────────────────────
def _upload_asset(abs_path, sha256, token, filename=None, progress_cb=None):
    """
    Upload a single asset file to Vercel Blob via the /api/assets endpoint.
    Returns the blob URL for this asset.

    1. POST /assets?action=token  — check if already uploaded; get upload URL
    2. If exists → return cached URL immediately (deduplicated)
    3. Otherwise → stream PUT directly to Vercel Blob in 64 KB chunks
    4. POST /assets?action=confirm — record the asset in the DB
    """
    fname = filename or os.path.basename(abs_path)
    size  = os.path.getsize(abs_path)

    # Step 1: request a client token (or confirm dedup)
    resp = _post(f"/assets?action=token",
                 {"sha256": sha256, "filename": fname, "size_bytes": size},
                 token)

    if resp.get("exists"):
        # Already on the server — skip upload
        return resp["url"]

    # Step 3: chunked PUT directly to Vercel Blob
    client_token = resp["clientToken"]
    upload_url   = resp["uploadUrl"]

    with open(abs_path, "rb") as fh:
        file_data = fh.read()
    total = len(file_data)

    parsed  = urllib.parse.urlparse(upload_url)
    ssl_ctx = ssl.create_default_context()
    conn    = http.client.HTTPSConnection(parsed.netloc, timeout=600, context=ssl_ctx)
    try:
        path_qs = parsed.path + (("?" + parsed.query) if parsed.query else "")
        conn.putrequest("PUT", path_qs)
        conn.putheader("Authorization",  f"Bearer {client_token}")
        conn.putheader("Content-Type",   "application/octet-stream")
        conn.putheader("Content-Length", str(total))
        conn.putheader("x-api-version",  "7")
        conn.endheaders()

        sent = 0
        while sent < total:
            chunk = file_data[sent:sent + 65536]
            conn.send(chunk)
            sent += len(chunk)
            if progress_cb and total:
                progress_cb(sent / total)

        resp_obj   = conn.getresponse()
        result     = json.loads(resp_obj.read())
        blob_url   = result.get("url", upload_url)
    finally:
        conn.close()

    # Step 4: confirm in DB
    _post("/assets?action=confirm",
          {"sha256": sha256, "url": blob_url, "filename": fname, "size_bytes": size},
          token)

    return blob_url


# ──────────────────────────────────────────────────────────────────────────────
# GCS direct uploader (used by GCP submission path)
# ──────────────────────────────────────────────────────────────────────────────
def _upload_blend_to_gcs(blend_path, token, progress_cb=None):
    """
    Upload the .blend file directly to GCS via a signed PUT URL.

    1. POST /gcp/upload-url  — API returns { uploadUrl, gcsPath }
    2. Stream PUT to GCS     — no temp buffer; reads in 64 KB chunks
    Returns gcs_path (the GCS object path within the bucket).
    """
    import time, random, string
    filename  = os.path.basename(blend_path)
    file_size = os.path.getsize(blend_path)
    temp_id   = f"{int(time.time()):x}" + "".join(
        random.choices(string.ascii_lowercase + string.digits, k=6)
    )

    resp       = _post("/gcp/upload-url", {"jobId": temp_id, "filename": filename}, token)
    upload_url = resp["uploadUrl"]
    gcs_path   = resp["gcsPath"]

    parsed  = urllib.parse.urlparse(upload_url)
    ssl_ctx = ssl.create_default_context()
    conn    = http.client.HTTPSConnection(parsed.netloc, timeout=600, context=ssl_ctx)
    try:
        path_qs = parsed.path + (("?" + parsed.query) if parsed.query else "")
        conn.putrequest("PUT", path_qs)
        conn.putheader("Content-Type",   "application/octet-stream")
        conn.putheader("Content-Length", str(file_size))
        conn.endheaders()

        sent = 0
        with open(blend_path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                conn.send(chunk)
                sent += len(chunk)
                if progress_cb and file_size:
                    progress_cb(sent / file_size)

        resp_obj = conn.getresponse()
        resp_obj.read()   # drain body
        if not (200 <= resp_obj.status < 300):
            raise RuntimeError(f"GCS upload failed: HTTP {resp_obj.status}")
    finally:
        conn.close()

    return gcs_path


# ──────────────────────────────────────────────────────────────────────────────
# Background submission thread
# ──────────────────────────────────────────────────────────────────────────────
def _run_submission(scene_props, token, blend_path, payload):
    """
    Runs entirely in a background daemon thread.
    Communicates with the main thread only through the _sub dict + timers.

    scene_props: plain dict snapshot of scene properties (no bpy access in thread)
    """

    def _set_step(n):
        with _sub_lock:
            _sub["step"] = n
            for k in _sub["step_states"]:
                if k < n:
                    _sub["step_states"][k] = "done"
                elif k == n:
                    _sub["step_states"][k] = "active"
                else:
                    _sub["step_states"][k] = "pending"

    try:
        # ── Step 1: Analyze ──────────────────────────────────────────────────
        _set_step(1)
        _log("Analyzing scene dependencies…")
        bpy.app.timers.register(_redraw, first_interval=0.05)

        deps       = _sub["deps"]   # already populated from PREFLIGHT
        uploadable = [d for d in deps if d["exists"]]

        _log(f"Found {len(uploadable)} uploadable assets")

        with _sub_lock:
            _sub["step_states"][1] = "done"

        # ── Step 2: Build manifest + preflight ──────────────────────────────
        _set_step(2)
        _log("Computing SHA-256 hashes…")
        bpy.app.timers.register(_redraw, first_interval=0.05)

        total_hash = len(uploadable)
        for i, dep in enumerate(uploadable):
            if _sub.get("cancel"):
                raise InterruptedError("Cancelled")
            print(f"[RF] Hashing dep {i}: type={dep.get('type')} name={dep.get('name')} path={dep.get('abs_path')} exists={dep.get('exists')} size={dep.get('size')}")
            sha = _sha256_file(dep["abs_path"])
            print(f"[RF] Hash done: {sha[:16]}...")
            dep["sha256"] = sha
            with _sub_lock:
                _sub["step_progress"] = (i + 1) / max(1, total_hash)
            _log(f"Hashed: {dep['name']}")
            bpy.app.timers.register(_redraw, first_interval=0.05)

        _log("Running preflight check with server…")
        preflight_resp = _post("/jobs/preflight",
                               {"assets": [{"sha256": d["sha256"]} for d in uploadable]},
                               token)
        missing_set = set(preflight_resp.get("missing", []))
        to_upload   = [d for d in uploadable if d["sha256"] in missing_set]

        _log(f"Server needs {len(to_upload)} of {len(uploadable)} assets")

        # Build manifest
        v = bpy.app.version
        frame_range = scene_props.get("frames", "1-1")
        parts = frame_range.replace(" ", "").split("-")
        manifest = {
            "scene":           os.path.basename(blend_path),
            "blender_version": f"{v[0]}.{v[1]}.{v[2]}",
            "renderer":        scene_props.get("render_software", "Cycles"),
            "instance_type":   scene_props.get("instance_type", "CPU"),  # "GPU" or "CPU"
            "machine_type":    scene_props.get("machine_type", ""),
            "frame_start":     int(parts[0]),
            "frame_end":       int(parts[-1]) if len(parts) > 1 else int(parts[0]),
            "chunk_size":      scene_props.get("chunk_size", 1),
            "assets": [
                {
                    "path":       d["rel_path"],
                    "sha256":     d["sha256"],
                    "size_bytes": d["size"],
                    "type":       d["type"],
                }
                for d in uploadable
            ],
        }

        with _sub_lock:
            _sub["step_states"][2] = "done"
            _sub["step_progress"]  = 0.0

        # ── Step 3: Submit job record ────────────────────────────────────────
        _set_step(3)
        _log("Creating job record…")
        bpy.app.timers.register(_redraw, first_interval=0.05)

        submit_payload = dict(payload)
        submit_payload["status"]       = "uploading"
        submit_payload["manifest"]     = manifest
        submit_payload["assets_total"] = len(uploadable)

        result = _post("/jobs", submit_payload, token)
        job_number = result.get("jobNumber", "?")
        job_id     = result.get("id", "")

        with _sub_lock:
            _sub["job_number"]    = str(job_number)
            _sub["job_id"]        = str(job_id)
            _sub["step_states"][3] = "done"

        _log(f"Job {job_number} created (uploading)")
        bpy.app.timers.register(_redraw, first_interval=0.05)

        # ── Step 4: Upload assets ─────────────────────────────────────────────
        _set_step(4)
        with _sub_lock:
            _sub["file_total"]    = len(to_upload)
            _sub["file_idx"]      = 0
            _sub["step_progress"] = 0.0

        uploaded = 0
        skipped  = 0

        # Track blob_url per sha256 so we can patch the manifest
        sha256_to_url = {}

        # Also pre-populate with already-existing assets (server has them)
        for dep in uploadable:
            if dep["sha256"] not in missing_set:
                # Already on server — fetch its URL via token endpoint
                try:
                    resp_exists = _post("/assets?action=token",
                                        {"sha256": dep["sha256"],
                                         "filename": dep["name"],
                                         "size_bytes": dep["size"]},
                                        token)
                    if resp_exists.get("exists") and resp_exists.get("url"):
                        sha256_to_url[dep["sha256"]] = resp_exists["url"]
                        print(f"[RF] Asset already on server: {dep['name']} → {resp_exists['url'][:60]}")
                    else:
                        # Server said not missing but can't get URL — force re-upload
                        print(f"[RF] Asset dedup miss for {dep['name']}, adding to upload queue")
                        missing_set.add(dep["sha256"])
                        to_upload.append(dep)
                except Exception as ex:
                    print(f"[RF] WARNING: could not fetch cached URL for {dep['name']}: {ex} — forcing re-upload")
                    missing_set.add(dep["sha256"])
                    to_upload.append(dep)

        for i, dep in enumerate(to_upload):
            if _sub.get("cancel"):
                raise InterruptedError("Cancelled")

            fname = dep["name"]
            with _sub_lock:
                _sub["file_idx"]      = i + 1
                _sub["file_name"]     = fname
                _sub["file_progress"] = 0.0

            _log(f"Uploading {fname}…")
            bpy.app.timers.register(_redraw, first_interval=0.05)

            def _prog_cb(frac, dep=dep):
                with _sub_lock:
                    _sub["file_progress"] = frac
                bpy.app.timers.register(_redraw, first_interval=0.05)

            url = _upload_asset(
                dep["abs_path"],
                dep["sha256"],
                token,
                filename=fname,
                progress_cb=_prog_cb,
            )

            if url:
                uploaded += 1
                sha256_to_url[dep["sha256"]] = url
            else:
                skipped += 1

            with _sub_lock:
                _sub["step_progress"] = (i + 1) / max(1, len(to_upload))
                _sub["file_progress"] = 1.0

        # Assets that were already on the server count as skipped
        skipped += len(uploadable) - len(to_upload)

        # Build resolved manifest — add blob_url to each asset entry so the
        # render worker can download assets without extra API calls
        resolved_assets = []
        for a in manifest["assets"]:
            entry = dict(a)
            entry["blob_url"] = sha256_to_url.get(a["sha256"], "")
            resolved_assets.append(entry)
        resolved_manifest = dict(manifest)
        resolved_manifest["assets"] = resolved_assets

        # Finalise job: set status=pending (ready for render worker), resolved manifest, assets_uploaded count
        _patch(f"/jobs?id={job_id}",
               {
                   "status":          "pending",
                   "assets_uploaded": uploaded + skipped,
                   "manifest":        resolved_manifest,
               },
               token)

        _log(f"Done — {uploaded} uploaded, {skipped} already cached")

        with _sub_lock:
            _sub["state"]          = "COMPLETE"
            _sub["uploaded"]       = uploaded
            _sub["skipped"]        = skipped
            _sub["step_states"][4] = "done"

        bpy.app.timers.register(_redraw, first_interval=0.05)

    except InterruptedError:
        with _sub_lock:
            _sub["state"]       = "ERROR"
            _sub["error"]       = "Cancelled"
            _sub["failed_step"] = _sub["step"]
        bpy.app.timers.register(_redraw, first_interval=0.05)

    except Exception as e:
        with _sub_lock:
            _sub["state"]       = "ERROR"
            _sub["error"]       = str(e)
            _sub["failed_step"] = _sub["step"]
        bpy.app.timers.register(_redraw, first_interval=0.05)


# ──────────────────────────────────────────────────────────────────────────────
# GCP submission pipeline (background thread)
# ──────────────────────────────────────────────────────────────────────────────
def _run_gcp_submission(scene_props, token, blend_path, payload):
    """
    GCP-only pipeline — runs in a background daemon thread.

    Step 1: Upload .blend directly to GCS (with live progress)
    Step 2: POST /api/jobs → API auto-dispatches render VMs
    Steps 3 + 4: instantly marked done (N/A for GCP)
    """

    def _set_step(n):
        with _sub_lock:
            _sub["step"] = n
            for k in _sub["step_states"]:
                if k < n:
                    _sub["step_states"][k] = "done"
                elif k == n:
                    _sub["step_states"][k] = "active"
                else:
                    _sub["step_states"][k] = "pending"

    try:
        filename = os.path.basename(blend_path)

        # ── Step 1: Upload .blend to GCS ─────────────────────────────────────
        _set_step(1)
        _log(f"Uploading {filename} to cloud storage…")
        with _sub_lock:
            _sub["file_total"]    = 1
            _sub["file_idx"]      = 1
            _sub["file_name"]     = filename
            _sub["file_progress"] = 0.0
        bpy.app.timers.register(_redraw, first_interval=0.05)

        def _gcs_prog(frac):
            with _sub_lock:
                _sub["file_progress"] = frac
                _sub["step_progress"] = frac
            bpy.app.timers.register(_redraw, first_interval=0.05)

        if _sub.get("cancel"):
            raise InterruptedError("Cancelled")

        gcs_path = _upload_blend_to_gcs(blend_path, token, progress_cb=_gcs_prog)
        _log(f"Uploaded → {gcs_path}")

        with _sub_lock:
            _sub["step_states"][1] = "done"
            _sub["step_progress"]  = 1.0
            _sub["file_progress"]  = 1.0
        bpy.app.timers.register(_redraw, first_interval=0.05)

        if _sub.get("cancel"):
            raise InterruptedError("Cancelled")

        # ── Step 2: Create job (VMs auto-dispatch inside the API) ─────────────
        _set_step(2)
        _log("Creating job and dispatching render VMs…")
        bpy.app.timers.register(_redraw, first_interval=0.05)

        submit_payload = dict(payload)
        submit_payload["provider"]       = "gcp"
        submit_payload["gcs_scene_path"] = gcs_path
        # Strip renderfarm-only keys that confuse the API for GCP jobs
        for k in ("status", "manifest", "assets_total"):
            submit_payload.pop(k, None)

        result     = _post("/jobs", submit_payload, token)
        job_number = result.get("jobNumber", "?")
        job_id     = result.get("id", "")

        with _sub_lock:
            _sub["job_number"]     = str(job_number)
            _sub["job_id"]         = str(job_id)
            _sub["step_states"][2] = "done"
            _sub["step_states"][3] = "done"   # N/A for GCP
            _sub["step_states"][4] = "done"   # N/A for GCP
            _sub["step_progress"]  = 1.0
        _log(f"Job {job_number} dispatched — VMs starting…")
        bpy.app.timers.register(_redraw, first_interval=0.05)

        with _sub_lock:
            _sub["state"]    = "COMPLETE"
            _sub["uploaded"] = 1
            _sub["skipped"]  = 0
        bpy.app.timers.register(_redraw, first_interval=0.05)

    except InterruptedError:
        with _sub_lock:
            _sub["state"]       = "ERROR"
            _sub["error"]       = "Cancelled"
            _sub["failed_step"] = _sub["step"]
        bpy.app.timers.register(_redraw, first_interval=0.05)

    except Exception as e:
        with _sub_lock:
            _sub["state"]       = "ERROR"
            _sub["error"]       = str(e)
            _sub["failed_step"] = _sub["step"]
        bpy.app.timers.register(_redraw, first_interval=0.05)


# ──────────────────────────────────────────────────────────────────────────────
# Dialog draw helpers
# ──────────────────────────────────────────────────────────────────────────────
_TYPE_ICON = {
    "image":   "FILE_IMAGE",
    "library": "LIBRARY_DATA_DIRECT",
    "font":    "FONT_DATA",
    "sound":   "SPEAKER",
    "cache":   "FILE_CACHE",
    "blend":   "FILE_BLEND",
}

_STEP_LABELS = {
    1: "Analyze dependencies",
    2: "Build manifest & preflight",
    3: "Submit job record",
    4: "Upload assets",
}

# GCP uses a 2-step pipeline — steps 3+4 are N/A and auto-completed
_STEP_LABELS_GCP = {
    1: "Upload .blend to cloud storage",
    2: "Create job & dispatch VMs",
    3: "—",
    4: "—",
}


def _fmt_size(size_bytes):
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024**3):.1f} GB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024**2):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _draw_gcp_preflight(layout):
    """Simplified pre-submit screen shown for GCP jobs."""
    blend_path = bpy.context.blend_data.filepath
    filename   = os.path.basename(blend_path) if blend_path else "untitled.blend"
    try:
        size_str = _fmt_size(os.path.getsize(blend_path)) if blend_path else "?"
    except OSError:
        size_str = "?"

    row = layout.row()
    row.scale_y = 1.2
    row.label(text="GCP Cloud Render", icon="WORLD")
    layout.separator(factor=0.8)

    box = layout.box()
    col = box.column(align=True)
    col.scale_y = 1.1
    col.label(text=f"Scene:  {filename}  ({size_str})", icon="FILE_BLEND")
    col.label(text="The .blend will be uploaded to cloud storage,")
    col.label(text="then render VMs will be dispatched automatically.")

    layout.separator(factor=0.8)
    action = layout.row(align=True)
    action.scale_y = 1.6
    action.operator("rf.start_submission", text="Upload & Submit to GCP", icon="RENDER_STILL")

    layout.separator(factor=0.3)
    hint = layout.row()
    hint.scale_y = 0.8
    hint.label(text="Close button below cancels.", icon="INFO")


def _draw_preflight(layout):
    # GCP has its own simpler preflight
    if _sub.get("gcp_mode"):
        _draw_gcp_preflight(layout)
        return

    deps    = _sub["deps"]
    missing = [d for d in deps if not d["exists"]]
    present = [d for d in deps if d["exists"]]
    total_b = sum(d["size"] for d in present)

    # Header
    row = layout.row()
    row.scale_y = 1.2
    row.label(text="Analyze Scene", icon="VIEWZOOM")
    layout.separator(factor=0.8)

    # Summary box
    box = layout.box()
    col = box.column(align=True)
    col.scale_y = 1.1
    col.label(text=f"Found {len(deps)} assets   ({_fmt_size(total_b)} total)")
    if missing:
        row_m = col.row()
        row_m.alert = True
        row_m.label(text=f"Missing: {len(missing)} (shown in red)", icon="ERROR")
    else:
        col.label(text="All assets found on disk", icon="CHECKMARK")

    layout.separator(factor=0.5)

    # Asset list (max 8 shown at once)
    box2 = layout.box()
    col2 = box2.column(align=True)
    visible = deps[:8]
    for dep in visible:
        icon = _TYPE_ICON.get(dep["type"], "FILE_BLANK")
        row  = col2.row(align=True)
        row.alert = not dep["exists"]
        row.label(text="", icon=icon)
        row.label(text=dep["name"])
        size_row = row.row()
        size_row.alignment = "RIGHT"
        if dep["exists"]:
            size_row.label(text=_fmt_size(dep["size"]))
        else:
            size_row.label(text="NOT FOUND", icon="ERROR")

    if len(deps) > 8:
        col2.label(text=f"… and {len(deps) - 8} more")

    layout.separator(factor=0.8)

    # Action row
    action = layout.row(align=True)
    action.scale_y = 1.5
    if not missing:
        action.operator("rf.start_submission", text="Continue Submission", icon="PLAY")
    else:
        col_warn = layout.column()
        col_warn.alert = True
        col_warn.label(
            text="Resolve missing assets before submitting.",
            icon="ERROR",
        )
        layout.separator(factor=0.4)
        action.operator("rf.start_submission", text="Continue Anyway", icon="PLAY")

    layout.separator(factor=0.3)
    hint = layout.row()
    hint.scale_y = 0.8
    hint.label(text="Close button below cancels.", icon="INFO")


def _draw_submitting(layout):
    job_num = _sub.get("job_number") or "Job"

    # Header
    row = layout.row()
    row.scale_y = 1.2
    row.label(text=f"Submitting {job_num}…", icon="RENDER_ANIMATION")
    layout.separator(factor=0.8)

    # Step rows — GCP shows 2 meaningful steps; renderfarm shows 4
    gcp_mode    = _sub.get("gcp_mode", False)
    labels      = _STEP_LABELS_GCP if gcp_mode else _STEP_LABELS
    total_steps = 2 if gcp_mode else 4
    upload_step = 1 if gcp_mode else 4   # which step shows the file-progress box

    for n in range(1, total_steps + 1):
        state = _sub["step_states"][n]
        if state == "done":
            icon   = "CHECKMARK"
            status = "Done"
        elif state == "active":
            icon   = "RENDER_ANIMATION"
            status = "Working…"
        else:
            icon   = "BLANK1"
            status = "Waiting"

        row = layout.row(align=True)
        row.label(text="", icon=icon)
        row.label(text=f"Step {n}/{total_steps} — {labels[n]}")
        right = row.row()
        right.alignment = "RIGHT"
        right.label(text=status)

    # File upload detail row (step 1 for GCP, step 4 for renderfarm)
    if _sub["step_states"][upload_step] == "active":
        layout.separator(factor=0.6)
        detail = layout.box()
        file_idx   = _sub["file_idx"]
        file_total = _sub["file_total"]
        file_name  = _sub["file_name"] or ""
        detail.label(text=f"File {file_idx} of {file_total}: {file_name}")

        # Per-file progress bar
        fp = max(0.0, min(1.0, _sub["file_progress"]))
        if bpy.app.version >= (3, 3, 0) and hasattr(detail.row(), "progress"):
            detail.row().progress(factor=fp, text=f"{int(fp*100)} %")
        else:
            done  = int(fp * 10)
            detail.label(text="[" + "=" * done + " " * (10 - done) + f"] {int(fp*100)} %")

    # Overall step progress bar
    layout.separator(factor=0.6)
    sp = max(0.0, min(1.0, _sub["step_progress"]))
    if bpy.app.version >= (3, 3, 0) and hasattr(layout.row(), "progress"):
        layout.row().progress(factor=sp, text=f"Overall: {int(sp*100)} %")
    else:
        done  = int(sp * 10)
        layout.label(text="Overall: [" + "=" * done + " " * (10 - done) + f"] {int(sp*100)} %")

    # Log box
    layout.separator(factor=0.6)
    log_box = layout.box()
    log_col = log_box.column(align=True)
    log_col.scale_y = 0.85
    log_lines = _sub["log"][-4:]
    for line in log_lines:
        log_col.label(text=line)

    # Cancel button
    layout.separator(factor=0.5)
    layout.operator("rf.cancel_submission", text="Cancel", icon="CANCEL")


def _draw_complete(layout):
    # Header
    row = layout.row()
    row.scale_y = 1.2
    row.label(text="Job Submitted", icon="CHECKMARK")
    layout.separator(factor=0.8)

    box = layout.box()
    col = box.column(align=True)
    col.scale_y = 1.15
    col.label(text=f"Job Number:  {_sub['job_number']}", icon="RENDER_STILL")
    if _sub.get("gcp_mode"):
        col.label(text="1 .blend file uploaded to GCS · VMs dispatching…", icon="WORLD_DATA")
    else:
        col.label(text=f"Uploaded {_sub['uploaded']} assets  (skipped {_sub['skipped']} already on server)")

    layout.separator(factor=1.2)
    dash = layout.row()
    dash.scale_y = 1.8
    dash.operator("rf.open_dashboard", text="Open Dashboard", icon="URL")

    layout.separator(factor=0.5)
    hint = layout.row()
    hint.scale_y = 0.8
    hint.label(text="Close button below dismisses this window.", icon="INFO")


def _draw_error(layout):
    # Header
    row = layout.row()
    row.scale_y = 1.2
    row.alert = True
    row.label(text="Submission Failed", icon="ERROR")
    layout.separator(factor=0.8)

    box = layout.box()
    box.alert = True
    col = box.column(align=True)
    col.scale_y = 1.1
    err_text = _sub.get("error", "Unknown error")
    for line in err_text[:300].split("\n")[:6]:
        col.label(text=line or " ")
    if _sub.get("failed_step"):
        col.label(text=f"Failed at step {_sub['failed_step']}")

    layout.separator(factor=0.8)
    row2 = layout.row(align=True)
    row2.scale_y = 1.5
    row2.operator("rf.submit", text="Retry", icon="FILE_REFRESH")

    hint = layout.row()
    hint.scale_y = 0.8
    hint.label(text="Click Close below or Retry to try again.", icon="INFO")


# ──────────────────────────────────────────────────────────────────────────────
# Operators — Connection
# ──────────────────────────────────────────────────────────────────────────────
class RF_OT_Connect(Operator):
    bl_idname      = "rf.connect"
    bl_label       = "Connect"
    bl_description = "Open your browser to sign in to Renderfarm."

    def execute(self, context):
        scene = context.scene
        scene.rf_status_msg  = "Waiting for browser sign-in…"
        scene.rf_status_type = "INFO"
        _start_auth_server()
        url = f"{WEB_BASE}/login?port={CALLBACK_PORT}"
        webbrowser.open(url)
        self.report({"INFO"}, "Browser opened — sign in to connect Blender.")
        return {"FINISHED"}


class RF_OT_Disconnect(Operator):
    bl_idname      = "rf.disconnect"
    bl_label       = "Disconnect"
    bl_description = "Sign out and disconnect Blender from Renderfarm."

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        prefs.access_token = ""
        prefs.user_email   = ""
        _clear_token()
        _stop_auth_server()
        context.scene.rf_status_msg  = ""
        context.scene.rf_status_type = "INFO"
        return {"FINISHED"}


class RF_OT_PreviewScript(Operator):
    bl_idname      = "rf.preview_script"
    bl_label       = "Preview Script"
    bl_description = "Export the job payload as JSON and open it."

    def execute(self, context):
        payload   = _build_payload(context)
        json_data = json.dumps(payload, indent=4)
        try:
            fp   = bpy.context.blend_data.filepath
            name = os.path.splitext(os.path.basename(fp))[0] if fp else "renderfarm_job"
            out  = os.path.join(os.path.dirname(fp) if fp else os.path.expanduser("~"), f"{name}.json")
            with open(out, "w") as f:
                f.write(json_data)
            if sys.platform.startswith("win"):
                os.startfile(out)
            elif sys.platform == "darwin":
                import subprocess; subprocess.call(["open", out])
            else:
                import subprocess; subprocess.call(["xdg-open", out])
        except Exception as e:
            self.report({"WARNING"}, f"Could not open file: {e}")
        return {"FINISHED"}


# ──────────────────────────────────────────────────────────────────────────────
# Operators — Submission pipeline
# ──────────────────────────────────────────────────────────────────────────────

class RF_OT_Submit(Operator):
    bl_idname      = "rf.submit"
    bl_label       = "Submit"
    bl_description = "Scan scene, upload assets, and submit the render job."

    def invoke(self, context, event):
        # ── Reset state ──────────────────────────────────────────────────────
        with _sub_lock:
            _sub["state"]       = "IDLE"
            _sub["deps"]        = []
            _sub["missing"]     = []
            _sub["step"]        = 0
            _sub["step_states"] = {1: "pending", 2: "pending", 3: "pending", 4: "pending"}
            _sub["step_progress"] = 0.0
            _sub["file_idx"]    = 0
            _sub["file_total"]  = 0
            _sub["file_name"]   = ""
            _sub["file_progress"] = 0.0
            _sub["log"]         = []
            _sub["job_number"]  = ""
            _sub["job_id"]      = ""
            _sub["uploaded"]    = 0
            _sub["skipped"]     = 0
            _sub["error"]       = ""
            _sub["failed_step"] = 0
            _sub["cancel"]      = False
            _sub["gcp_mode"]    = False

        # Pre-flight validation
        token      = _get_token()
        blend_path = bpy.context.blend_data.filepath

        if not token:
            self.report({"ERROR"}, "Not connected — click Connect first.")
            return {"CANCELLED"}
        if not blend_path:
            self.report({"ERROR"}, "Please save your .blend file before submitting.")
            return {"CANCELLED"}
        scene = context.scene
        if scene.get("rf_project", "none") in ("none", "", None):
            self.report({"ERROR"}, "No active project selected. Create one in Admin → Projects first.")
            return {"CANCELLED"}

        provider = scene.get("rf_provider", "renderfarm")

        if provider == "gcp":
            # GCP path: no dep scan needed — only the .blend itself is uploaded
            with _sub_lock:
                _sub["gcp_mode"] = True
                _sub["state"]    = "PREFLIGHT"
        else:
            # ── Scan deps (fast — no I/O except stat) ───────────────────────────
            deps    = _scan_deps()
            missing = [d for d in deps if not d["exists"]]
            with _sub_lock:
                _sub["deps"]    = deps
                _sub["missing"] = missing
                _sub["state"]   = "PREFLIGHT"

        try:
            return context.window_manager.invoke_props_dialog(
                self, width=700, confirm_text="Close")
        except TypeError:
            return context.window_manager.invoke_props_dialog(self, width=700)

    def draw(self, context):
        state = _sub["state"]
        if state == "PREFLIGHT":
            _draw_preflight(self.layout)
        elif state == "SUBMITTING":
            _draw_submitting(self.layout)
        elif state == "COMPLETE":
            _draw_complete(self.layout)
        elif state == "ERROR":
            _draw_error(self.layout)
        else:
            self.layout.label(text="Initialising…")

    def execute(self, context):
        # Called when user clicks the built-in "Close" button
        return {"FINISHED"}

    def check(self, context):
        # Returning True forces a redraw — only needed while actively submitting
        return _sub["state"] == "SUBMITTING"


class RF_OT_StartSubmission(Operator):
    """Transition from PREFLIGHT to SUBMITTING; start the background thread."""
    bl_idname = "rf.start_submission"
    bl_label  = "Start Submission"

    def execute(self, context):
        token      = _get_token()
        blend_path = bpy.context.blend_data.filepath

        if not token or not blend_path:
            self.report({"ERROR"}, "Missing token or blend path.")
            return {"CANCELLED"}

        # Capture scene-level properties NOW before the thread starts
        payload     = _build_payload(context)
        scene_props = dict(payload)
        provider    = scene_props.get("provider", "renderfarm")

        with _sub_lock:
            _sub["state"]    = "SUBMITTING"
            _sub["cancel"]   = False
            _sub["gcp_mode"] = (provider == "gcp")

        # Branch: GCP uses a direct GCS upload path; renderfarm uses the full asset pipeline
        if provider == "gcp":
            run_fn   = _run_gcp_submission
            run_args = (scene_props, token, blend_path, payload)
        else:
            run_fn   = _run_submission
            run_args = (scene_props, token, blend_path, payload)

        # Start background thread
        t = threading.Thread(
            target=run_fn,
            args=run_args,
            daemon=True,
        )
        t.start()

        # Register tick timer — keeps dialog alive and redraws
        def _tick():
            _redraw()
            return 0.15 if _sub["state"] == "SUBMITTING" else None

        bpy.app.timers.register(_tick, first_interval=0.15)

        return {"FINISHED"}


class RF_OT_CancelSubmission(Operator):
    """Set the cancel flag so the background thread stops gracefully."""
    bl_idname = "rf.cancel_submission"
    bl_label  = "Cancel"

    def execute(self, context):
        with _sub_lock:
            _sub["cancel"] = True
        return {"FINISHED"}


class RF_OT_OpenDashboard(Operator):
    """Open the submitted job page in a browser."""
    bl_idname = "rf.open_dashboard"
    bl_label  = "Open Dashboard"

    def execute(self, context):
        job_number = _sub.get("job_number", "")
        if job_number:
            webbrowser.open(f"{WEB_BASE}/jobs/{job_number}")
        else:
            webbrowser.open(WEB_BASE)
        return {"FINISHED"}


# ── Kept for backward compat ──────────────────────────────────────────────────
class RF_OT_SubmitClose(Operator):
    """No-op kept for compatibility."""
    bl_idname = "rf.submit_close"
    bl_label  = "Close"

    def execute(self, context):
        return {"FINISHED"}


class RF_OT_SaveAndContinue(Operator):
    """Save the .blend file then start uploading."""
    bl_idname = "rf.save_and_continue"
    bl_label  = "Save Scene and Continue Submission"

    def execute(self, context):
        bpy.ops.wm.save_mainfile()
        bpy.ops.rf.start_submission()
        return {"FINISHED"}


class RF_OT_ContinueSubmit(Operator):
    """Start uploading without saving (kept for compat)."""
    bl_idname = "rf.continue_submit"
    bl_label  = "Continue Submission"

    def execute(self, context):
        bpy.ops.rf.start_submission()
        return {"FINISHED"}


# ── Environment / asset management operators ──────────────────────────────────
class RF_OT_AddEnvVar(Operator):
    bl_idname = "rf.add_env_var";    bl_label = "Add Extra Environment Variable"
    def execute(self, context):
        context.scene.rf_env_vars.variables.add(); return {"FINISHED"}

class RF_OT_RemoveEnvVar(Operator):
    bl_idname = "rf.remove_env_var"; bl_label = "Remove Variable"
    index: bpy.props.IntProperty()
    def execute(self, context):
        context.scene.rf_env_vars.variables.remove(self.index); return {"FINISHED"}

class RF_OT_AddFileAsset(Operator):
    bl_idname = "rf.add_file_asset"; bl_label = "Add Extra File Asset"
    filepath: StringProperty(subtype="FILE_PATH")
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self); return {"RUNNING_MODAL"}
    def execute(self, context):
        if self.filepath:
            context.scene.rf_extra_files.add().file_path = self.filepath
        return {"FINISHED"}

class RF_OT_RemoveFileAsset(Operator):
    bl_idname = "rf.remove_file_asset"; bl_label = "Remove"
    index: bpy.props.IntProperty()
    def execute(self, context):
        context.scene.rf_extra_files.remove(self.index); return {"FINISHED"}

class RF_OT_AddDirAsset(Operator):
    bl_idname = "rf.add_dir_asset"; bl_label = "Add Extra Directory Asset"
    filepath: StringProperty(subtype="DIR_PATH")
    def invoke(self, context, event):
        self.filepath = ""; context.window_manager.fileselect_add(self); return {"RUNNING_MODAL"}
    def execute(self, context):
        if self.filepath:
            context.scene.rf_extra_dirs.add().dir_path = self.filepath
        return {"FINISHED"}

class RF_OT_RemoveDirAsset(Operator):
    bl_idname = "rf.remove_dir_asset"; bl_label = "Remove"
    index: bpy.props.IntProperty()
    def execute(self, context):
        context.scene.rf_extra_dirs.remove(self.index); return {"FINISHED"}


# ──────────────────────────────────────────────────────────────────────────────
# Panels
# ──────────────────────────────────────────────────────────────────────────────
class _Base(Panel):
    bl_space_type  = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context     = "render"
    def draw(self, context): pass

class RF_PT_Root(_Base):
    bl_label  = "Renderfarm Render Submitter"
    bl_idname = "RF_PT_root"

class RF_PT_Job(_Base):
    bl_label     = "Renderfarm Job"
    bl_idname    = "RF_PT_job"
    bl_parent_id = "RF_PT_root"

    def draw(self, context):
        layout = self.layout
        scene  = context.scene
        email  = _get_email()

        box = layout.box()
        row = box.row()
        if email:
            row.label(text=email, icon="CHECKMARK")
            row.operator("rf.disconnect", text="Disconnect", icon="PANEL_CLOSE")
        else:
            row.label(text="Not connected", icon="UNLINKED")

        layout.operator("rf.connect",        text="Connect",        icon="LINKED")
        layout.operator("rf.preview_script", text="Preview Script", icon="TEXT")

        # ── Provider selector ─────────────────────────────────────────────
        layout.separator()
        row = layout.row(align=True)
        row.label(text="Provider:")
        row.prop(scene, "rf_provider", text="", expand=True)
        layout.separator()

        # ── Project guard — block submission when no active project exists ───
        has_project = scene.get("rf_project", "none") not in ("none", "", None)
        if not has_project:
            box = layout.box()
            box.alert = True
            box.label(text="No active project selected.", icon="ERROR")
            box.label(text="Go to Admin → Projects and create one first.")
        else:
            submit_label = "Submit to GCP" if scene.rf_provider == "gcp" else "Submit"
            layout.operator("rf.submit", text=submit_label, icon="RENDER_STILL")

        if scene.rf_status_msg:
            row  = layout.row()
            icon = ("CHECKMARK" if scene.rf_status_type == "OK"
                    else "ERROR" if scene.rf_status_type == "ERROR" else "INFO")
            row.label(text=scene.rf_status_msg, icon=icon)

class RF_PT_Configuration(_Base):
    bl_label = "Configuration"; bl_idname = "RF_PT_configuration"; bl_parent_id = "RF_PT_root"

class RF_PT_General(_Base):
    bl_label = "General"; bl_idname = "RF_PT_general"; bl_parent_id = "RF_PT_configuration"

    def draw(self, context):
        layout = self.layout; scene = context.scene
        layout.label(text="Job Title:");      layout.prop(scene, "rf_job_title",        text="")
        layout.label(text="Project:");        layout.prop(scene, "rf_project",           text="")
        layout.label(text="Instance Type:");  layout.prop(scene, "rf_instance_type",     text="")
        layout.label(text="Machine Type:");   layout.prop(scene, "rf_machine_type",      text="")
        layout.prop(scene, "rf_preemptible", text="Preemptible")
        layout.label(text="Preemptible Retries:"); layout.prop(scene, "rf_preemptible_retries", text="")
        layout.label(text="Blender Version:"); layout.prop(scene, "rf_blender_version",  text="")
        layout.label(text="Render Software:"); layout.prop(scene, "rf_render_software",  text="")

class RF_PT_RenderSettings(_Base):
    bl_label = "Render Settings"; bl_idname = "RF_PT_render_settings"; bl_parent_id = "RF_PT_configuration"

    def draw(self, context):
        layout = self.layout; scene = context.scene
        layout.row(align=True).prop(scene, "rf_resolution_x",   text="Resolution X")
        layout.row(align=True).prop(scene, "rf_resolution_y",   text="Resolution Y")
        layout.row(align=True).prop(scene, "rf_resolution_pct", text="Resolution %")
        layout.label(text="Camera:"); layout.prop(scene, "rf_camera_override", text="")
        layout.row(align=True).prop(scene, "rf_samples", text="Samples")

class RF_PT_Frames(_Base):
    bl_label = "Frames"; bl_idname = "RF_PT_frames"; bl_parent_id = "RF_PT_configuration"

    def draw(self, context):
        layout = self.layout; scene = context.scene
        layout.row(align=True).prop(scene, "rf_chunk_size", text="Chunk Size")
        layout.prop(scene, "rf_use_custom_range", text="Use Custom Range")
        if scene.rf_use_custom_range:
            layout.row(align=True).prop(scene, "rf_frame_range", text="Custom Range")
        layout.prop(scene, "rf_use_scout_frames", text="Use Scout Frames")
        layout.row(align=True).prop(scene, "rf_scout_frames", text="Scout Frames")

        # ── Tiles (Mosaic Rendering) ───────────────────────────────────────────
        layout.separator()
        layout.prop(scene, "rf_use_tiles", text="Tiled (Mosaic) Rendering")
        if scene.rf_use_tiles:
            layout.row(align=True).prop(scene, "rf_tiles", text="Tiles")
            # Inline grid hint: "9 tiles (3×3 grid)"
            try:
                import math
                tp = scene.rf_tiles.replace(" ", "").split("-")
                ts = int(tp[0]); te = int(tp[-1]) if len(tp) > 1 else ts
                tc = max(1, te - ts + 1)
                sq = int(math.isqrt(tc))
                hint = f"{tc} tiles ({sq}×{sq} grid)" if sq * sq == tc else f"{tc} tiles"
                layout.label(text=hint, icon="MESH_GRID")
            except Exception:
                pass

class RF_PT_FrameInfo(_Base):
    bl_label = "Frame Info"; bl_idname = "RF_PT_frame_info"
    bl_parent_id = "RF_PT_configuration"; bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout; scene = context.scene
        for prop, label in [("rf_frame_spec","Frame Spec"),("rf_frame_count","Frame Count"),("rf_task_count","Task Count")]:
            row = layout.row(align=True); row.active = False; row.prop(scene, prop, text=label)

class RF_PT_Addons(_Base):
    bl_label = "Add-ons"; bl_idname = "RF_PT_addons"; bl_parent_id = "RF_PT_root"

    def draw(self, context):
        layout = self.layout; scene = context.scene
        if scene.rf_addon_props:
            for addon in scene.rf_addon_props:
                row = layout.row()
                row.prop(addon, "enabled", text=addon.name)
                row.prop(addon, "menu_option", text="")
        else:
            layout.label(text="Click Connect to load compatible add-ons.", icon="INFO")

class RF_PT_Advanced(_Base):
    bl_label = "Advanced"; bl_idname = "RF_PT_advanced"; bl_parent_id = "RF_PT_root"

    def draw(self, context):
        self.layout.prop(context.scene, "rf_output_folder", text="Output Folder")

class RF_PT_ExtraFiles(_Base):
    bl_label = "Extra File Assets"; bl_idname = "RF_PT_extra_files"; bl_parent_id = "RF_PT_advanced"

    def draw(self, context):
        layout = self.layout; scene = context.scene
        layout.operator("rf.add_file_asset", text="Add Extra File Asset")
        for i, item in enumerate(scene.rf_extra_files):
            row = layout.row(align=True); row.prop(item, "file_path", text="")
            row.operator("rf.remove_file_asset", text="", icon="X").index = i

class RF_PT_ExtraDirs(_Base):
    bl_label = "Extra Directory Assets"; bl_idname = "RF_PT_extra_dirs"; bl_parent_id = "RF_PT_advanced"

    def draw(self, context):
        layout = self.layout; scene = context.scene
        layout.operator("rf.add_dir_asset", text="Add Extra Directory Asset")
        for i, item in enumerate(scene.rf_extra_dirs):
            row = layout.row(align=True); row.prop(item, "dir_path", text="")
            row.operator("rf.remove_dir_asset", text="", icon="X").index = i

class RF_PT_ExtraEnv(_Base):
    bl_label = "Extra Environment"; bl_idname = "RF_PT_extra_env"; bl_parent_id = "RF_PT_advanced"

    def draw(self, context):
        layout = self.layout; scene = context.scene
        layout.operator("rf.add_env_var", text="Add Extra Environment Variable")
        for i, var in enumerate(scene.rf_env_vars.variables):
            row = layout.row()
            row.prop(var, "variable_name",  text="Variable")
            row.prop(var, "variable_value", text="Value")
            row.operator("rf.remove_env_var", text="", icon="X").index = i

class RF_PT_AdditionalOptions(_Base):
    bl_label = "Additional Rendering Options"; bl_idname = "RF_PT_additional_options"; bl_parent_id = "RF_PT_advanced"

    def draw(self, context):
        layout = self.layout; scene = context.scene
        layout.prop(scene, "rf_disable_audio",           text="Disable Audio")
        layout.prop(scene, "rf_update_camera_per_frame", text="Update active camera every frame")
        layout.prop(scene, "rf_render_all_view_layers",  text="Render all active view layers")


# ──────────────────────────────────────────────────────────────────────────────
# Registration
# ──────────────────────────────────────────────────────────────────────────────
CLASSES = [
    RFExtraFileAsset, RFExtraDirAsset, RFEnvVar, RFEnvVarList, RFAddonProp,
    RFPreferences,
    RF_OT_Connect, RF_OT_Disconnect, RF_OT_PreviewScript,
    RF_OT_Submit, RF_OT_StartSubmission, RF_OT_CancelSubmission,
    RF_OT_SubmitClose, RF_OT_SaveAndContinue, RF_OT_ContinueSubmit,
    RF_OT_OpenDashboard,
    RF_OT_AddEnvVar, RF_OT_RemoveEnvVar,
    RF_OT_AddFileAsset, RF_OT_RemoveFileAsset,
    RF_OT_AddDirAsset,  RF_OT_RemoveDirAsset,
    RF_PT_Root, RF_PT_Job, RF_PT_Configuration,
    RF_PT_General, RF_PT_RenderSettings, RF_PT_Frames, RF_PT_FrameInfo,
    RF_PT_Addons, RF_PT_Advanced,
    RF_PT_ExtraFiles, RF_PT_ExtraDirs, RF_PT_ExtraEnv, RF_PT_AdditionalOptions,
]

def register():
    for cls in CLASSES:
        register_class(cls)

    S = bpy.types.Scene
    S.rf_job_title             = bpy.props.StringProperty(name="Job Title", default="Blender Linux Render")
    S.rf_provider              = bpy.props.EnumProperty(
        name="Provider",
        description="Cloud backend to submit this job to",
        items=[
            ("renderfarm", "Renderfarm", "Submit to the Renderfarm cloud backend"),
            ("gcp",        "GCP",        "Submit to Google Cloud Platform rendering"),
        ],
        default="renderfarm",
    )
    S.rf_project               = bpy.props.EnumProperty(name="Project", items=[("none","— click Connect —","")])
    S.rf_instance_type         = bpy.props.EnumProperty(name="Instance Type", items=[("GPU","GPU",""),("CPU","CPU","")], update=_update_instance_type)
    S.rf_machine_type          = bpy.props.EnumProperty(name="Machine Type", items=INSTANCE_TYPES["GPU"])
    S.rf_preemptible           = bpy.props.BoolProperty(name="Preemptible", default=True)
    S.rf_preemptible_retries   = bpy.props.IntProperty(name="Preempted Retries", default=1, min=1, max=100)
    S.rf_blender_version       = bpy.props.EnumProperty(name="Blender Version", items=BLENDER_VERSIONS)
    S.rf_render_software       = bpy.props.EnumProperty(name="Render Software", items=[("Cycles","Cycles",""),("Eevee","Eevee","")])
    S.rf_resolution_x          = bpy.props.IntProperty(name="Resolution X", min=1, max=16384)
    S.rf_resolution_y          = bpy.props.IntProperty(name="Resolution Y", min=1, max=16384)
    S.rf_resolution_pct        = bpy.props.IntProperty(name="Resolution %", min=1, max=1000)
    S.rf_camera_override       = bpy.props.EnumProperty(name="Camera", items=[("none","None","")])
    S.rf_samples               = bpy.props.IntProperty(name="Samples", min=1, max=10000)
    S.rf_chunk_size            = bpy.props.IntProperty(name="Chunk Size", default=1, min=1, max=800, update=_on_chunk_updated)
    S.rf_use_custom_range      = bpy.props.BoolProperty(name="Use Custom Range", default=True)
    S.rf_frame_range           = bpy.props.StringProperty(name="Custom Range", default="1-100", update=_on_range_updated)
    S.rf_use_scout_frames      = bpy.props.BoolProperty(name="Use Scout Frames", default=True)
    S.rf_scout_frames          = bpy.props.StringProperty(name="Scout Frames", default="fml:3")
    S.rf_use_tiles             = bpy.props.BoolProperty(
        name="Tiled Rendering",
        description="Split each frame across multiple cloud machines (mosaic rendering). Enter a range like 1-9 for a 3×3 grid.",
        default=False,
        update=_on_tiles_updated,
    )
    S.rf_tiles                 = bpy.props.StringProperty(
        name="Tiles",
        description="Tile range e.g. 1-9 for a 3×3 grid, 1-16 for 4×4. Each tile renders on its own cloud machine.",
        default="1-9",
        update=_on_tiles_updated,
    )
    S.rf_frame_spec            = bpy.props.StringProperty(name="Frame Spec",  default="")
    S.rf_frame_count           = bpy.props.StringProperty(name="Frame Count", default="")
    S.rf_task_count            = bpy.props.StringProperty(name="Task Count",  default="")
    S.rf_output_folder         = bpy.props.StringProperty(name="Output Folder", default="", subtype="DIR_PATH")
    S.rf_disable_audio         = bpy.props.BoolProperty(name="Disable Audio", default=True)
    S.rf_update_camera_per_frame = bpy.props.BoolProperty(name="Update active camera every frame", default=False)
    S.rf_render_all_view_layers  = bpy.props.BoolProperty(name="Render all active view layers", default=False)
    S.rf_status_msg            = bpy.props.StringProperty(default="")
    S.rf_status_type           = bpy.props.StringProperty(default="INFO")
    S.rf_extra_files           = CollectionProperty(type=RFExtraFileAsset)
    S.rf_extra_dirs            = CollectionProperty(type=RFExtraDirAsset)
    S.rf_env_vars              = bpy.props.PointerProperty(type=RFEnvVarList)
    S.rf_addon_props           = CollectionProperty(type=RFAddonProp)

    # Auto-restore saved session
    token, email = _load_token()
    if token and email:
        def _restore():
            try:
                prefs = bpy.context.preferences.addons[__name__].preferences
                prefs.access_token = token
                prefs.user_email   = email
            except Exception:
                pass
        bpy.app.timers.register(_restore, first_interval=0.5)


def unregister():
    _stop_auth_server()
    props = [
        "rf_job_title","rf_project","rf_instance_type","rf_machine_type",
        "rf_preemptible","rf_preemptible_retries","rf_blender_version","rf_render_software",
        "rf_resolution_x","rf_resolution_y","rf_resolution_pct","rf_camera_override","rf_samples",
        "rf_chunk_size","rf_use_custom_range","rf_frame_range","rf_use_scout_frames","rf_scout_frames",
        "rf_frame_spec","rf_frame_count","rf_task_count","rf_output_folder","rf_disable_audio",
        "rf_update_camera_per_frame","rf_render_all_view_layers","rf_status_msg","rf_status_type",
        "rf_extra_files","rf_extra_dirs","rf_env_vars","rf_addon_props",
        "rf_use_tiles","rf_tiles","rf_provider",
    ]
    for p in props:
        if hasattr(bpy.types.Scene, p): delattr(bpy.types.Scene, p)
    for cls in reversed(CLASSES):
        unregister_class(cls)


if __name__ == "__main__":
    register()
