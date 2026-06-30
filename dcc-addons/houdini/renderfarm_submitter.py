"""
Renderfarm Houdini Submitter v1.0.0
Submit Houdini render jobs to Renderfarm directly from Houdini.

Installation:
  1. In Houdini: Shelves → New Tool → paste the following into the Script tab:
       import sys; sys.path.insert(0, r"/path/to/folder")
       import renderfarm_submitter; renderfarm_submitter.show()
  2. Click the shelf button to open the submitter.

Requirements: Houdini 19.5+ (ships PySide2). Python 3.
"""

import os
import json
import hashlib
import threading
import webbrowser
import urllib.request
import urllib.parse
import urllib.error
import http.server
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Houdini imports ───────────────────────────────────────────────────────────
try:
    import hou
    _IN_HOUDINI = True
except ImportError:
    _IN_HOUDINI = False

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from PySide2.QtCore import Qt, Signal, QThread
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import Qt, Signal, QThread

# ── Config ────────────────────────────────────────────────────────────────────
API_BASE      = "https://renderfarm-web.vercel.app/api"
WEB_BASE      = "https://renderfarm-web.vercel.app"
CALLBACK_PORT = 8989
_TOKEN_FILE   = os.path.join(os.path.expanduser("~"), ".rf_token")

HOUDINI_VERSIONS = [
    ("houdini-20.5", "Houdini 20.5"),
    ("houdini-20.0", "Houdini 20.0"),
    ("houdini-19.5", "Houdini 19.5"),
]

RENDERERS = [
    ("karma",   "Karma (USD)"),
    ("mantra",  "Mantra"),
    ("redshift","Redshift"),
    ("arnold",  "Arnold"),
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

def _put_blob(url, filepath):
    size  = os.path.getsize(filepath)
    chunk = 65536
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
            buf = fh.read(chunk)
            if not buf:
                break
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
                b"<body><div class='card'><h2 style='color:#22d3ee'>&#10003; Houdini Connected</h2>"
                b"<p>You can close this tab and return to Houdini.</p></div></body></html>"
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
def _scan_dependencies():
    """Use hou.fileReferences() to find all external files referenced in the .hip."""
    if not _IN_HOUDINI:
        return []
    seen   = set()
    assets = []

    def _add(path, asset_type):
        if not path:
            return
        abs_path = hou.expandString(path)
        abs_path = os.path.abspath(abs_path)
        if abs_path in seen:
            return
        seen.add(abs_path)
        assets.append({
            "path":   abs_path,
            "type":   asset_type,
            "size":   os.path.getsize(abs_path) if os.path.exists(abs_path) else 0,
            "exists": os.path.exists(abs_path),
        })

    # Built-in HOM: returns list of (parm, value) for all file references
    for parm, value in hou.fileReferences():
        if not value:
            continue
        # Classify by parm name hints
        p_name = parm.name().lower() if parm else ""
        if any(k in p_name for k in ("tex", "img", "image", "map", "hdri")):
            asset_type = "texture"
        elif any(k in p_name for k in ("alembic", "abc", "bgeo", "geo", "vdb")):
            asset_type = "cache"
        elif "hip" in p_name:
            asset_type = "reference"
        else:
            asset_type = "file"
        _add(value, asset_type)

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
    finished = Signal(str)
    error    = Signal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def run(self):
        p     = self.params
        token = p["token"]
        try:
            self.progress.emit("Scanning scene dependencies…")
            assets     = _scan_dependencies()
            scene_file = hou.hipFile.path() if _IN_HOUDINI else ""
            if scene_file and os.path.exists(scene_file):
                assets.insert(0, {
                    "path": scene_file, "type": "scene",
                    "size": os.path.getsize(scene_file), "exists": True,
                })

            self.progress.emit(f"Hashing {len(assets)} files…")
            assets = _hash_all(assets)
            valid  = [a for a in assets if a.get("sha256")]

            self.progress.emit("Running preflight check…")
            pre            = _post("/jobs/preflight", {"assets": [{"sha256": a["sha256"]} for a in valid]}, token)
            missing_hashes = set(pre.get("missing", []))

            self.progress.emit("Creating job…")
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
                    self.progress.emit(f"Uploading {uploaded}/{len(to_upload)} files…")

            threads = [threading.Thread(target=_upload_one, args=(a,), daemon=True) for a in to_upload]
            for t in threads: t.start()
            for t in threads: t.join()

            self.progress.emit("Finalising job…")
            manifest = {
                "scene":    os.path.basename(scene_file),
                "software": p["software"],
                "renderer": p["renderer"],
                "assets":   [{"path": a["path"], "sha256": a["sha256"], "size_bytes": a["size"], "type": a["type"], "url": a.get("url", "")} for a in valid],
            }
            _patch(f"/jobs?id={job_id}", {
                "status": "pending", "manifest": manifest, "assets_uploaded": len(valid),
            }, token)

            self.finished.emit(job_number)
        except Exception as e:
            self.error.emit(str(e))

# ── Main UI ───────────────────────────────────────────────────────────────────
class RenderfarmSubmitter(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Renderfarm Submitter — Houdini")
        self.setMinimumWidth(520)
        self.setStyleSheet("""
            QDialog, QWidget { background:#12121c; color:#e2e8f0; font-family:'Segoe UI',system-ui; }
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
        """)

        self._token, self._email = _load_token()
        self._machine_types      = {}
        self._projects           = []
        self._worker             = None
        self._build_ui()
        if self._token:
            self._refresh_after_login()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        hdr = QtWidgets.QLabel("⚡ RENDERFARM")
        hdr.setStyleSheet("font-size:18px; font-weight:700; color:#0ea5e9; padding:4px 0;")
        root.addWidget(hdr)

        auth_row = QtWidgets.QHBoxLayout()
        self._status_lbl = QtWidgets.QLabel("Not connected")
        self._status_lbl.setStyleSheet("color:#f87171;")
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

        grp  = QtWidgets.QGroupBox("Job Settings")
        form = QtWidgets.QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)

        self._title_edit = QtWidgets.QLineEdit("Houdini Render")
        form.addRow("Title:", self._title_edit)

        self._project_combo = QtWidgets.QComboBox()
        form.addRow("Project:", self._project_combo)

        self._software_combo = QtWidgets.QComboBox()
        for val, lbl in HOUDINI_VERSIONS:
            self._software_combo.addItem(lbl, val)
        form.addRow("Houdini Version:", self._software_combo)

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

        self._progress_lbl = QtWidgets.QLabel("")
        self._progress_lbl.setStyleSheet("color:#94a3b8; font-size:12px;")
        root.addWidget(self._progress_lbl)

        self._submit_btn = QtWidgets.QPushButton("Submit Job")
        self._submit_btn.setEnabled(False)
        self._submit_btn.clicked.connect(self._do_submit)
        root.addWidget(self._submit_btn)

        if _IN_HOUDINI:
            hip = hou.hipFile.path()
            name = os.path.splitext(os.path.basename(hip))[0]
            self._title_edit.setText(f"Houdini Render — {name}" if name else "Houdini Render")
            rng = hou.playbar.frameRange()
            self._frames_edit.setText(f"{int(rng[0])}-{int(rng[1])}")

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
            self._projects      = _get("/projects", self._token)
            self._machine_types = _fetch_machine_types()
        except Exception as e:
            self._status_lbl.setText(f"Error: {e}")
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
        self._status_lbl.setStyleSheet("color:#22d3ee;")
        self._connect_btn.setVisible(False)
        self._disconnect_btn.setVisible(True)
        self._submit_btn.setEnabled(True)

    def _do_logout(self):
        _clear_token()
        self._token = None
        self._status_lbl.setText("Not connected")
        self._status_lbl.setStyleSheet("color:#f87171;")
        self._connect_btn.setVisible(True)
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setVisible(False)
        self._submit_btn.setEnabled(False)
        self._project_combo.clear()
        self._machine_combo.clear()

    def _update_machine_list(self):
        inst = self._instance_combo.currentText()
        self._machine_combo.clear()
        for mid, mlabel in self._machine_types.get(inst, _FALLBACK_MACHINE_TYPES.get(inst, [])):
            self._machine_combo.addItem(mlabel, mid)

    def _do_submit(self):
        if not self._token:
            QtWidgets.QMessageBox.warning(self, "Not connected", "Please connect first.")
            return
        project_id = self._project_combo.currentData()
        if not project_id or project_id == "none":
            QtWidgets.QMessageBox.warning(self, "No project", "Select a project.")
            return
        params = {
            "token":         self._token,
            "title":         self._title_edit.text().strip() or "Houdini Render",
            "frames":        self._frames_edit.text().strip() or "1-1",
            "software":      self._software_combo.currentData(),
            "renderer":      self._renderer_combo.currentData(),
            "project_id":    project_id,
            "chunk_size":    self._chunk_spin.value(),
            "instance_type": self._instance_combo.currentText(),
            "machine_type":  self._machine_combo.currentData() or "n1-4",
            "preemptible":   self._preemptible_chk.isChecked(),
        }
        self._submit_btn.setEnabled(False)
        self._worker = SubmitWorker(params)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, msg): self._progress_lbl.setText(msg)

    def _on_finished(self, job_number):
        self._submit_btn.setEnabled(True)
        self._progress_lbl.setText(f"✓ Submitted: {job_number}")
        QtWidgets.QMessageBox.information(self, "Job Submitted",
            f"Job {job_number} submitted!\n\nView at renderfarm.swade-art.com")

    def _on_error(self, msg):
        self._submit_btn.setEnabled(True)
        self._progress_lbl.setText(f"Error: {msg}")
        QtWidgets.QMessageBox.critical(self, "Submission Failed", msg)


# ── Entry point ───────────────────────────────────────────────────────────────
_window = None

def show():
    """Call from a Houdini shelf button to open the submitter."""
    global _window
    parent = None
    if _IN_HOUDINI:
        try:
            parent = hou.qt.mainWindow()
        except Exception:
            pass
    if _window is not None:
        try:
            _window.close()
        except Exception:
            pass
    _window = RenderfarmSubmitter(parent)
    _window.setWindowFlags(_window.windowFlags() | Qt.Window)
    _window.show()
    _window.raise_()
