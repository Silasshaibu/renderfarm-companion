"""
Renderfarm Maya Submitter v1.1.0
Submit Maya render jobs to Renderfarm directly from Maya.

Installation:
  1. Copy this file anywhere on disk.
  2. In Maya: Python Script Editor → run:
       import sys; sys.path.insert(0, r"/path/to/folder"); import renderfarm_submitter; renderfarm_submitter.show()
  3. Or add a shelf button with the same two lines.

Requirements: Maya 2022+ (ships PySide2). Python 3.
"""

import os
import sys
import json
import hashlib
import threading
import webbrowser
import urllib.request
import urllib.parse
import urllib.error
import http.server
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Maya imports (available when running inside Maya) ────────────────────────
try:
    import maya.cmds as cmds
    import maya.OpenMaya as om
    _IN_MAYA = True
except ImportError:
    _IN_MAYA = False

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

MAYA_VERSIONS = [
    ("maya-2025", "Maya 2025"),
    ("maya-2024", "Maya 2024"),
    ("maya-2023", "Maya 2023"),
    ("maya-2022", "Maya 2022"),
]

RENDERERS = [
    ("arnold",   "Arnold"),
    ("vray",     "V-Ray"),
    ("redshift", "Redshift"),
    ("mayasw",   "Maya Software"),
    ("mayahw",   "Maya Hardware 2.0"),
]

_FALLBACK_MACHINE_TYPES = {
    "GPU": [("t4-1", "T4 16GB · 4 vCPU · 15 GB")],
    "CPU": [("n1-4", "CPU · 4 vCPU · 15 GB")],
}

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
                b"<!doctype html><html><head><meta charset='utf-8'><title>Renderfarm</title>"
                b"<style>body{margin:0;min-height:100vh;display:flex;align-items:center;"
                b"justify-content:center;background:#0d0d1a;font-family:system-ui;color:#fff}"
                b".card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:16px;"
                b"padding:48px;text-align:center}</style></head>"
                b"<body><div class='card'><h2 style='color:#22d3ee'>&#10003; Maya Connected</h2>"
                b"<p>You can close this tab and return to Maya.</p></div></body></html>"
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

# ── Dependency scanner ────────────────────────────────────────────────────────
def _scan_dependencies():
    """Return list of {path, type, size, exists} for all external files in the scene."""
    if not _IN_MAYA:
        return []

    seen  = set()
    assets = []

    def _add(path, asset_type):
        if not path or path in seen:
            return
        abs_path = os.path.abspath(cmds.workspace(expandName=path) if not os.path.isabs(path) else path)
        seen.add(abs_path)
        assets.append({
            "path":   abs_path,
            "type":   asset_type,
            "size":   os.path.getsize(abs_path) if os.path.exists(abs_path) else 0,
            "exists": os.path.exists(abs_path),
        })

    # File texture nodes
    for node in cmds.ls(type="file") or []:
        _add(cmds.getAttr(f"{node}.fileTextureName"), "texture")

    # References
    for ref in cmds.file(q=True, reference=True) or []:
        _add(ref, "reference")

    # Alembic nodes
    for node in cmds.ls(type="AlembicNode") or []:
        _add(cmds.getAttr(f"{node}.abc_File"), "cache_alembic")

    # GPU caches
    for node in cmds.ls(type="gpuCache") or []:
        _add(cmds.getAttr(f"{node}.cacheFileName"), "cache_gpu")

    # nCache / fluid caches — look for cacheFile nodes
    for node in cmds.ls(type="cacheFile") or []:
        folder = cmds.getAttr(f"{node}.cachePath")
        name   = cmds.getAttr(f"{node}.cacheName")
        for ext in (".xml", ".mcx", ".mc"):
            p = os.path.join(folder, name + ext)
            _add(p, "cache_ncache")

    # Image planes attached to cameras
    for cam in cmds.ls(type="camera") or []:
        for ip in cmds.listConnections(cam, type="imagePlane") or []:
            _add(cmds.getAttr(f"{ip}.imageName"), "image_plane")

    # Audio nodes
    for node in cmds.ls(type="audio") or []:
        _add(cmds.getAttr(f"{node}.filename"), "audio")

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
            try:
                a["sha256"] = fut.result()
            except Exception:
                a["sha256"] = ""
    return assets

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
        try:
            # 1. Scan + hash
            self.progress.emit("Scanning scene dependencies…")
            assets = _scan_dependencies()
            scene_file = cmds.file(q=True, sn=True) if _IN_MAYA else ""
            if scene_file:
                assets.insert(0, {
                    "path": scene_file, "type": "scene",
                    "size": os.path.getsize(scene_file) if os.path.exists(scene_file) else 0,
                    "exists": os.path.exists(scene_file),
                })
            self.progress.emit(f"Hashing {len(assets)} files…")
            assets = _hash_all(assets)

            valid = [a for a in assets if a.get("sha256")]

            # 2. Preflight
            self.progress.emit("Running preflight check…")
            pre = _post("/jobs/preflight", {"assets": [{"sha256": a["sha256"]} for a in valid]}, token)
            missing_hashes = set(pre.get("missing", []))

            # 3. Create job
            self.progress.emit("Creating job…")
            payload = {
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
            }
            resp = _post("/jobs", payload, token)
            job_id     = resp["id"]
            job_number = resp["jobNumber"]

            # 4. Upload missing assets
            to_upload = [a for a in valid if a["sha256"] in missing_hashes]
            uploaded  = 0
            sem       = threading.Semaphore(2)

            def _upload_one(asset):
                nonlocal uploaded
                with sem:
                    tok_resp = _post("/assets", {
                        "action":     "token",
                        "sha256":     asset["sha256"],
                        "filename":   os.path.basename(asset["path"]),
                        "size_bytes": asset["size"],
                    }, token)
                    if tok_resp.get("exists"):
                        asset["url"] = tok_resp.get("url", "")
                    else:
                        _put_blob(tok_resp["uploadUrl"], asset["path"])
                        conf = _post("/assets", {
                            "action":      "confirm",
                            "clientToken": tok_resp["clientToken"],
                        }, token)
                        asset["url"] = conf.get("url", "")
                    uploaded += 1
                    self.progress.emit(f"Uploading {uploaded}/{len(to_upload)} files…")

            threads = [threading.Thread(target=_upload_one, args=(a,), daemon=True) for a in to_upload]
            for t in threads: t.start()
            for t in threads: t.join()

            # Build URL map from all valid assets
            url_map = {a["path"]: a.get("url", "") for a in valid}

            # 5. Finalize
            # NOTE: asset entries use "blob_url" (not "url") — that's the key the
            # render worker's prepare_scene_v7() reads. instance_type is read by
            # the worker's GPU-availability check.
            self.progress.emit("Finalising job…")
            manifest = {
                "scene":         os.path.basename(scene_file),
                "software":      p["software"],
                "renderer":      p["renderer"],
                "instance_type": p["instance_type"],
                "machine_type":  p["machine_type"],
                "chunk_size":    p["chunk_size"],
                "assets":        [{"path": a["path"], "sha256": a["sha256"], "size_bytes": a["size"], "type": a["type"], "blob_url": url_map.get(a["path"], "")} for a in valid],
            }
            _patch(f"/jobs?id={job_id}", {
                "status":          "pending",
                "manifest":        manifest,
                "assets_uploaded": len(valid),
            }, token)

            self.finished.emit(job_number)

        except Exception as e:
            self.error.emit(str(e))

# ── Main UI ───────────────────────────────────────────────────────────────────
class RenderfarmSubmitter(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Renderfarm Submitter — Maya")
        self.setMinimumWidth(520)
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
            QGroupBox { border:1px solid #2a2a4a; border-radius:8px; margin-top:12px; padding:8px; }
            QGroupBox::title { color:#94a3b8; padding:0 6px; }
            QCheckBox { color:#94a3b8; }
            QLabel#status_ok    { color:#22d3ee; }
            QLabel#status_error { color:#f87171; }
        """)

        self._token, self._email = _load_token()
        self._machine_types      = {}
        self._projects           = []
        self._worker             = None

        self._build_ui()
        if self._token:
            self._refresh_after_login()

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        # Header
        hdr = QtWidgets.QLabel("⚡ RENDERFARM")
        hdr.setStyleSheet("font-size:18px; font-weight:700; color:#0ea5e9; padding:4px 0;")
        root.addWidget(hdr)

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
        grp = QtWidgets.QGroupBox("Job Settings")
        form = QtWidgets.QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)

        self._title_edit = QtWidgets.QLineEdit()
        self._title_edit.setPlaceholderText("Maya Render")
        form.addRow("Title:", self._title_edit)

        self._project_combo = QtWidgets.QComboBox()
        form.addRow("Project:", self._project_combo)

        self._software_combo = QtWidgets.QComboBox()
        for val, lbl in MAYA_VERSIONS:
            self._software_combo.addItem(lbl, val)
        form.addRow("Maya Version:", self._software_combo)

        self._renderer_combo = QtWidgets.QComboBox()
        for val, lbl in RENDERERS:
            self._renderer_combo.addItem(lbl, val)
        form.addRow("Renderer:", self._renderer_combo)

        self._frames_edit = QtWidgets.QLineEdit("1-10")
        form.addRow("Frames:", self._frames_edit)

        self._chunk_spin = QtWidgets.QSpinBox()
        self._chunk_spin.setRange(1, 500)
        self._chunk_spin.setValue(1)
        form.addRow("Chunk Size:", self._chunk_spin)

        root.addWidget(grp)

        # Machine settings
        mgrp = QtWidgets.QGroupBox("Machine")
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

        # Set default title from scene
        if _IN_MAYA:
            scene = cmds.file(q=True, sn=True, shortName=True)
            self._title_edit.setText(f"Maya Render — {scene}" if scene else "Maya Render")
            self._frames_edit.setText(f"{cmds.playbackOptions(q=True, min=True):.0f}-{cmds.playbackOptions(q=True, max=True):.0f}")

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

        params = {
            "token":        self._token,
            "title":        self._title_edit.text().strip() or "Maya Render",
            "frames":       self._frames_edit.text().strip() or "1-1",
            "software":     self._software_combo.currentData(),
            "renderer":     self._renderer_combo.currentData(),
            "project_id":   project_id,
            "chunk_size":   self._chunk_spin.value(),
            "instance_type": self._instance_combo.currentText(),
            "machine_type": self._machine_combo.currentData() or "n1-4",
            "preemptible":  self._preemptible_chk.isChecked(),
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

def show():
    """Call this from Maya's Script Editor or a shelf button to open the submitter."""
    global _window
    try:
        from shiboken2 import wrapInstance
        import maya.OpenMayaUI as omui
        ptr    = omui.MQtUtil.mainWindow()
        parent = wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        parent = None

    if _window is not None:
        try:
            _window.close()
        except Exception:
            pass

    _window = RenderfarmSubmitter(parent)
    _window.show()
    _window.raise_()
