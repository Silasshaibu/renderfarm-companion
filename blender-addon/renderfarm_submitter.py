bl_info = {
    "name":        "Renderfarm Render Submitter",
    "author":      "Renderfarm",
    "version":     (2, 1, 0),
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
        global _machine_type_cache
        scene    = bpy.context.scene
        projects = _get("/projects", token)
        _machine_type_cache = _fetch_machine_types()
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

# Fallback machine type list — used if the API is unreachable.
# Only shows machine types that were enabled at the time this addon was built.
# The live list is fetched from /api/machine-types on login.
_FALLBACK_MACHINE_TYPES = {
    "GPU": [("t4-1", "T4 16GB · 4 vCPU · 15 GB", "")],
    "CPU": [("n1-4", "CPU · 4 vCPU · 15 GB",      "")],
}

# Cache populated on login — { "GPU": [...], "CPU": [...] }
_machine_type_cache: dict = {}

def _fetch_machine_types():
    """Fetch enabled machine types from the API. Returns a dict keyed by instance type."""
    try:
        rows = _get("/machine-types")  # no auth needed
        result: dict = {"GPU": [], "CPU": []}
        for row in rows:
            instance = row.get("instance", "GPU")
            if instance not in result:
                result[instance] = []
            result[instance].append((row["id"], row["label"], ""))
        # Fall back to hardcoded list for any category with no enabled types
        for key in ("GPU", "CPU"):
            if not result.get(key):
                result[key] = _FALLBACK_MACHINE_TYPES.get(key, [("none", "Unavailable", "")])
        return result
    except Exception:
        return _FALLBACK_MACHINE_TYPES.copy()

# ──────────────────────────────────────────────────────────────────────────────
# Populate helpers
# ──────────────────────────────────────────────────────────────────────────────
def _populate_project_menu(projects):
    items = [(str(p["id"]), p["name"], "") for p in projects if p.get("isActive", True)]
    if not items:
        items = [("none", "No Projects", "")]
    bpy.types.Scene.rf_project = bpy.props.EnumProperty(
        name="Project",
        description="Renderfarm project to run the job in.",
        items=items,
    )

def _populate_machine_menu(instance_type="GPU"):
    global _machine_type_cache
    cache = _machine_type_cache if _machine_type_cache else _FALLBACK_MACHINE_TYPES
    items = cache.get(instance_type) or cache.get("GPU") or [("none", "Unavailable", "")]
    bpy.types.Scene.rf_machine_type = bpy.props.EnumProperty(
        name="Machine Type",
        description="Select the machine for your render. Enabled types are managed in Admin → Machine Types.",
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

def _parse_frame_spec(spec):
    """
    Parse a Conductor frame spec string into a sorted list of unique frame numbers.
    Supports:
      42            single frame
      1-100         inclusive range
      1-100x2       range with step
      1,7,10-20x3   comma-separated mixed list
    Does NOT handle fml:/auto: — those are resolved before calling this.
    Returns [] on parse error.
    """
    frames = set()
    spec = spec.replace(" ", "")
    if not spec:
        return []
    for part in spec.split(","):
        if not part:
            continue
        try:
            if "x" in part:
                range_part, step = part.rsplit("x", 1)
                step = int(step)
            else:
                range_part = part
                step = 1
            if "-" in range_part.lstrip("-"):
                # handle negative numbers: find the second '-' after optional leading '-'
                stripped = range_part.lstrip("-")
                dash_idx  = stripped.find("-")
                if range_part.startswith("-"):
                    start_s = "-" + stripped[:dash_idx]
                    end_s   = stripped[dash_idx+1:]
                else:
                    idx     = range_part.find("-")
                    start_s = range_part[:idx]
                    end_s   = range_part[idx+1:]
                start = int(start_s); end = int(end_s)
                frames.update(range(start, end + 1, max(1, step)))
            else:
                frames.add(int(range_part))
        except (ValueError, ZeroDivisionError):
            pass
    return sorted(frames)


def _resolve_scout_spec(scout_expr, all_frames):
    """
    Resolve fml:N / auto:N shorthand or fall back to _parse_frame_spec.
    Returns a sorted list of scout frame numbers.
    """
    if not scout_expr or not all_frames:
        return []
    expr = scout_expr.strip()
    try:
        if expr.lower().startswith("fml:"):
            n = max(1, int(expr.split(":")[1]))
            if n == 1:
                return [all_frames[0]]
            if n == 2:
                return [all_frames[0], all_frames[-1]]
            # First, middle positions, last
            indices = [0]
            mid_count = n - 2
            for i in range(1, mid_count + 1):
                idx = round(i * (len(all_frames) - 1) / (mid_count + 1))
                indices.append(idx)
            indices.append(len(all_frames) - 1)
            return sorted(set(all_frames[i] for i in indices))
        elif expr.lower().startswith("auto:"):
            n = max(1, int(expr.split(":")[1]))
            if n >= len(all_frames):
                return list(all_frames)
            indices = [round(i * (len(all_frames) - 1) / (n - 1)) for i in range(n)] if n > 1 else [0]
            return sorted(set(all_frames[i] for i in indices))
    except (ValueError, IndexError):
        pass
    # Fall back to explicit frame spec
    explicit = _parse_frame_spec(expr)
    return [f for f in explicit if f in set(all_frames)]


def _populate_frame_info():
    """
    Recompute and write all six Frame Info read-only fields:
      Frame Spec, Scout Spec, Frame Count, Task Count,
      Scout Frame Count, Scout Task Count
    Called whenever chunk size, frame range, scout frames, or tiles change.
    """
    scene = bpy.context.scene
    try:
        import math as _math

        # ── Resolve full frame list ───────────────────────────────────────────
        frame_range = scene.rf_frame_range if scene.rf_use_custom_range else _get_scene_frame_range()
        all_frames  = _parse_frame_spec(frame_range)
        if not all_frames:
            # Fallback simple parse (handles plain "1-5" before _parse_frame_spec)
            parts = frame_range.replace(" ", "").split("-")
            try:
                start = int(parts[0]); end = int(parts[-1]) if len(parts) > 1 else start
                all_frames = list(range(start, end + 1))
            except ValueError:
                all_frames = []

        chunk = max(1, scene.rf_chunk_size)
        count = len(all_frames)
        tasks = max(1, _math.ceil(count / chunk)) if count else 0

        # ── Tile multiplier ───────────────────────────────────────────────────
        tile_count = 1
        if scene.rf_use_tiles and scene.rf_tiles.strip():
            tp = scene.rf_tiles.replace(" ", "").split("-")
            try:
                ts = int(tp[0]); te = int(tp[-1]) if len(tp) > 1 else ts
                tile_count = max(1, te - ts + 1)
            except ValueError:
                pass
        tasks *= tile_count

        # ── Scout frames ──────────────────────────────────────────────────────
        scout_frames     = []
        scout_spec_str   = ""
        scout_frame_count = 0
        scout_task_count  = 0

        if scene.rf_use_scout_frames and scene.rf_scout_frames.strip():
            scout_frames = _resolve_scout_spec(scene.rf_scout_frames.strip(), all_frames)
            scout_spec_str = ",".join(str(f) for f in scout_frames)

            # Scout frame count: every frame rendered in any chunk containing a scout frame
            # (because remote nodes execute entire chunks)
            scout_set    = set(scout_frames)
            all_frames_s = set(all_frames)
            affected_chunks = set()
            for i, f in enumerate(all_frames):
                chunk_idx = i // chunk
                if f in scout_set:
                    affected_chunks.add(chunk_idx)

            # Count all frames in affected chunks
            rendered_in_scout = sum(
                1 for i, f in enumerate(all_frames) if i // chunk in affected_chunks
            )
            scout_frame_count = rendered_in_scout * tile_count
            scout_task_count  = len(affected_chunks) * tile_count

        # ── Write to scene properties ─────────────────────────────────────────
        scene.rf_frame_spec        = frame_range
        scene.rf_scout_spec        = scout_spec_str
        scene.rf_frame_count       = str(count)
        scene.rf_task_count        = str(tasks)
        scene.rf_scout_frame_count = str(scout_frame_count) if scout_frame_count else ""
        scene.rf_scout_task_count  = str(scout_task_count)  if scout_task_count  else ""

    except Exception:
        pass


def _on_chunk_updated(self, context):  _populate_frame_info()
def _on_range_updated(self, context):  _populate_frame_info()
def _on_tiles_updated(self, context):  _populate_frame_info()
def _on_scout_updated(self, context):  _populate_frame_info()

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
        "project_id":          scene.rf_project if scene.rf_project not in ("none", "", None) else "",
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
    # SUBMITTING — legacy 4-step (GCP path keeps these)
    "step": 0,
    "step_states": {1: "pending", 2: "pending", 3: "pending", 4: "pending"},
    "step_progress": 0.0,
    "file_idx": 0,
    "file_total": 0,
    "file_name": "",
    "file_progress": 0.0,
    "log": [],          # last 5 lines
    # SUBMITTING — Conductor 3-bar progress (renderfarm path)
    "sub_status":         "idle",  # "analyzing"|"md5"|"uploading"|"submitting"|"done"|"error"
    "md5_total":          0,
    "md5_done":           0,
    "upload_total":       0,
    "upload_done":        0,
    "upload_bytes_total": 0,
    "upload_bytes_sent":  0,
    "job_progress":       0.0,   # 0.0 – 1.0  (bar 1, purple)
    "files":              [],    # list of per-file state dicts — drives the file rows
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
    Background daemon thread — Conductor-style pipeline:
      1. Parallel MD5 computation (ThreadPoolExecutor, max 8 workers)
      2. Server preflight check (deduplication)
      3. Create job record
      4. Parallel file upload (ThreadPoolExecutor, max 2 workers)
      5. Patch job to 'pending'

    All UI state written only to _sub dict; redraws triggered via
    bpy.app.timers.register(_redraw, ...) which runs on Blender's main thread.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _bump_progress(val):
        with _sub_lock:
            _sub["job_progress"] = max(_sub["job_progress"], val)
        bpy.app.timers.register(_redraw, first_interval=0.05)

    try:
        # ── Step 1: Gather uploadable assets (already scanned in PREFLIGHT) ──
        with _sub_lock:
            _sub["sub_status"] = "analyzing"
            _sub["job_progress"] = 0.02
        _log("Analyzing scene dependencies…")
        bpy.app.timers.register(_redraw, first_interval=0.05)

        deps       = _sub["deps"]
        uploadable = [d for d in deps if d["exists"]]

        # Build per-file state list that drives the UI rows
        files_state = []
        for dep in uploadable:
            fs = {
                "dep":          dep,
                "path":         dep["abs_path"],
                "name":         dep["name"],
                "size":         dep["size"],
                "hash":         None,      # SHA-256 hex digest
                "md5_done":     False,     # UI: MD5/hash phase complete
                "upload_done":  False,     # UI: upload complete
                "upload_bytes": 0,         # bytes sent so far
                "skipped":      False,     # dedup hit — already on server
            }
            files_state.append(fs)

        with _sub_lock:
            _sub["files"]     = files_state
            _sub["md5_total"] = len(files_state)
            _sub["md5_done"]  = 0
            _sub["sub_status"] = "md5"
        _bump_progress(0.05)
        _log(f"Found {len(uploadable)} assets — computing checksums in parallel…")

        # ── Step 2: Parallel hash (SHA-256 displayed as "MD5" to match Conductor) ──
        hash_lock = threading.Lock()

        def _hash_file(fs):
            if _sub.get("cancel"):
                raise InterruptedError("Cancelled")
            sha = _sha256_file(fs["path"])
            fs["dep"]["sha256"] = sha
            fs["hash"]    = sha
            fs["md5_done"] = True
            with hash_lock:
                with _sub_lock:
                    _sub["md5_done"] += 1
                    done = _sub["md5_done"]
                    total = _sub["md5_total"]
                # Update job_progress: hash phase covers 5%→30%
                _bump_progress(0.05 + 0.25 * (done / max(1, total)))
            bpy.app.timers.register(_redraw, first_interval=0.05)

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(files_state)))) as pool:
            futs = {pool.submit(_hash_file, fs): fs for fs in files_state}
            for fut in as_completed(futs):
                if _sub.get("cancel"):
                    raise InterruptedError("Cancelled")
                fut.result()   # re-raise any exceptions from the thread

        _log("Checksums done — running server preflight check…")
        _bump_progress(0.30)

        # ── Step 2b: Server preflight (deduplication) ────────────────────────
        with _sub_lock:
            _sub["sub_status"] = "submitting"

        preflight_resp = _post(
            "/jobs/preflight",
            {"assets": [{"sha256": fs["hash"]} for fs in files_state]},
            token,
        )
        missing_set = set(preflight_resp.get("missing", []))

        # Mark files that are already on the server
        for fs in files_state:
            if fs["hash"] not in missing_set:
                fs["skipped"]     = True
                fs["upload_done"] = True

        to_upload = [fs for fs in files_state if not fs["skipped"]]
        skipped   = len(files_state) - len(to_upload)

        _log(f"Server needs {len(to_upload)} of {len(uploadable)} assets to upload")
        _bump_progress(0.35)

        # Build manifest
        v           = bpy.app.version
        frame_range = scene_props.get("frames", "1-1")
        parts       = frame_range.replace(" ", "").split("-")
        manifest = {
            "scene":           os.path.basename(blend_path),
            "blender_version": f"{v[0]}.{v[1]}.{v[2]}",
            "renderer":        scene_props.get("render_software", "Cycles"),
            "instance_type":   scene_props.get("instance_type", "CPU"),
            "machine_type":    scene_props.get("machine_type", ""),
            "frame_start":     int(parts[0]),
            "frame_end":       int(parts[-1]) if len(parts) > 1 else int(parts[0]),
            "chunk_size":      scene_props.get("chunk_size", 1),
            "assets": [
                {
                    "path":       fs["dep"]["rel_path"],
                    "sha256":     fs["hash"],
                    "size_bytes": fs["size"],
                    "type":       fs["dep"]["type"],
                }
                for fs in files_state
            ],
        }

        # ── Step 3: Create job record ─────────────────────────────────────────
        _log("Creating job record…")
        _bump_progress(0.40)

        submit_payload = dict(payload)
        submit_payload["status"]       = "uploading"
        submit_payload["manifest"]     = manifest
        submit_payload["assets_total"] = len(uploadable)

        result     = _post("/jobs", submit_payload, token)
        job_number = result.get("jobNumber", "?")
        job_id     = result.get("id", "")

        with _sub_lock:
            _sub["job_number"] = str(job_number)
            _sub["job_id"]     = str(job_id)
        _log(f"Job {job_number} created — uploading {len(to_upload)} files…")
        _bump_progress(0.45)

        # ── Step 4: Parallel upload ────────────────────────────────────────────
        total_bytes = sum(fs["size"] for fs in to_upload)
        with _sub_lock:
            _sub["upload_total"]       = len(to_upload)
            _sub["upload_done"]        = 0
            _sub["upload_bytes_total"] = total_bytes
            _sub["upload_bytes_sent"]  = 0
            _sub["sub_status"]         = "uploading"

        sha256_to_url = {}
        upload_lock   = threading.Lock()
        uploaded      = 0

        def _upload_file(fs):
            nonlocal uploaded
            if _sub.get("cancel"):
                raise InterruptedError("Cancelled")

            def _prog(frac, _fs=fs):
                _fs["upload_bytes"] = int(frac * _fs["size"])
                with _sub_lock:
                    _sub["upload_bytes_sent"] = sum(
                        f["upload_bytes"] for f in files_state
                    )
                    total = max(1, _sub["upload_bytes_total"])
                    # Bar 3 covers 45%→95% of job_progress
                    _sub["job_progress"] = 0.45 + 0.50 * (
                        _sub["upload_bytes_sent"] / total
                    )
                bpy.app.timers.register(_redraw, first_interval=0.05)

            url = _upload_asset(
                fs["path"], fs["hash"], token,
                filename=fs["name"], progress_cb=_prog,
            )

            fs["upload_done"]  = True
            fs["upload_bytes"] = fs["size"]

            with upload_lock:
                nonlocal uploaded
                uploaded += 1
                sha256_to_url[fs["hash"]] = url or ""
                with _sub_lock:
                    _sub["upload_done"]        = uploaded
                    _sub["upload_bytes_sent"]  = sum(
                        f["upload_bytes"] for f in files_state
                    )

            bpy.app.timers.register(_redraw, first_interval=0.05)

        # Fetch cached URLs for already-deduplicated files (for the manifest)
        for fs in files_state:
            if fs["skipped"]:
                try:
                    r = _post("/assets?action=token",
                              {"sha256": fs["hash"], "filename": fs["name"],
                               "size_bytes": fs["size"]}, token)
                    if r.get("exists") and r.get("url"):
                        sha256_to_url[fs["hash"]] = r["url"]
                    else:
                        # Server cannot provide URL — re-queue for upload
                        fs["skipped"]     = False
                        fs["upload_done"] = False
                        to_upload.append(fs)
                except Exception as ex:
                    print(f"[RF] Dedup URL fetch failed for {fs['name']}: {ex}")
                    fs["skipped"]     = False
                    fs["upload_done"] = False
                    to_upload.append(fs)

        MAX_CONCURRENT_UPLOADS = 2
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_UPLOADS) as pool:
            futs = {pool.submit(_upload_file, fs): fs for fs in to_upload}
            for fut in as_completed(futs):
                if _sub.get("cancel"):
                    raise InterruptedError("Cancelled")
                fut.result()

        skipped = len(uploadable) - len(to_upload)   # recalculate after potential re-queue

        # ── Step 5: Finalise job ──────────────────────────────────────────────
        _log("Finalising job…")
        _bump_progress(0.96)

        resolved_assets = []
        for a in manifest["assets"]:
            entry = dict(a)
            entry["blob_url"] = sha256_to_url.get(a["sha256"], "")
            resolved_assets.append(entry)
        resolved_manifest = dict(manifest)
        resolved_manifest["assets"] = resolved_assets

        _patch(
            f"/jobs?id={job_id}",
            {
                "status":          "pending",
                "assets_uploaded": uploaded + skipped,
                "manifest":        resolved_manifest,
            },
            token,
        )

        _log(f"Done — {uploaded} uploaded, {skipped} already cached")

        with _sub_lock:
            _sub["state"]      = "COMPLETE"
            _sub["uploaded"]   = uploaded
            _sub["skipped"]    = skipped
            _sub["job_progress"] = 1.0
            _sub["sub_status"] = "done"

        bpy.app.timers.register(_redraw, first_interval=0.05)

    except InterruptedError:
        with _sub_lock:
            _sub["state"]       = "ERROR"
            _sub["error"]       = "Cancelled"
            _sub["failed_step"] = _sub["step"]
        bpy.app.timers.register(_redraw, first_interval=0.05)

    except Exception as e:
        import traceback
        with _sub_lock:
            _sub["state"]       = "ERROR"
            _sub["error"]       = str(e)
            _sub["failed_step"] = _sub["step"]
        print(f"[RF] Submission error:\n{traceback.format_exc()}")
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


def _draw_validation(layout):
    """Validation tab — pre-submission checks, asset scan, and action buttons."""
    scene    = bpy.context.scene
    is_dirty = bpy.data.is_dirty

    # ── Global warnings (always shown) ───────────────────────────────────────
    any_warning = False

    if is_dirty:
        row = layout.row()
        row.alert = True
        row.label(text="Scene has unsaved changes", icon="ERROR")
        any_warning = True

    # Camera check
    cam_override = getattr(scene, "rf_camera", "").strip()
    if not cam_override and not scene.camera:
        row = layout.row()
        row.alert = True
        row.label(text="No active camera in scene", icon="ERROR")
        any_warning = True

    if any_warning:
        layout.separator(factor=0.6)

    if _sub.get("gcp_mode"):
        # ── GCP path ─────────────────────────────────────────────────────────
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

    else:
        # ── Renderfarm path — asset scan ──────────────────────────────────────
        deps    = _sub["deps"]
        missing = [d for d in deps if not d["exists"]]
        present = [d for d in deps if d["exists"]]
        total_b = sum(d["size"] for d in present)

        row = layout.row()
        row.scale_y = 1.2
        row.label(text="Analyze Scene", icon="VIEWZOOM")
        layout.separator(factor=0.8)

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

        if missing:
            layout.separator(factor=0.5)
            col_warn = layout.column()
            col_warn.alert = True
            col_warn.label(text="Resolve missing assets before submitting.", icon="ERROR")

    # ── Action buttons ────────────────────────────────────────────────────────
    layout.separator(factor=0.8)
    action = layout.row(align=True)
    action.scale_y = 1.5

    if is_dirty:
        action.operator("rf.save_and_continue",
                        text="Save Scene and Continue Submission", icon="FILE_TICK")

    if _sub.get("gcp_mode"):
        action.operator("rf.start_submission",
                        text="Upload & Submit to GCP", icon="RENDER_STILL")
    else:
        deps_now = _sub.get("deps", [])
        missing  = [d for d in deps_now if not d["exists"]]
        if not missing:
            action.operator("rf.start_submission",
                            text="Continue Submission", icon="PLAY")
        else:
            action.operator("rf.start_submission",
                            text="Continue Anyway", icon="PLAY")

    layout.separator(factor=0.3)
    hint = layout.row()
    hint.scale_y = 0.8
    hint.label(text="Close button below cancels.", icon="INFO")


def _progress_bar(layout, frac):
    """Draw a native Blender progress bar (3.3+) or ASCII fallback."""
    frac = max(0.0, min(1.0, frac))
    row = layout.row()
    if bpy.app.version >= (3, 3, 0):
        try:
            row.progress(factor=frac, text="")
            return
        except Exception:
            pass
    # ASCII fallback
    done = int(frac * 30)
    row.label(text="[" + "█" * done + "·" * (30 - done) + "]")


def _draw_gcp_submitting(layout):
    """GCP 2-step progress view (preserved from original)."""
    job_num = _sub.get("job_number") or "Job"

    row = layout.row()
    row.scale_y = 1.2
    row.label(text=f"Submitting {job_num}…", icon="RENDER_ANIMATION")
    layout.separator(factor=0.8)

    labels      = _STEP_LABELS_GCP
    total_steps = 2
    upload_step = 1

    for n in range(1, total_steps + 1):
        state = _sub["step_states"][n]
        if state == "done":
            icon, status = "CHECKMARK", "Done"
        elif state == "active":
            icon, status = "RENDER_ANIMATION", "Working…"
        else:
            icon, status = "BLANK1", "Waiting"
        row = layout.row(align=True)
        row.label(text="", icon=icon)
        row.label(text=f"Step {n}/{total_steps} — {labels[n]}")
        right = row.row(); right.alignment = "RIGHT"; right.label(text=status)

    if _sub["step_states"][upload_step] == "active":
        layout.separator(factor=0.6)
        detail = layout.box()
        detail.label(text=f"File {_sub['file_idx']} of {_sub['file_total']}: {_sub['file_name'] or ''}")
        fp = max(0.0, min(1.0, _sub["file_progress"]))
        _progress_bar(detail, fp)

    layout.separator(factor=0.6)
    _progress_bar(layout, max(0.0, min(1.0, _sub["step_progress"])))

    layout.separator(factor=0.6)
    log_box = layout.box(); log_col = log_box.column(align=True); log_col.scale_y = 0.85
    for line in _sub["log"][-4:]:
        log_col.label(text=line)

    layout.separator(factor=0.5)
    layout.operator("rf.cancel_submission", text="Cancel", icon="CANCEL")


def _draw_conductor_progress(layout):
    """
    Conductor-style 3-bar progress view for renderfarm submissions.
      Bar 1 (purple icon) — Jobs Progress    overall job preparation
      Bar 2 (blue icon)   — Computing MD5    parallel hash computation
      Bar 3 (green icon)  — File Upload      parallel byte upload
    Below: per-file rows with md5/upload status pills.
    """
    blend_fp   = bpy.data.filepath or "untitled.blend"
    scene_name = os.path.splitext(os.path.basename(blend_fp))[0]
    v          = bpy.app.version
    blender_str = f"Blender {v[0]}.{v[1]}.{v[2]}"

    md5_total  = max(1, _sub.get("md5_total", 1))
    md5_done   = _sub.get("md5_done", 0)
    ubt        = _sub.get("upload_bytes_total", 0)
    ubs        = _sub.get("upload_bytes_sent",  0)
    jp         = max(0.0, min(1.0, _sub.get("job_progress", 0.0)))
    md5_frac   = md5_done / md5_total
    up_frac    = (ubs / ubt) if ubt > 0 else 0.0
    up_frac    = max(0.0, min(1.0, up_frac))

    def _bar(lyt, frac, label, pct_str, color_icon):
        box = lyt.box()
        hdr = box.row()
        # Colored square icon as visual color indicator
        hdr.label(text="", icon=color_icon)
        hdr.label(text=label)
        right = hdr.row(); right.alignment = "RIGHT"
        right.label(text=pct_str)
        _progress_bar(box, frac)

    # Bar 1 — Jobs Progress (SEQUENCE_COLOR_05 ≈ violet/purple)
    _bar(layout, jp,
         f"Jobs Progress: 1/1 – {blender_str} Linux Render {scene_name}",
         f"{jp * 100:.1f}%",
         "SEQUENCE_COLOR_05")

    # Bar 2 — Computing MD5 (SEQUENCE_COLOR_04 ≈ blue)
    _bar(layout, md5_frac,
         f"Computing MD5: {md5_done}/{md5_total}",
         f"{md5_frac * 100:.1f}%",
         "SEQUENCE_COLOR_04")

    # Bar 3 — File Upload (SEQUENCE_COLOR_03 ≈ green)
    _bar(layout, up_frac,
         "File Upload",
         f"{up_frac * 100:.1f}%",
         "SEQUENCE_COLOR_03")

    # ── Per-file list ─────────────────────────────────────────────────────────
    files = _sub.get("files", [])
    if files:
        layout.separator(factor=0.4)
        scroll = layout.box()
        col    = scroll.column(align=True)
        col.scale_y = 0.88
        for fs in files:
            row  = col.row(align=True)
            ext  = os.path.splitext(fs.get("name", ""))[1].lower()
            icon = "FILE_BLEND" if ext == ".blend" else (
                   "IMAGE_DATA" if ext in (".png", ".jpg", ".jpeg", ".exr", ".hdr", ".tiff", ".tga") else
                   "FILE_BLANK")
            row.label(text=fs.get("path", fs.get("name", "")), icon=icon)
            pill = row.row(align=True)
            pill.alignment = "RIGHT"
            if fs.get("skipped"):
                pill.label(text="Cached", icon="MODIFIER_DATA")
            elif fs.get("upload_done"):
                pill.label(text="  100%", icon="SEQUENCE_COLOR_03")   # green pill
            elif fs.get("md5_done"):
                # Uploading — show live byte percentage
                fsz = fs.get("size", 1) or 1
                pct = int(100 * fs.get("upload_bytes", 0) / fsz)
                pill.label(text=f"  {pct}%", icon="SEQUENCE_COLOR_04")  # blue pill
            else:
                pill.label(text="  —", icon="TEMP")

    layout.separator(factor=0.5)
    layout.operator("rf.cancel_submission", text="Cancel", icon="CANCEL")


def _draw_submitting(layout):
    """Dispatcher: GCP uses the simple 2-step view; renderfarm uses Conductor-style 3-bar view."""
    if _sub.get("gcp_mode"):
        _draw_gcp_submitting(layout)
    else:
        _draw_conductor_progress(layout)


def _draw_complete(layout):
    """
    Conductor-style Response tab — success.
    Single job-submitted line (top-left) + 'Go to dashboard' button (top-right).
    The rest of the content area is empty. Footer 'Close' is the dialog's own button.
    """
    job_num_raw = _sub.get("job_number", "0")
    try:
        job_padded = str(int(job_num_raw)).zfill(5)
    except (ValueError, TypeError):
        job_padded = str(job_num_raw).zfill(5)

    blender_ver = _sub.get("blender_ver_str", "")
    if not blender_ver:
        _v = bpy.app.version
        blender_ver = f"Blender {_v[0]}.{_v[1]}.{_v[2]}"

    scene_name = os.path.splitext(
        os.path.basename(bpy.data.filepath or "untitled.blend")
    )[0]

    job_line = f"Job submitted – {blender_ver} Linux Render {scene_name} ({job_padded})"

    # ── Content area ──────────────────────────────────────────────────────────
    box = layout.box()
    row = box.row(align=False)
    # Job line — left aligned, no icon
    row.label(text=job_line)
    # 'Go to dashboard' — right aligned, outlined look via operator button
    right = row.row()
    right.alignment = "RIGHT"
    right.operator("rf.open_dashboard", text="Go to dashboard")

    # Empty space to fill the content area (mirrors the real Conductor dialog)
    box.separator(factor=14.0)


def _draw_error(layout):
    """
    Conductor-style Response tab — failure.
    Single red 'Submission failed' line. No dashboard button. No retry button.
    """
    err_text = _sub.get("error", "Unknown error")
    # Trim to one line for the label; full text available in Blender's console
    first_line = err_text.split("\n")[0][:200]

    box = layout.box()
    row = box.row()
    row.alert = True
    row.label(text=f"Submission failed – {first_line}")

    # Empty space to match dialog height
    box.separator(factor=14.0)


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

    active_tab: bpy.props.EnumProperty(
        items=[
            ("validation", "Validation", "Pre-submission checks and asset scan"),
            ("progress",   "Progress",   "Submission progress and upload status"),
            ("response",   "Response",   "Submission result and job details"),
        ],
        default="validation",
    )

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
            _sub["cancel"]         = False
            _sub["gcp_mode"]       = False
            _sub["frame_spec"]     = ""
            _sub["frame_count"]    = ""
            _sub["task_count"]     = ""
            _sub["scout_task_count"] = ""
            _sub["blender_ver_str"]  = ""
            # Conductor 3-bar fields
            _sub["sub_status"]         = "idle"
            _sub["md5_total"]          = 0
            _sub["md5_done"]           = 0
            _sub["upload_total"]       = 0
            _sub["upload_done"]        = 0
            _sub["upload_bytes_total"] = 0
            _sub["upload_bytes_sent"]  = 0
            _sub["job_progress"]       = 0.0
            _sub["files"]              = []

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
        if scene.rf_project in ("none", "", None):
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

        # Snapshot frame info so the Response tab can display it after submission
        _sub["frame_spec"]       = getattr(scene, "rf_frame_spec",        "") or getattr(scene, "rf_frame_range", "")
        _sub["frame_count"]      = getattr(scene, "rf_frame_count",       "")
        _sub["task_count"]       = getattr(scene, "rf_task_count",        "")
        _sub["scout_task_count"] = getattr(scene, "rf_scout_task_count",  "")
        # Snapshot Blender version string for the Response tab job line
        bv = getattr(scene, "rf_blender_version", "").strip()
        if not bv:
            _v = bpy.app.version
            bv = f"{_v[0]}.{_v[1]}.{_v[2]}"
        if not bv.lower().startswith("blender"):
            bv = f"Blender {bv}"
        _sub["blender_ver_str"] = bv

        try:
            return context.window_manager.invoke_props_dialog(
                self, width=700, confirm_text="Close")
        except TypeError:
            return context.window_manager.invoke_props_dialog(self, width=700)

    def draw(self, context):
        layout = self.layout
        state  = _sub["state"]

        # Auto-advance tab to match submission state
        if state == "SUBMITTING":
            self.active_tab = "progress"
        elif state in ("COMPLETE", "ERROR"):
            self.active_tab = "response"

        # ── 3-tab bar ─────────────────────────────────────────────────────────
        row = layout.row()
        row.prop_tabs_enum(self, "active_tab")
        layout.separator(factor=0.5)

        # ── Tab content ───────────────────────────────────────────────────────
        if self.active_tab == "validation":
            _draw_validation(layout)
        elif self.active_tab == "progress":
            _draw_submitting(layout)
        elif self.active_tab == "response":
            if state == "ERROR":
                _draw_error(layout)
            elif state == "COMPLETE":
                _draw_complete(layout)
            else:
                col = layout.column(align=True)
                col.scale_y = 1.2
                col.label(text="Waiting for submission to complete…", icon="RENDER_ANIMATION")
                col.label(text="Switch to the Progress tab to monitor.", icon="INFO")

    def execute(self, context):
        # Called when user clicks the built-in "Close" button
        return {"FINISHED"}

    def check(self, context):
        # Always return True so tab switches and state changes both trigger a redraw
        return True


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
            try:
                padded = str(int(job_number)).zfill(5)
            except (ValueError, TypeError):
                padded = str(job_number)
            url = f"{WEB_BASE}/jobs/{padded}"
        else:
            url = WEB_BASE
        try:
            bpy.ops.wm.url_open(url=url)
        except Exception:
            webbrowser.open(url)
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
        row.prop(scene, "rf_provider", text="")
        layout.separator()

        # ── Project guard — block submission when no active project exists ───
        has_project = scene.rf_project not in ("none", "", None)
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
        if scene.rf_preemptible:
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
        if scene.rf_use_scout_frames:
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
        fields = [
            ("rf_frame_spec",        "Frame Spec"),
            ("rf_frame_count",       "Frame Count"),
            ("rf_task_count",        "Task Count"),
        ]
        if scene.rf_use_scout_frames:
            fields += [
                ("rf_scout_spec",        "Scout Spec"),
                ("rf_scout_frame_count", "Scout Frame Count"),
                ("rf_scout_task_count",  "Scout Task Count"),
            ]
        for prop, label in fields:
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
    S.rf_machine_type          = bpy.props.EnumProperty(name="Machine Type", items=_FALLBACK_MACHINE_TYPES["GPU"])
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
    S.rf_use_scout_frames      = bpy.props.BoolProperty(name="Use Scout Frames", default=True, update=_on_scout_updated)
    S.rf_scout_frames          = bpy.props.StringProperty(name="Scout Frames", default="fml:3", update=_on_scout_updated)
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
    S.rf_frame_spec            = bpy.props.StringProperty(name="Frame Spec",        default="")
    S.rf_scout_spec            = bpy.props.StringProperty(name="Scout Spec",        default="")
    S.rf_frame_count           = bpy.props.StringProperty(name="Frame Count",       default="")
    S.rf_task_count            = bpy.props.StringProperty(name="Task Count",        default="")
    S.rf_scout_frame_count     = bpy.props.StringProperty(name="Scout Frame Count", default="")
    S.rf_scout_task_count      = bpy.props.StringProperty(name="Scout Task Count",  default="")
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
        "rf_frame_spec","rf_scout_spec","rf_frame_count","rf_task_count",
        "rf_scout_frame_count","rf_scout_task_count","rf_output_folder","rf_disable_audio",
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
