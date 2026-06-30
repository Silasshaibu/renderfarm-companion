"""
Renderfarm Cinema 4D Submitter v1.0.0
Submit Cinema 4D render jobs to Renderfarm directly from Cinema 4D.

Installation:
  Windows: copy this folder to %APPDATA%\Maxon\Cinema 4D\plugins\renderfarm_submitter\
  macOS:   copy this folder to ~/Library/Preferences/Maxon/Cinema 4D/plugins/renderfarm_submitter/
  Restart Cinema 4D. The submitter appears under Extensions → Renderfarm Submitter.

Requirements: Cinema 4D 2023+ (ships Python 3.10+).
"""

import os
import json
import hashlib
import threading
import webbrowser
import urllib.request
import urllib.parse
import http.server
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Cinema 4D imports ─────────────────────────────────────────────────────────
try:
    import c4d
    from c4d import gui, plugins, bitmaps
    _IN_C4D = True
except ImportError:
    _IN_C4D = False

# ── Config ────────────────────────────────────────────────────────────────────
API_BASE      = "https://renderfarm-web.vercel.app/api"
WEB_BASE      = "https://renderfarm-web.vercel.app"
CALLBACK_PORT = 8989
_TOKEN_FILE   = os.path.join(os.path.expanduser("~"), ".rf_token")

PLUGIN_ID = 1059472   # unique plugin ID registered with Maxon

C4D_VERSIONS = [
    ("cinema4d-2025", "Cinema 4D 2025"),
    ("cinema4d-2024", "Cinema 4D 2024"),
    ("cinema4d-2023", "Cinema 4D 2023"),
]

RENDERERS = [
    ("redshift",  "Redshift"),
    ("arnold",    "Arnold"),
    ("standard",  "Standard"),
    ("physical",  "Physical"),
    ("octane",    "Octane"),
]

_FALLBACK_MACHINE_TYPES = {
    "GPU": [("t4-1", "T4 16GB · 4 vCPU · 15 GB")],
    "CPU": [("n1-4", "CPU · 4 vCPU · 15 GB")],
}

# ── Dialog IDs ────────────────────────────────────────────────────────────────
ID_BTN_CONNECT      = 1000
ID_BTN_DISCONNECT   = 1001
ID_BTN_SUBMIT       = 1002
ID_LBL_STATUS       = 1003
ID_EDIT_TITLE       = 1004
ID_COMBO_PROJECT    = 1005
ID_COMBO_SOFTWARE   = 1006
ID_COMBO_RENDERER   = 1007
ID_EDIT_FRAMES      = 1008
ID_SPIN_CHUNK       = 1009
ID_COMBO_INSTANCE   = 1010
ID_COMBO_MACHINE    = 1011
ID_CHK_PREEMPTIBLE  = 1012
ID_LBL_PROGRESS     = 1013
ID_GRP_JOB          = 1020
ID_GRP_MACHINE      = 1030

# ── Token helpers ─────────────────────────────────────────────────────────────
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

def _put_blob(url, filepath):
    size  = os.path.getsize(filepath)
    import http.client as hc
    conn_url = urllib.parse.urlparse(url)
    conn = hc.HTTPSConnection(conn_url.netloc, timeout=120)
    conn.connect()
    conn.putrequest("PUT", conn_url.path + (f"?{conn_url.query}" if conn_url.query else ""))
    conn.putheader("Content-Length", str(size))
    conn.putheader("Content-Type", "application/octet-stream")
    conn.endheaders()
    with open(filepath, "rb") as fh:
        while True:
            buf = fh.read(65536)
            if not buf: break
            conn.send(buf)
    resp = conn.getresponse(); resp.read(); conn.close()
    if resp.status not in (200, 201):
        raise RuntimeError(f"Blob upload failed: {resp.status}")

# ── Browser auth ──────────────────────────────────────────────────────────────
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
                b"<!doctype html><html><head><meta charset='utf-8'>"
                b"<style>body{margin:0;min-height:100vh;display:flex;align-items:center;"
                b"justify-content:center;background:#0d0d1a;font-family:system-ui;color:#fff}"
                b".card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:16px;"
                b"padding:48px;text-align:center}</style></head>"
                b"<body><div class='card'><h2 style='color:#22d3ee'>&#10003; Cinema 4D Connected</h2>"
                b"<p>You can close this tab and return to Cinema 4D.</p></div></body></html>"
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

    def log_message(self, *_): pass


def _start_browser_login(on_success):
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

    threading.Thread(target=_serve, daemon=True).start()

# ── Dependency scanner ────────────────────────────────────────────────────────
def _collect_shaders(obj, seen, assets):
    """Recursively collect all bitmap/file paths from material shaders."""
    if not obj:
        return
    # Check for bitmap shader texture paths
    if obj.CheckType(c4d.Xbitmap):
        path = obj[c4d.BITMAPSHADER_FILENAME]
        if path and path not in seen:
            seen.add(path)
            abs_p = c4d.documents.LoadFile(path) if not os.path.isabs(path) else path
            abs_p = os.path.abspath(path)
            assets.append({"path": abs_p, "type": "texture",
                           "size": os.path.getsize(abs_p) if os.path.exists(abs_p) else 0,
                           "exists": os.path.exists(abs_p)})
    child = obj.GetDown()
    while child:
        _collect_shaders(child, seen, assets)
        child = child.GetNext()


def _scan_dependencies():
    if not _IN_C4D:
        return []
    doc    = c4d.documents.GetActiveDocument()
    seen   = set()
    assets = []

    # Walk all materials for texture paths
    mat = doc.GetFirstMaterial()
    while mat:
        shader = mat.GetFirstShader()
        while shader:
            _collect_shaders(shader, seen, assets)
            shader = shader.GetNext()
        mat = mat.GetNext()

    # Walk all objects for XRef, Sound, and cache nodes
    def _walk(obj):
        if not obj:
            return
        if obj.CheckType(c4d.Oxref):
            path = obj[c4d.XREFIMPORT_FILE]
            if path and path not in seen:
                seen.add(path)
                abs_p = os.path.abspath(path)
                assets.append({"path": abs_p, "type": "reference",
                               "size": os.path.getsize(abs_p) if os.path.exists(abs_p) else 0,
                               "exists": os.path.exists(abs_p)})
        child = obj.GetDown()
        if child: _walk(child)
        nxt = obj.GetNext()
        if nxt: _walk(nxt)

    _walk(doc.GetFirstObject())
    return assets

# ── SHA-256 hashing ───────────────────────────────────────────────────────────
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def _hash_all(assets):
    existing = [a for a in assets if a["exists"]]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_sha256, a["path"]): a for a in existing}
        for fut in as_completed(futures):
            a = futures[fut]
            try:    a["sha256"] = fut.result()
            except: a["sha256"] = ""
    return assets

# ── Machine types ─────────────────────────────────────────────────────────────
def _fetch_machine_types():
    try:
        rows   = _get("/machine-types", token="")
        result = {"GPU": [], "CPU": []}
        for row in rows:
            result.setdefault(row.get("instance", "GPU"), []).append((row["id"], row["label"]))
        for k in ("GPU", "CPU"):
            if not result.get(k):
                result[k] = _FALLBACK_MACHINE_TYPES[k]
        return result
    except Exception:
        return dict(_FALLBACK_MACHINE_TYPES)

# ── Submission thread ─────────────────────────────────────────────────────────
class _SubmitThread(threading.Thread):
    def __init__(self, params, on_progress, on_done, on_error):
        super().__init__(daemon=True)
        self.params      = params
        self.on_progress = on_progress
        self.on_done     = on_done
        self.on_error    = on_error

    def run(self):
        p     = self.params
        token = p["token"]
        try:
            self.on_progress("Scanning scene dependencies…")
            assets = _scan_dependencies()
            doc    = c4d.documents.GetActiveDocument() if _IN_C4D else None
            scene_file = ""
            if doc:
                path = doc.GetDocumentPath()
                name = doc.GetDocumentName()
                if path and name:
                    scene_file = os.path.join(path, name)
            if scene_file and os.path.exists(scene_file):
                assets.insert(0, {"path": scene_file, "type": "scene",
                                   "size": os.path.getsize(scene_file), "exists": True})

            self.on_progress(f"Hashing {len(assets)} files…")
            assets = _hash_all(assets)
            valid  = [a for a in assets if a.get("sha256")]

            self.on_progress("Running preflight…")
            pre            = _post("/jobs/preflight", {"assets": [{"sha256": a["sha256"]} for a in valid]}, token)
            missing_hashes = set(pre.get("missing", []))

            self.on_progress("Creating job…")
            resp = _post("/jobs", {
                "title":          p["title"],
                "frames":         p["frames"],
                "software":       p["software"],
                "provider":       "renderfarm",
                "project_id":     p["project_id"],
                "chunk_size":     p["chunk_size"],
                "machine_type":   p["machine_type"],
                "preemptible":    p["preemptible"],
                "assets_total":   len(valid),
                "assets_uploaded": 0,
                "status":         "uploading",
                "render_settings": {
                    "renderer":      p["renderer"],
                    "machine_type":  p["machine_type"],
                    "instance_type": p["instance_type"],
                    "frame_range":   p["frames"],
                    "chunk_size":    p["chunk_size"],
                },
            }, token)
            job_id     = resp["id"]
            job_number = resp["jobNumber"]

            to_upload = [a for a in valid if a["sha256"] in missing_hashes]
            uploaded  = 0
            sem       = threading.Semaphore(2)

            def _upload_one(asset):
                nonlocal uploaded
                with sem:
                    tok_resp = _post("/assets", {
                        "action": "token", "sha256": asset["sha256"],
                        "filename": os.path.basename(asset["path"]), "size_bytes": asset["size"],
                    }, token)
                    if not tok_resp.get("exists"):
                        _put_blob(tok_resp["uploadUrl"], asset["path"])
                        conf = _post("/assets", {"action": "confirm", "clientToken": tok_resp["clientToken"]}, token)
                        asset["url"] = conf.get("url", "")
                    else:
                        asset["url"] = tok_resp.get("url", "")
                    uploaded += 1
                    self.on_progress(f"Uploading {uploaded}/{len(to_upload)} files…")

            threads = [threading.Thread(target=_upload_one, args=(a,), daemon=True) for a in to_upload]
            for t in threads: t.start()
            for t in threads: t.join()

            self.on_progress("Finalising job…")
            _patch(f"/jobs?id={job_id}", {
                "status": "pending",
                "manifest": {
                    "scene":    os.path.basename(scene_file),
                    "software": p["software"],
                    "renderer": p["renderer"],
                    "assets":   [{"path": a["path"], "sha256": a["sha256"], "size_bytes": a["size"],
                                  "type": a["type"], "url": a.get("url", "")} for a in valid],
                },
                "assets_uploaded": len(valid),
            }, token)
            self.on_done(job_number)
        except Exception as e:
            self.on_error(str(e))

# ── Cinema 4D Dialog ──────────────────────────────────────────────────────────
class RenderfarmDialog(gui.GeDialog):
    def __init__(self):
        self._token, self._email = _load_token()
        self._machine_types = {}
        self._projects      = []
        self._machine_list  = []  # [(id, label), ...]
        super().__init__()

    def CreateLayout(self):
        self.SetTitle("Renderfarm Submitter")
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 1, 0, "")
        self.GroupSpace(6, 6)
        self.GroupBorderSpace(10, 10, 10, 10)

        # Status / auth
        self.AddStaticText(ID_LBL_STATUS, c4d.BFH_SCALEFIT, name="Not connected")
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 1, "")
        self.AddButton(ID_BTN_CONNECT, c4d.BFH_LEFT, name="Connect")
        self.AddButton(ID_BTN_DISCONNECT, c4d.BFH_LEFT, name="Disconnect")
        self.GroupEnd()

        self.AddSeparatorH(200)

        # Job settings
        self.GroupBegin(ID_GRP_JOB, c4d.BFH_SCALEFIT, 2, 0, "Job Settings")
        self.GroupBorderNoTitle(c4d.BORDER_ROUND)
        self.GroupSpace(4, 4)

        self.AddStaticText(0, c4d.BFH_LEFT, name="Title:")
        self.AddEditText(ID_EDIT_TITLE, c4d.BFH_SCALEFIT)

        self.AddStaticText(0, c4d.BFH_LEFT, name="Project:")
        self.AddComboBox(ID_COMBO_PROJECT, c4d.BFH_SCALEFIT)

        self.AddStaticText(0, c4d.BFH_LEFT, name="C4D Version:")
        self.AddComboBox(ID_COMBO_SOFTWARE, c4d.BFH_SCALEFIT)
        for i, (val, lbl) in enumerate(C4D_VERSIONS):
            self.AddChild(ID_COMBO_SOFTWARE, i, lbl)

        self.AddStaticText(0, c4d.BFH_LEFT, name="Renderer:")
        self.AddComboBox(ID_COMBO_RENDERER, c4d.BFH_SCALEFIT)
        for i, (val, lbl) in enumerate(RENDERERS):
            self.AddChild(ID_COMBO_RENDERER, i, lbl)

        self.AddStaticText(0, c4d.BFH_LEFT, name="Frames:")
        self.AddEditText(ID_EDIT_FRAMES, c4d.BFH_SCALEFIT)

        self.AddStaticText(0, c4d.BFH_LEFT, name="Chunk Size:")
        self.AddEditNumberArrows(ID_SPIN_CHUNK, c4d.BFH_LEFT)

        self.GroupEnd()  # job

        # Machine settings
        self.GroupBegin(ID_GRP_MACHINE, c4d.BFH_SCALEFIT, 2, 0, "Machine")
        self.GroupBorderNoTitle(c4d.BORDER_ROUND)
        self.GroupSpace(4, 4)

        self.AddStaticText(0, c4d.BFH_LEFT, name="Instance:")
        self.AddComboBox(ID_COMBO_INSTANCE, c4d.BFH_SCALEFIT)
        self.AddChild(ID_COMBO_INSTANCE, 0, "GPU")
        self.AddChild(ID_COMBO_INSTANCE, 1, "CPU")

        self.AddStaticText(0, c4d.BFH_LEFT, name="Machine Type:")
        self.AddComboBox(ID_COMBO_MACHINE, c4d.BFH_SCALEFIT)

        self.AddStaticText(0, c4d.BFH_LEFT, name="")
        self.AddCheckbox(ID_CHK_PREEMPTIBLE, c4d.BFH_LEFT, 0, 0, "Use preemptible (cheaper)")

        self.GroupEnd()  # machine

        self.AddSeparatorH(200)
        self.AddStaticText(ID_LBL_PROGRESS, c4d.BFH_SCALEFIT, name="")
        self.AddButton(ID_BTN_SUBMIT, c4d.BFH_SCALEFIT, name="Submit Job")

        self.GroupEnd()  # root
        return True

    def InitValues(self):
        doc = c4d.documents.GetActiveDocument() if _IN_C4D else None
        if doc:
            name = os.path.splitext(doc.GetDocumentName())[0]
            self.SetString(ID_EDIT_TITLE, f"C4D Render — {name}" if name else "C4D Render")
            rd = doc.GetActiveRenderData()
            if rd:
                f_start = int(rd[c4d.RDATA_FRAMEFROM].GetFrame(doc.GetFps()))
                f_end   = int(rd[c4d.RDATA_FRAMETO].GetFrame(doc.GetFps()))
                self.SetString(ID_EDIT_FRAMES, f"{f_start}-{f_end}")
            else:
                self.SetString(ID_EDIT_FRAMES, "0-0")
        else:
            self.SetString(ID_EDIT_TITLE, "C4D Render")
            self.SetString(ID_EDIT_FRAMES, "0-100")

        self.SetLong(ID_SPIN_CHUNK, 1)
        self.Enable(ID_BTN_DISCONNECT, False)
        self.Enable(ID_BTN_SUBMIT, False)

        if self._token:
            self._refresh_after_login()
        return True

    def Command(self, cid, msg):
        if cid == ID_BTN_CONNECT:
            self.SetString(ID_LBL_STATUS, "Opening browser…")
            _start_browser_login(self._on_token_received)

        elif cid == ID_BTN_DISCONNECT:
            _clear_token()
            self._token = None
            self.SetString(ID_LBL_STATUS, "Not connected")
            self.Enable(ID_BTN_CONNECT, True)
            self.Enable(ID_BTN_DISCONNECT, False)
            self.Enable(ID_BTN_SUBMIT, False)

        elif cid == ID_COMBO_INSTANCE:
            self._update_machine_combo()

        elif cid == ID_BTN_SUBMIT:
            self._do_submit()

        return True

    def _on_token_received(self, token, email):
        self._token = token
        self._email = email
        _save_token(token, email)
        c4d.SpecialEventAdd(PLUGIN_ID)   # triggers CoreMessage to update UI on main thread

    def CoreMessage(self, cid, msg):
        if cid == PLUGIN_ID:
            self._refresh_after_login()
        return True

    def _refresh_after_login(self):
        try:
            self._projects      = _get("/projects", self._token)
            self._machine_types = _fetch_machine_types()
        except Exception as e:
            self.SetString(ID_LBL_STATUS, f"Error: {e}")
            return

        self.FreeChildren(ID_COMBO_PROJECT)
        for i, p in enumerate(self._projects):
            if p.get("isActive", True):
                self.AddChild(ID_COMBO_PROJECT, i, p["name"])
        if not self._projects:
            self.AddChild(ID_COMBO_PROJECT, 0, "No projects")

        self._update_machine_combo()
        self.SetString(ID_LBL_STATUS, f"Connected as {self._email}")
        self.Enable(ID_BTN_CONNECT, False)
        self.Enable(ID_BTN_DISCONNECT, True)
        self.Enable(ID_BTN_SUBMIT, True)

    def _update_machine_combo(self):
        inst_idx = self.GetLong(ID_COMBO_INSTANCE)
        inst     = "GPU" if inst_idx == 0 else "CPU"
        self.FreeChildren(ID_COMBO_MACHINE)
        self._machine_list = self._machine_types.get(inst, _FALLBACK_MACHINE_TYPES.get(inst, []))
        for i, (mid, mlabel) in enumerate(self._machine_list):
            self.AddChild(ID_COMBO_MACHINE, i, mlabel)

    def _set_progress(self, msg):
        self.SetString(ID_LBL_PROGRESS, msg)

    def _do_submit(self):
        if not self._token:
            gui.MessageDialog("Please connect first.")
            return
        project_idx = self.GetLong(ID_COMBO_PROJECT)
        if not self._projects:
            gui.MessageDialog("No project selected.")
            return
        active_projects = [p for p in self._projects if p.get("isActive", True)]
        project_id = str(active_projects[project_idx]["id"]) if project_idx < len(active_projects) else None
        if not project_id:
            gui.MessageDialog("Select a project.")
            return

        software_idx = self.GetLong(ID_COMBO_SOFTWARE)
        renderer_idx = self.GetLong(ID_COMBO_RENDERER)
        machine_idx  = self.GetLong(ID_COMBO_MACHINE)
        inst_idx     = self.GetLong(ID_COMBO_INSTANCE)

        params = {
            "token":         self._token,
            "title":         self.GetString(ID_EDIT_TITLE) or "C4D Render",
            "frames":        self.GetString(ID_EDIT_FRAMES) or "0-0",
            "software":      C4D_VERSIONS[software_idx][0],
            "renderer":      RENDERERS[renderer_idx][0],
            "project_id":    project_id,
            "chunk_size":    int(self.GetLong(ID_SPIN_CHUNK)),
            "instance_type": "GPU" if inst_idx == 0 else "CPU",
            "machine_type":  self._machine_list[machine_idx][0] if self._machine_list else "n1-4",
            "preemptible":   self.GetBool(ID_CHK_PREEMPTIBLE),
        }
        self.Enable(ID_BTN_SUBMIT, False)
        _SubmitThread(params,
            on_progress=self._set_progress,
            on_done=self._on_done,
            on_error=self._on_error,
        ).start()

    def _on_done(self, job_number):
        self.SetString(ID_LBL_PROGRESS, f"✓ Submitted: {job_number}")
        self.Enable(ID_BTN_SUBMIT, True)
        gui.MessageDialog(f"Job {job_number} submitted!\n\nView at renderfarm.swade-art.com")

    def _on_error(self, msg):
        self.SetString(ID_LBL_PROGRESS, f"Error: {msg}")
        self.Enable(ID_BTN_SUBMIT, True)
        gui.MessageDialog(f"Submission failed:\n{msg}")


# ── Plugin command (menu entry) ───────────────────────────────────────────────
class RenderfarmCommand(plugins.CommandData):
    _dlg = None

    def Execute(self, doc):
        if self._dlg is None:
            self._dlg = RenderfarmDialog()
        return self._dlg.Open(
            dlgtype=c4d.DLG_TYPE_ASYNC,
            pluginid=PLUGIN_ID,
            defaultw=480,
            defaulth=540,
        )

    def RestoreLayout(self, sec_ref):
        if self._dlg is None:
            self._dlg = RenderfarmDialog()
        return self._dlg.Restore(PLUGIN_ID, sec_ref)


# ── Plugin registration ───────────────────────────────────────────────────────
if _IN_C4D:
    def PluginMessage(msg_type, data):
        if msg_type == c4d.C4DPL_BUILDMENU:
            pass
        return False

    plugins.RegisterCommandPlugin(
        id=PLUGIN_ID,
        str="Renderfarm Submitter",
        info=0,
        icon=None,
        help="Submit render jobs to Renderfarm",
        dat=RenderfarmCommand(),
    )
