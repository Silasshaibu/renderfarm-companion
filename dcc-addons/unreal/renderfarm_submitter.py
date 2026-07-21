"""
Renderfarm Unreal Engine Submitter v1.0.0
Submit Unreal Engine Movie Render Queue (MRQ) jobs to Renderfarm directly
from inside the Unreal Editor's Python console.

Installation:
  1. Copy this file anywhere on disk.
  2. In Unreal: Window -> Developer Tools -> Output Log -> Cmd (or Window ->
     Developer Tools -> Python console, on engine versions that have one):
       import sys; sys.path.insert(0, r"/path/to/folder"); import renderfarm_submitter; renderfarm_submitter.show()
  3. Or add a call to the two lines above in an editor startup script /
     menu-extension Python file, or bind it to a toolbar button via the
     Unreal Python "Scripts" menu / Editor Utility Widget.

Requirements: Unreal Engine 5.0+ with the Python Editor Script Plugin
enabled (Edit -> Plugins -> "Python Editor Script Plugin"), and either
PySide2 or PySide6 importable from Unreal's embedded Python interpreter
(you may need to `pip install PySide6` into Unreal's Python -- see Epic's
docs on using Unreal's bundled Python / site-packages).

======================================================================
WHY THIS ADDON LOOKS DIFFERENT FROM MAYA / HOUDINI / NUKE / KATANA
======================================================================
Every other DCC addon in this project reads ONE portable scene file (a
.ma/.mb, .hip, .nk, or .katana) plus a set of individually-referenced
external files (textures, caches, etc.), computes a SHA-256 per external
file, and uploads/dedups each one independently (see those addons'
`_scan_dependencies()` + `assets[]` manifest shape).

Unreal has no equivalent single "scene file". A render depends on the
*entire* .uproject + its Content/ folder -- maps, Level Sequences, MRQ
presets, materials, blueprints, and every asset they reference -- which is
typically enormous and deeply cross-referenced through the engine's own
binary asset registry, not plain external file paths. Faithfully doing
per-asset dependency scanning + dedup for that (the way the other addons
do) is impractical within reasonable scope for this addon.

So instead of the assets[]-array v7 manifest convention, this addon uses
the SIMPLER "whole project zip" approach, in the spirit of the render
worker's *legacy v6* zip-based flow (see worker/renderfarm_worker.py's
prepare_scene_v6(), which already knows how to download+extract a zip
containing a scene file -- read for reference only, not modified here):
we zip up the project's `Project.uproject` file plus its `Content/` and
`Config/` folders into a single archive, SHA-256 hash *that zip* (so a
resubmission of an unchanged project dedups against the previous upload),
and upload it through the same token/PUT/confirm blob flow the other
addons use for individual assets.

See the `manifest` dict built in SubmitWorker.run() below, and the large
comment directly above it, for the resulting manifest shape -- it is
intentionally NOT an assets[]-array like the other addons produce, and
whoever wires the worker side of this up will need a dedicated code path
(unzip `project_zip_url` into a temp dir, then invoke UnrealEditor-Cmd.exe)
rather than the generic prepare_scene_v7() per-asset loop.

======================================================================
HOW THE ACTUAL RENDER IS INVOKED (NOT by this script)
======================================================================
Unlike Maya/Houdini/Nuke, where the render worker runs the DCC's own batch
renderer against the scene file, Unreal's Movie Render Queue is invoked as
a *separate command-line process*, `UnrealEditor-Cmd.exe`, completely
outside of any running Editor Python session. This addon's job is only to
package the project and record which map / Level Sequence / MRQ preset to
use -- NOT to perform the render itself. The verified invocation (per
Epic's own official documentation) the render worker should use is:

    UnrealEditor-Cmd.exe "C:\\path\\to\\Project.uproject" MapName -game ^
        -LevelSequence="/Game/Cinematics/MySequence.MySequence" ^
        -MoviePipelineConfig="/Game/Cinematics/MoviePipeline/Presets/MyPreset.MyPreset" ^
        -windowed -resx=1920 -resy=1080 -log -notexturestreaming ^
        -Unattended -renderoffscreen

Where:
  - "Project.uproject"     -- filesystem path to the project file (after
                              the worker has unzipped project_zip_url).
  - MapName                -- just the short map/level name (NOT an
                              in-project asset path), per Epic's example.
  - -game                  -- required for MRQ command-line rendering.
  - -LevelSequence=        -- in-project asset path of the Sequencer asset
                              to render (job manifest's "level_sequence").
  - -MoviePipelineConfig=  -- in-project asset path of the MRQ preset asset
                              (job manifest's "moviepipeline_config"). This
                              preset is where output directory, format,
                              frame range, resolution overrides, renderer/
                              RHI settings and anti-aliasing are ALL
                              configured by the artist, ahead of time,
                              inside the Unreal Editor UI -- they are NOT
                              overridable from this basic command line, and
                              this addon does not attempt to fake that; it
                              only lets the artist pick which already-saved
                              preset asset to use.
  - -Unattended -renderoffscreen -log -notexturestreaming
                           -- headless/automation flags.
  - -windowed -resx=... -resy=...
                           -- still required even when rendering offscreen,
                              per Epic's own documented example.

We deliberately do NOT build the `unreal.MoviePipelinePythonHostExecutor`
custom-Python-executor pattern here -- that requires bespoke code to
already be installed *inside the target project being rendered*, which is
too invasive to require of every artist submitting a job. The command-line
Level Sequence + MoviePipelineConfig method above requires zero custom
code in the target project.

Changelog:
  v1.0.0 - Initial release. Whole-project-zip submission (not the v7
           per-asset manifest convention used by the other addons); Map,
           Level Sequence, and MoviePipelineConfig preset are selected via
           Asset-Registry-backed combo boxes (editable, so the artist can
           still type a path by hand if scanning comes up empty on a given
           engine version -- see the API-stability notes on the scanning
           helpers below).
"""

import os
import sys
import json
import hashlib
import tempfile
import threading
import webbrowser
import urllib.request
import urllib.parse
import urllib.error
import http.server

# ── Unreal imports (available inside the Unreal Editor's embedded Python) ────
try:
    import unreal
    _IN_UNREAL = True
except ImportError:
    _IN_UNREAL = False

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from PySide2.QtCore import Qt, Signal, QThread
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import Qt, Signal, QThread

# ── Config ───────────────────────────────────────────────────────────────────
API_BASE      = "https://renderfarm-web.vercel.app/api"
WEB_BASE      = "https://renderfarm-web.vercel.app"
CALLBACK_PORT = 8989
_TOKEN_FILE   = os.path.join(os.path.expanduser("~"), ".rf_token")

# Engine-version choices for the "Engine Version" dropdown. Best-effort
# auto-detected/preselected from unreal.SystemLibrary.get_engine_version()
# when running inside Unreal (see _detect_engine_version()); manual override
# is always available since this is a plain combo box.
UNREAL_VERSIONS = [
    ("unreal-5.5", "Unreal Engine 5.5"),
    ("unreal-5.4", "Unreal Engine 5.4"),
    ("unreal-5.3", "Unreal Engine 5.3"),
    ("unreal-5.2", "Unreal Engine 5.2"),
    ("unreal-5.1", "Unreal Engine 5.1"),
    ("unreal-5.0", "Unreal Engine 5.0"),
    ("unreal-4.27", "Unreal Engine 4.27"),
]

_FALLBACK_MACHINE_TYPES = {
    "GPU": [("t4-1", "T4 16GB · 4 vCPU · 15 GB")],
    "CPU": [("n1-4", "CPU · 4 vCPU · 15 GB")],
}

# Project folders that get zipped up alongside Project.uproject. This is
# the "at minimum" set needed for a render to be reproducible on another
# machine -- Content/ holds the maps/sequences/assets, Config/ holds the
# project's DefaultEngine.ini/DefaultGame.ini/etc. which affect rendering
# (RHI, plugins enabled, etc.). Projects that rely on custom C++ modules or
# third-party Plugins/ content are a known gap (not zipped) -- flagged here
# rather than silently mishandled; extend PROJECT_ZIP_FOLDERS if your farm
# needs that.
PROJECT_ZIP_FOLDERS = ["Content", "Config"]

# ── Token helpers (identical pattern to the other DCC addons) ────────────────
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

# ── HTTP helpers ──────────────────────────────────────────────────────────────
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
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def _patch(path, payload, token):
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers, method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def _put_blob(url, filepath, on_progress=None):
    size = os.path.getsize(filepath)
    chunk = 65536
    uploaded = 0
    with open(filepath, "rb") as fh:
        conn_url = urllib.parse.urlparse(url)
        import http.client as hc
        conn = hc.HTTPSConnection(conn_url.netloc, timeout=120)
        conn.connect()
        conn.putrequest("PUT", conn_url.path + (f"?{conn_url.query}" if conn_url.query else ""))
        conn.putheader("Content-Length", str(size))
        conn.putheader("Content-Type", "application/octet-stream")
        conn.endheaders()
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            conn.send(buf)
            uploaded += len(buf)
            if on_progress:
                on_progress(uploaded, size)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        if resp.status not in (200, 201):
            raise RuntimeError(f"Blob upload failed: {resp.status}")

# ── Browser auth (identical pattern to the other DCC addons) ─────────────────
class _AuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, callback=None, **kwargs):
        self._callback = callback
        super().__init__(*args, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        token  = params.get("token", [None])[0]
        email  = params.get("email",  [None])[0]
        if parsed.path == "/callback" and token and email:
            html = (
                b"<!doctype html><html><head><meta charset='utf-8'><title>Renderfarm</title>"
                b"<style>body{margin:0;min-height:100vh;display:flex;align-items:center;"
                b"justify-content:center;background:#0d0d1a;font-family:system-ui;color:#fff}"
                b".card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:16px;"
                b"padding:48px;text-align:center}</style></head>"
                b"<body><div class='card'><h2 style='color:#22d3ee'>&#10003; Unreal Connected</h2>"
                b"<p>You can close this tab and return to Unreal Editor.</p></div></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)
            if self._callback:
                self._callback(token, email)
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, *_):
        pass


def _start_browser_login(on_success):
    """Opens system browser to renderfarm login; on_success(token, email) called on receipt."""
    result = {}

    def handler_factory(*args, **kwargs):
        return _AuthCallbackHandler(*args, callback=lambda t, e: result.update({"token": t, "email": e}), **kwargs)

    server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), handler_factory)
    webbrowser.open(f"{WEB_BASE}/login?port={CALLBACK_PORT}")

    def _serve():
        while not result:
            server.handle_request()
        server.server_close()
        on_success(result["token"], result["email"])

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

# ── SHA-256 hashing ───────────────────────────────────────────────────────────
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

# ── Machine types ─────────────────────────────────────────────────────────────
def _fetch_machine_types():
    try:
        rows = _get("/machine-types", token="")
        result = {"GPU": [], "CPU": []}
        for row in rows:
            inst = row.get("instance", "GPU")
            result.setdefault(inst, []).append((row["id"], row["label"]))
        for k in ("GPU", "CPU"):
            if not result.get(k):
                result[k] = _FALLBACK_MACHINE_TYPES[k]
        return result
    except Exception:
        return dict(_FALLBACK_MACHINE_TYPES)

# ══════════════════════════════════════════════════════════════════════════
# Unreal introspection helpers
#
# NOTE ON API STABILITY: Epic has moved several editor-scripting APIs across
# Unreal 5.x releases (EditorLevelLibrary -> subsystems; AssetData.object_path
# -> get_soft_object_path(); get_assets_by_class() signature changes for
# TopLevelAssetPath, etc). Every helper below is written defensively (tries
# the modern call, falls back to older ones, then gives up quietly) and is
# NOT independently verified against every 5.x point release in a live
# Editor session. Anywhere scanning comes up empty, the corresponding UI
# combo box is editable so the artist can type the asset path by hand
# instead of being blocked.
# ══════════════════════════════════════════════════════════════════════════

def _detect_engine_version():
    """Best-effort match of the running engine version to an UNREAL_VERSIONS
    entry, for preselecting the Engine Version combo. Falls back to the
    newest entry if detection fails or we're not running inside Unreal.
    unreal.SystemLibrary.get_engine_version() has been stable since early
    UE4 Python-plugin days (returns e.g. "5.3.2-XXXXXXX+++UE5+Release-5.3"),
    so this one is lower-risk than the asset-registry helpers below, but
    the exact string format has not been re-verified against 5.5/latest."""
    if not _IN_UNREAL:
        return UNREAL_VERSIONS[0][0]
    try:
        raw = unreal.SystemLibrary.get_engine_version()  # e.g. "5.3.2-XXXX+++UE5+Release-5.3"
        import re
        m = re.match(r"(\d+)\.(\d+)", raw)
        if m:
            major, minor = m.group(1), m.group(2)
            candidate = f"unreal-{major}.{minor}"
            for val, _label in UNREAL_VERSIONS:
                if val == candidate:
                    return val
    except Exception:
        pass
    return UNREAL_VERSIONS[0][0]


def _get_project_file_path():
    """Auto-detect the open project's .uproject path via
    unreal.Paths.get_project_file_path(). This one is a long-standing,
    stable part of the Python API. Returns "" if not in Unreal or on error
    -- the Project field always allows manual entry/browsing regardless."""
    if not _IN_UNREAL:
        return ""
    try:
        raw = unreal.Paths.get_project_file_path()
        return os.path.normpath(unreal.Paths.convert_relative_path_to_full(raw))
    except Exception:
        return ""


def _asset_registry():
    if not _IN_UNREAL:
        return None
    try:
        return unreal.AssetRegistryHelpers.get_asset_registry()
    except Exception:
        return None


def _asset_object_path(asset_data):
    """Return the '/Game/Path/AssetName.AssetName' object-path string for an
    unreal.AssetData, tolerating the property rename across 5.x:
      - UE 5.1+: AssetData.object_path (FName) was deprecated in favor of
        AssetData.get_soft_object_path() (FSoftObjectPath), part of the
        World Partition / external-actors path-handling changes.
      - UE 5.0 and earlier: .object_path held this directly.
    Tries the modern accessor first, falls back to the legacy property,
    then reconstructs from package_name + asset_name as a last resort.
    NOT independently verified against every 5.x point release."""
    try:
        return str(asset_data.get_soft_object_path().to_string())
    except Exception:
        pass
    try:
        op = asset_data.object_path
        if op:
            return str(op)
    except Exception:
        pass
    try:
        return f"{asset_data.package_name}.{asset_data.asset_name}"
    except Exception:
        return ""


def _short_asset_name(object_path):
    """'/Game/Cinematics/MySequence.MySequence' -> 'MySequence'"""
    if not object_path:
        return ""
    tail = object_path.rsplit(".", 1)[-1]
    return tail.rsplit("/", 1)[-1]


def _get_assets_by_class_name(short_class_name, module_path):
    """Query the Asset Registry for all assets of a given class, tolerating
    the get_assets_by_class() signature change across 5.x:
      - UE 5.1+: expects an unreal.TopLevelAssetPath(module_path, short_class_name).
      - UE 5.0 and earlier: expects a plain unreal.Name(short_class_name).
    module_path is the class's native module, e.g. "/Script/LevelSequence"
    for LevelSequence, "/Script/MovieRenderPipelineCore" for the MRQ config
    classes, "/Script/Engine" for World (maps/levels).
    NOT independently verified against every 5.x point release -- if
    scanning unexpectedly returns [], check
    unreal.AssetRegistryHelpers.get_asset_registry().get_assets_by_class.__doc__
    in the Python console for your specific engine build."""
    registry = _asset_registry()
    if registry is None:
        return []
    try:
        class_path = unreal.TopLevelAssetPath(module_path, short_class_name)
        return list(registry.get_assets_by_class(class_path, search_sub_classes=True))
    except Exception:
        pass
    try:
        return list(registry.get_assets_by_class(unreal.Name(short_class_name), True))
    except Exception:
        return []


def _scan_maps():
    """All Level/World assets in the project, for the Map combo. Maps are
    stored as 'World' assets in the registry."""
    result = {}
    for a in _get_assets_by_class_name("World", "/Script/Engine"):
        p = _asset_object_path(a)
        if p:
            result[p] = p
    return sorted(result.items(), key=lambda t: t[1])


def _scan_level_sequences():
    """All unreal.LevelSequence assets in the project, for the Level
    Sequence combo -- same 'which output to render' gap as Houdini's ROP
    picker / Nuke's Write-node picker: Unreal has no single 'active
    sequence' concept, a project can contain any number of them."""
    result = {}
    for a in _get_assets_by_class_name("LevelSequence", "/Script/LevelSequence"):
        p = _asset_object_path(a)
        if p:
            result[p] = p
    return sorted(result.items(), key=lambda t: t[1])


def _scan_moviepipeline_configs():
    """All saved MoviePipelineConfig preset assets in the project. Epic
    renamed the base config class from 'MoviePipelineMasterConfig' (5.0)
    to 'MoviePipelinePrimaryConfig' (5.1+) -- we scan for both names to be
    safe across engine versions."""
    result = {}
    for cls in ("MoviePipelinePrimaryConfig", "MoviePipelineMasterConfig"):
        for a in _get_assets_by_class_name(cls, "/Script/MovieRenderPipelineCore"):
            p = _asset_object_path(a)
            if p:
                result[p] = p
    return sorted(result.items(), key=lambda t: t[1])


def _get_current_map_object_path():
    """Best-effort detection of the currently open level, to preselect the
    Map combo. Epic's 'get the open editor world' API moved across 5.x:
      - unreal.UnrealEditorSubsystem().get_editor_world() -- the current
        subsystem-based path.
      - unreal.EditorLevelLibrary.get_editor_world() -- older call, marked
        deprecated in 5.1+ but often still functional via a shim.
    NOT independently verified against a live Unreal 5.x install. If both
    fail, the Map field simply starts blank/unselected -- pick manually."""
    if not _IN_UNREAL:
        return ""
    world = None
    try:
        subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        world = subsystem.get_editor_world()
    except Exception:
        try:
            world = unreal.EditorLevelLibrary.get_editor_world()
        except Exception:
            world = None
    if world is None:
        return ""
    try:
        return str(world.get_path_name()).split(":")[0]
    except Exception:
        return ""


def _get_sequence_playback_range_str(level_sequence_path):
    """Best-effort, DISPLAY/BOOKKEEPING-ONLY frame range for the selected
    Level Sequence -- used only to populate the job's 'frames' field for
    dashboard/task-row bookkeeping. This has NO effect on the actual
    render: the real output frame range is controlled entirely by the
    settings baked into the MoviePipelineConfig preset (see the module
    docstring), which this addon cannot generically read or override.
    Returns a "start-end" string, or "1-1" if it can't be determined."""
    if not _IN_UNREAL or not level_sequence_path:
        return "1-1"
    try:
        seq = unreal.load_asset(level_sequence_path)
        if seq is None:
            return "1-1"
        rng = seq.get_playback_range()
        start = int(rng.get_editor_property("inclusive_start"))
        end   = int(rng.get_editor_property("exclusive_end")) - 1
        return f"{start}-{max(start, end)}"
    except Exception:
        pass
    try:
        start = int(seq.get_playback_start())
        end   = int(seq.get_playback_end())
        return f"{start}-{max(start, end)}"
    except Exception:
        return "1-1"


# ── Project zip packaging ─────────────────────────────────────────────────────
def _build_project_zip(uproject_path, on_progress=None):
    """
    Zip the whole project (Project.uproject + Content/ + Config/, see
    PROJECT_ZIP_FOLDERS) into a single archive for upload. Unreal has no
    single portable "scene file" the other addons could scan file-by-file,
    so we package and dedup the whole project instead -- see the module
    docstring for the full rationale.

    Returns the path to a temp .zip file. Caller is responsible for
    deleting it after upload.
    """
    import zipfile

    project_dir = os.path.dirname(uproject_path)
    fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="rf_unreal_project_")
    os.close(fd)

    files_to_zip = [uproject_path]
    for folder_name in PROJECT_ZIP_FOLDERS:
        folder_path = os.path.join(project_dir, folder_name)
        if not os.path.isdir(folder_path):
            continue
        for dirpath, _dirnames, filenames in os.walk(folder_path):
            for fn in filenames:
                files_to_zip.append(os.path.join(dirpath, fn))

    total = len(files_to_zip)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, fpath in enumerate(files_to_zip, 1):
                arcname = os.path.relpath(fpath, project_dir)
                try:
                    zf.write(fpath, arcname)
                except Exception:
                    pass  # skip unreadable/locked files rather than aborting the whole zip
                if on_progress:
                    on_progress(i, total)
    except Exception:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        raise
    return zip_path


# ── Submission worker ─────────────────────────────────────────────────────────
class SubmitWorker(QThread):
    progress = Signal(str)
    finished = Signal(str)   # job number
    error    = Signal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def run(self):
        p = self.params
        token = p["token"]
        zip_path = None
        try:
            # 1. Package the whole project into a single zip.
            self.progress.emit("Packaging project (Content/ + Config/)…")

            def _zip_progress(i, total):
                if total:
                    self.progress.emit(f"Zipping project… {i}/{total} files")

            zip_path = _build_project_zip(p["uproject_path"], on_progress=_zip_progress)
            zip_size = os.path.getsize(zip_path)

            # 2. Hash the zip itself (not individual assets) -- this is what
            # makes resubmitting an unchanged project a no-op re-upload.
            self.progress.emit("Hashing project zip…")
            zip_sha256 = _sha256(zip_path)

            # 3. Preflight (kept for parity with the other addons' dedup
            # flow / any farm-wide stats it feeds; the definitive dedup
            # check is the /assets "token" call below, which reports
            # exists=True itself if this exact sha256 was already uploaded).
            self.progress.emit("Running preflight check…")
            try:
                _post("/jobs/preflight", {"assets": [{"sha256": zip_sha256}]}, token)
            except Exception:
                pass

            # 4. Create job.
            self.progress.emit("Creating job…")
            job_payload = {
                "title":          p["title"],
                # Bookkeeping only -- NOT sent to UnrealEditor-Cmd.exe. The
                # real output frame range lives inside the selected
                # MoviePipelineConfig preset asset (see module docstring).
                "frames":         p["frames_display"],
                # Specific engine build, e.g. "unreal-5.4" -- kept distinct
                # from manifest["software"], which is always the literal
                # "unreal" per the fixed manifest shape (see comment below).
                "software":       p["engine_version"],
                "provider":       "renderfarm",
                "project_id":     p["project_id"],
                "chunk_size":     p["chunk_size"],
                "machine_type":   p["machine_type"],
                "preemptible":    p["preemptible"],
                "assets_total":   1,
                "assets_uploaded": 0,
                "status":         "uploading",
                "render_settings": {
                    "engine_version":       p["engine_version"],
                    "machine_type":         p["machine_type"],
                    "instance_type":        p["instance_type"],
                    "chunk_size":           p["chunk_size"],
                    "map_name":             p["map_name"],
                    "level_sequence":       p["level_sequence"],
                    "moviepipeline_config": p["moviepipeline_config"],
                },
            }
            resp = _post("/jobs", job_payload, token)
            job_id     = resp["id"]
            job_number = resp["jobNumber"]

            # 5. Upload the project zip through the existing token/PUT/confirm
            # blob flow (same endpoints the per-asset addons use for each of
            # their files -- here there's just the one "asset": the zip).
            tok_resp = _post("/assets", {
                "action":     "token",
                "sha256":     zip_sha256,
                "filename":   f"{os.path.splitext(os.path.basename(p['uproject_path']))[0]}.zip",
                "size_bytes": zip_size,
            }, token)

            if tok_resp.get("exists"):
                zip_url = tok_resp.get("url", "")
                self.progress.emit("Project zip already on farm — skipping upload (dedup).")
            else:
                def _upload_progress(uploaded, size):
                    pct = int(uploaded * 100 / size) if size else 0
                    self.progress.emit(f"Uploading project zip… {pct}%  ({uploaded // (1024*1024)} / {size // (1024*1024)} MB)")

                _put_blob(tok_resp["uploadUrl"], zip_path, on_progress=_upload_progress)
                conf = _post("/assets", {
                    "action":      "confirm",
                    "clientToken": tok_resp["clientToken"],
                }, token)
                zip_url = conf.get("url", "")

            # 6. Finalize.
            #
            # ══════════════════════════════════════════════════════════════
            # MANIFEST SHAPE WARNING -- read before wiring up the worker side:
            #
            # This manifest is DELIBERATELY NOT the assets[]-array shape used
            # by the v7 per-asset addons (Maya/Houdini/Nuke/Katana/3ds Max/
            # Cinema4D). Those addons list every individually-hashed external
            # file under manifest["assets"], each with its own blob_url, and
            # the worker's generic prepare_scene_v7() loop downloads them one
            # by one. Unreal has no practical per-asset equivalent (see the
            # module docstring), so this manifest instead carries ONE zip
            # covering the whole project:
            #
            #   project_zip_sha256 / project_zip_url  -- the single archive.
            #   map_name / level_sequence / moviepipeline_config
            #                                          -- what to render,
            #                                             as UnrealEditor-Cmd.exe
            #                                             command-line args.
            #
            # Whoever adds Unreal support to worker/renderfarm_worker.py
            # will need a DEDICATED code path (something like
            # prepare_scene_unreal()) that:
            #   1. Downloads project_zip_url, verifies against
            #      project_zip_sha256, and unzips it into a temp work dir.
            #   2. Locates the extracted "*.uproject" file.
            #   3. Invokes UnrealEditor-Cmd.exe with -game, MapName,
            #      -LevelSequence=<level_sequence>,
            #      -MoviePipelineConfig=<moviepipeline_config>, and the
            #      standard -windowed -resx= -resy= -log -notexturestreaming
            #      -Unattended -renderoffscreen flags (see module docstring
            #      for the full verified command line).
            # This does NOT slot into prepare_scene_v6() or
            # prepare_scene_v7() as-is -- both assume a single scene *file*
            # comes out the other end, not a project directory + separate
            # command-line render invocation.
            # ══════════════════════════════════════════════════════════════
            self.progress.emit("Finalising job…")
            manifest = {
                "scene":                os.path.basename(p["uproject_path"]),  # display parity with other addons
                "software":             "unreal",   # fixed literal -- see note above re: render_settings.engine_version
                "renderer":             "unreal",   # fixed literal -- MRQ preset encodes the actual renderer/RHI settings
                "instance_type":        p["instance_type"],
                "machine_type":         p["machine_type"],
                "chunk_size":           p["chunk_size"],
                "engine_version":       p["engine_version"],
                "project_zip_sha256":   zip_sha256,
                "project_zip_url":      zip_url,
                "map_name":             p["map_name"],
                "level_sequence":       p["level_sequence"],
                "moviepipeline_config": p["moviepipeline_config"],
            }
            _patch(f"/jobs?id={job_id}", {
                "status":          "pending",
                "manifest":        manifest,
                "assets_uploaded": 1,
            }, token)

            self.finished.emit(job_number)

        except Exception as e:
            self.error.emit(str(e))
        finally:
            if zip_path and os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except Exception:
                    pass

# ── Main UI ───────────────────────────────────────────────────────────────────
class RenderfarmSubmitter(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Renderfarm Submitter — Unreal Engine")
        self.setMinimumWidth(560)
        self.setStyleSheet("""
            QDialog, QWidget { background:#12121c; color:#e2e8f0; font-family:'Segoe UI',system-ui; }
            QLabel  { color:#e2e8f0; }
            QLineEdit, QComboBox, QSpinBox {
                background:#1e1e2e; border:1px solid #2a2a4a; border-radius:6px;
                padding:6px 10px; color:#e2e8f0; }
            QPushButton {
                background:#0ea5e9; color:#fff; border:none; border-radius:6px;
                padding:8px 18px; font-weight:600; }
            QPushButton:hover  { background:#38bdf8; }
            QPushButton:disabled { background:#2a2a4a; color:#666; }
            QPushButton#danger { background:#dc2626; }
            QPushButton#danger:hover { background:#ef4444; }
            QPushButton#small { padding:6px 10px; }
            QGroupBox { border:1px solid #2a2a4a; border-radius:8px; margin-top:12px; padding:8px; }
            QGroupBox::title { color:#94a3b8; padding:0 6px; }
            QCheckBox { color:#94a3b8; }
            QLabel#status_ok    { color:#22d3ee; }
            QLabel#status_error { color:#f87171; }
            QLabel#hint { color:#64748b; font-size:11px; }
        """)

        self._token, self._email = _load_token()
        self._machine_types      = {}
        self._projects            = []
        self._worker              = None

        self._build_ui()
        if self._token:
            self._refresh_after_login()

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        hdr = QtWidgets.QLabel("⚡ RENDERFARM")
        hdr.setStyleSheet("font-size:18px; font-weight:700; color:#0ea5e9; padding:4px 0;")
        root.addWidget(hdr)

        if not _IN_UNREAL:
            warn = QtWidgets.QLabel(
                "⚠ Not running inside Unreal Editor's Python console — "
                "project/map/sequence/preset auto-detection is disabled. "
                "Fields can still be filled in manually.")
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#f59e0b; font-size:11px;")
            root.addWidget(warn)

        # Auth row
        auth_row = QtWidgets.QHBoxLayout()
        self._status_lbl = QtWidgets.QLabel("Not connected")
        self._status_lbl.setObjectName("status_error")
        auth_row.addWidget(self._status_lbl, 1)
        self._connect_btn    = QtWidgets.QPushButton("Connect")
        self._disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self._disconnect_btn.setObjectName("danger")
        self._disconnect_btn.setVisible(False)
        self._connect_btn.clicked.connect(self._do_login)
        self._disconnect_btn.clicked.connect(self._do_logout)
        auth_row.addWidget(self._connect_btn)
        auth_row.addWidget(self._disconnect_btn)
        root.addLayout(auth_row)

        # Job settings
        grp  = QtWidgets.QGroupBox("Job Settings")
        form = QtWidgets.QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)

        self._title_edit = QtWidgets.QLineEdit()
        self._title_edit.setPlaceholderText("Unreal Render")
        form.addRow("Title:", self._title_edit)

        self._project_combo = QtWidgets.QComboBox()
        form.addRow("Project:", self._project_combo)

        self._engine_combo = QtWidgets.QComboBox()
        for val, lbl in UNREAL_VERSIONS:
            self._engine_combo.addItem(lbl, val)
        form.addRow("Engine Version:", self._engine_combo)

        # .uproject path: auto-detected + manual override + browse button
        uproject_row = QtWidgets.QHBoxLayout()
        self._uproject_edit = QtWidgets.QLineEdit()
        self._uproject_edit.setPlaceholderText(r"C:\MyProject\MyProject.uproject")
        uproject_browse = QtWidgets.QPushButton("Browse…")
        uproject_browse.setObjectName("small")
        uproject_browse.clicked.connect(self._browse_uproject)
        uproject_row.addWidget(self._uproject_edit, 1)
        uproject_row.addWidget(uproject_browse)
        form.addRow("Project (.uproject):", uproject_row)

        # Map / Level -- editable combo, populated from the Asset Registry
        map_row = QtWidgets.QHBoxLayout()
        self._map_combo = QtWidgets.QComboBox()
        self._map_combo.setEditable(True)
        self._map_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        map_refresh = QtWidgets.QPushButton("⟳")
        map_refresh.setObjectName("small")
        map_refresh.setToolTip("Re-scan project for maps")
        map_refresh.clicked.connect(self._populate_map_combo)
        map_row.addWidget(self._map_combo, 1)
        map_row.addWidget(map_refresh)
        form.addRow("Map / Level:", map_row)

        # Level Sequence -- MANDATORY selection (same gap as Houdini's ROP /
        # Nuke's Write node: Unreal has no single "active sequence").
        seq_row = QtWidgets.QHBoxLayout()
        self._sequence_combo = QtWidgets.QComboBox()
        self._sequence_combo.setEditable(True)
        self._sequence_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        seq_refresh = QtWidgets.QPushButton("⟳")
        seq_refresh.setObjectName("small")
        seq_refresh.setToolTip("Re-scan project for Level Sequences")
        seq_refresh.clicked.connect(self._populate_sequence_combo)
        seq_row.addWidget(self._sequence_combo, 1)
        seq_row.addWidget(seq_refresh)
        form.addRow("Level Sequence:", seq_row)

        # MoviePipelineConfig preset -- MANDATORY selection. Holds the
        # actual output format/dir/frame-range/resolution/renderer settings.
        cfg_row = QtWidgets.QHBoxLayout()
        self._config_combo = QtWidgets.QComboBox()
        self._config_combo.setEditable(True)
        self._config_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        cfg_refresh = QtWidgets.QPushButton("⟳")
        cfg_refresh.setObjectName("small")
        cfg_refresh.setToolTip("Re-scan project for MoviePipelineConfig presets")
        cfg_refresh.clicked.connect(self._populate_config_combo)
        cfg_row.addWidget(self._config_combo, 1)
        cfg_row.addWidget(cfg_refresh)
        form.addRow("MoviePipelineConfig:", cfg_row)

        cfg_hint = QtWidgets.QLabel(
            "Output directory, format, frame range and resolution are all "
            "configured inside this preset asset in the Unreal Editor — "
            "they are not overridden from here.")
        cfg_hint.setObjectName("hint")
        cfg_hint.setWordWrap(True)
        form.addRow("", cfg_hint)

        self._chunk_spin = QtWidgets.QSpinBox()
        self._chunk_spin.setRange(1, 500)
        self._chunk_spin.setValue(1)
        form.addRow("Chunk Size:", self._chunk_spin)

        root.addWidget(grp)

        # Machine settings
        mgrp  = QtWidgets.QGroupBox("Machine")
        mform = QtWidgets.QFormLayout(mgrp)
        mform.setLabelAlignment(Qt.AlignRight)

        self._instance_combo = QtWidgets.QComboBox()
        self._instance_combo.addItems(["GPU", "CPU"])
        self._instance_combo.currentTextChanged.connect(self._update_machine_list)
        mform.addRow("Instance Type:", self._instance_combo)

        self._machine_combo = QtWidgets.QComboBox()
        mform.addRow("Machine Type:", self._machine_combo)

        self._preemptible_chk = QtWidgets.QCheckBox("Use preemptible (cheaper, may restart)")
        mform.addRow("", self._preemptible_chk)

        root.addWidget(mgrp)

        # Progress + submit
        self._progress_lbl = QtWidgets.QLabel("")
        self._progress_lbl.setStyleSheet("color:#94a3b8; font-size:12px;")
        root.addWidget(self._progress_lbl)

        self._submit_btn = QtWidgets.QPushButton("Submit Job")
        self._submit_btn.setEnabled(False)
        self._submit_btn.clicked.connect(self._do_submit)
        root.addWidget(self._submit_btn)

        # ── Auto-detection from the running Unreal session ────────────────
        self._uproject_edit.setText(_get_project_file_path())

        idx = self._engine_combo.findData(_detect_engine_version())
        if idx >= 0:
            self._engine_combo.setCurrentIndex(idx)

        proj_name = ""
        if self._uproject_edit.text():
            proj_name = os.path.splitext(os.path.basename(self._uproject_edit.text()))[0]
        self._title_edit.setText(f"Unreal Render — {proj_name}" if proj_name else "Unreal Render")

        self._populate_map_combo()
        self._populate_sequence_combo()
        self._populate_config_combo()

    # ── Browse ────────────────────────────────────────────────────────────────
    def _browse_uproject(self):
        start_dir = os.path.dirname(self._uproject_edit.text()) if self._uproject_edit.text() else ""
        path, _filt = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select .uproject file", start_dir, "Unreal Project (*.uproject)")
        if path:
            self._uproject_edit.setText(os.path.normpath(path))

    # ── Asset-registry-backed combo population ─────────────────────────────
    def _populate_map_combo(self):
        self._map_combo.clear()
        maps = _scan_maps()
        for object_path, _label in maps:
            self._map_combo.addItem(_short_asset_name(object_path), _short_asset_name(object_path))
        current = _get_current_map_object_path()
        if current:
            short = _short_asset_name(current)
            idx = self._map_combo.findText(short)
            if idx >= 0:
                self._map_combo.setCurrentIndex(idx)
            else:
                self._map_combo.setEditText(short)
        if not maps and not current:
            self._map_combo.setEditText("")
            self._map_combo.lineEdit().setPlaceholderText("e.g. MyMap  (no maps found — type the map name)")

    def _populate_sequence_combo(self):
        self._sequence_combo.clear()
        seqs = _scan_level_sequences()
        for object_path, _label in seqs:
            self._sequence_combo.addItem(object_path, object_path)
        if not seqs:
            self._sequence_combo.setEditText("")
            self._sequence_combo.lineEdit().setPlaceholderText(
                "/Game/Cinematics/MySequence.MySequence  (none found — type the asset path)")

    def _populate_config_combo(self):
        self._config_combo.clear()
        cfgs = _scan_moviepipeline_configs()
        for object_path, _label in cfgs:
            self._config_combo.addItem(object_path, object_path)
        if not cfgs:
            self._config_combo.setEditText("")
            self._config_combo.lineEdit().setPlaceholderText(
                "/Game/Cinematics/MoviePipeline/Presets/MyPreset.MyPreset  (none found — type the asset path)")

    # ── Auth ──────────────────────────────────────────────────────────────────
    def _do_login(self):
        self._status_lbl.setText("Opening browser…")
        self._connect_btn.setEnabled(False)
        _start_browser_login(self._on_token_received)

    def _on_token_received(self, token, email):
        self._token = token
        self._email = email
        _save_token(token, email)
        QtCore.QMetaObject.invokeMethod(self, "_refresh_after_login", Qt.QueuedConnection)

    @QtCore.Slot()
    def _refresh_after_login(self):
        try:
            self._projects = _get("/projects", self._token)
            self._machine_types = _fetch_machine_types()
        except Exception as e:
            self._status_lbl.setText(f"Error fetching data: {e}")
            self._status_lbl.setObjectName("status_error")
            self._connect_btn.setEnabled(True)
            return

        self._project_combo.clear()
        for p in self._projects:
            if p.get("isActive", True):
                self._project_combo.addItem(p["name"], str(p["id"]))
        if not self._projects:
            self._project_combo.addItem("No projects", "none")

        self._update_machine_list()
        self._status_lbl.setText(f"Connected as {self._email}")
        self._status_lbl.setObjectName("status_ok")
        self._status_lbl.setStyleSheet("color:#22d3ee;")
        self._connect_btn.setVisible(False)
        self._disconnect_btn.setVisible(True)
        self._submit_btn.setEnabled(True)

    def _do_logout(self):
        _clear_token()
        self._token = None
        self._email = None
        self._status_lbl.setText("Not connected")
        self._status_lbl.setStyleSheet("color:#f87171;")
        self._connect_btn.setVisible(True)
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setVisible(False)
        self._submit_btn.setEnabled(False)
        self._project_combo.clear()
        self._machine_combo.clear()

    # ── Machine list ──────────────────────────────────────────────────────────
    def _update_machine_list(self):
        inst = self._instance_combo.currentText()
        self._machine_combo.clear()
        for mid, mlabel in self._machine_types.get(inst, _FALLBACK_MACHINE_TYPES.get(inst, [])):
            self._machine_combo.addItem(mlabel, mid)

    # ── Submit ────────────────────────────────────────────────────────────────
    def _do_submit(self):
        if not self._token:
            QtWidgets.QMessageBox.warning(self, "Not connected", "Please connect first.")
            return

        project_id = self._project_combo.currentData()
        if not project_id or project_id == "none":
            QtWidgets.QMessageBox.warning(self, "No project", "Select a project before submitting.")
            return

        uproject_path = self._uproject_edit.text().strip()
        if not uproject_path or not os.path.isfile(uproject_path):
            QtWidgets.QMessageBox.warning(
                self, "Project not found",
                "Enter or browse to a valid .uproject file before submitting.")
            return

        map_name = (self._map_combo.currentText() or "").strip()
        if not map_name:
            QtWidgets.QMessageBox.warning(self, "No map selected", "Select or type a Map / Level name.")
            return

        level_sequence = (self._sequence_combo.currentText() or "").strip()
        if not level_sequence:
            QtWidgets.QMessageBox.warning(
                self, "No Level Sequence selected",
                "Select or type the Level Sequence asset path to render.\n\n"
                "If the list was empty, no LevelSequence assets could be found "
                "by scanning the Asset Registry -- type the path manually, "
                "e.g. /Game/Cinematics/MySequence.MySequence")
            return

        moviepipeline_config = (self._config_combo.currentText() or "").strip()
        if not moviepipeline_config:
            QtWidgets.QMessageBox.warning(
                self, "No MoviePipelineConfig selected",
                "Select or type the MoviePipelineConfig preset asset path.\n\n"
                "This preset must already exist and be saved inside the "
                "project (author it via Window > Cinematics > Movie Render "
                "Queue in the Unreal Editor first).")
            return

        params = {
            "token":                self._token,
            "title":                self._title_edit.text().strip() or "Unreal Render",
            "engine_version":       self._engine_combo.currentData(),
            "uproject_path":        uproject_path,
            "map_name":             map_name,
            "level_sequence":       level_sequence,
            "moviepipeline_config": moviepipeline_config,
            "frames_display":       _get_sequence_playback_range_str(level_sequence),
            "project_id":           project_id,
            "chunk_size":           self._chunk_spin.value(),
            "instance_type":        self._instance_combo.currentText(),
            "machine_type":         self._machine_combo.currentData() or "n1-4",
            "preemptible":          self._preemptible_chk.isChecked(),
        }

        self._submit_btn.setEnabled(False)
        self._worker = SubmitWorker(params)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, msg):
        self._progress_lbl.setText(msg)

    def _on_finished(self, job_number):
        self._submit_btn.setEnabled(True)
        self._progress_lbl.setText(f"✓ Submitted: {job_number}")
        QtWidgets.QMessageBox.information(
            self, "Job Submitted",
            f"Job {job_number} submitted successfully!\n\nView progress at renderfarm.swade-art.com",
        )

    def _on_error(self, msg):
        self._submit_btn.setEnabled(True)
        self._progress_lbl.setText(f"Error: {msg}")
        QtWidgets.QMessageBox.critical(self, "Submission Failed", msg)


# ── Entry point ───────────────────────────────────────────────────────────────
_window = None

def _get_unreal_main_window():
    """
    Unreal has no OpenMayaUI/hou.qt-style official helper to wrap its main
    editor window as a Qt widget -- Unreal's own UI is built on Slate, not
    Qt, and the Python Editor Script Plugin does not document a supported
    Slate-to-QWidget bridge for parenting external Qt dialogs (unlike
    Maya's `shiboken2.wrapInstance(MQtUtil.mainWindow())` or Houdini's
    `hou.qt.mainWindow()`). As a best-effort fallback (same approach used
    by this project's Nuke addon for Foundry's undocumented main window),
    we scan Qt's own top-level widgets for something that looks like the
    editor's main window. If that fails, we simply parent to nothing --
    a standalone top-level QDialog works fine when PySide2/PySide6 is
    running inside Unreal's embedded Python/Qt event loop.
    """
    try:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return None
        for w in app.topLevelWidgets():
            try:
                if w.inherits("QMainWindow") and w.isVisible():
                    return w
            except Exception:
                continue
    except Exception:
        pass
    return None


def show():
    """Call this from Unreal's Python console/output-log Cmd, a startup
    script, or an Editor Utility Widget button to open the submitter."""
    global _window
    parent = _get_unreal_main_window() if _IN_UNREAL else None

    if _window is not None:
        try:
            _window.close()
        except Exception:
            pass

    _window = RenderfarmSubmitter(parent)
    _window.setWindowFlags(_window.windowFlags() | Qt.Window)
    _window.show()
    _window.raise_()
