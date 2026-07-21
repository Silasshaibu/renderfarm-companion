import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import http from 'http'
import { autoUpdater } from 'electron-updater'

const isDev = process.env.NODE_ENV === 'development'

// Web origin for the browser sign-in flow (must match a Google OAuth JS origin)
const WEB_BASE = 'https://renderfarm.swade-art.com'

// ── Auto-updater setup ────────────────────────────────────────────────────────
autoUpdater.autoDownload       = false   // user triggers download manually
autoUpdater.autoInstallOnAppQuit = true  // install silently on next quit

function getMainWindow(): BrowserWindow | undefined {
  return BrowserWindow.getAllWindows()[0]
}

autoUpdater.on('update-available', (info) => {
  getMainWindow()?.webContents.send('updater:available', info.version)
})
autoUpdater.on('update-not-available', () => {
  getMainWindow()?.webContents.send('updater:not-available')
})
autoUpdater.on('download-progress', ({ percent }) => {
  getMainWindow()?.webContents.send('updater:progress', Math.round(percent))
})
autoUpdater.on('update-downloaded', () => {
  getMainWindow()?.webContents.send('updater:downloaded')
})
autoUpdater.on('error', (err) => {
  getMainWindow()?.webContents.send('updater:error', err.message)
})
const API_BASE = 'https://renderfarm-web.vercel.app/api'

// ── helpers ──────────────────────────────────────────────────────────────────
async function apiRequest(path: string, options: RequestInit = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string> || {}),
    },
  })
  const json = await res.json()
  if (!res.ok) throw new Error(json.message || `HTTP ${res.status}`)
  return json
}

// ── Window ────────────────────────────────────────────────────────────────────
function createWindow(): void {
  const win = new BrowserWindow({
    width:           1120,
    height:          740,
    minWidth:        800,
    minHeight:       560,
    frame:           true,
    backgroundColor: '#12121c',
    show:            false,
    webPreferences: {
      preload:          join(__dirname, '../preload/index.js'),
      sandbox:          false,
      contextIsolation: true,
    },
    title: 'Renderfarm',
  })

  win.on('ready-to-show', () => win.show())

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    win.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    win.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// ── App ready ─────────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  app.setAppUserModelId('com.renderfarm.companion')

  // Register IPC handlers after app is ready
  ipcMain.handle('app:version', () => app.getVersion())

  ipcMain.handle('auth:login', async (_e, { email, password }: { email: string; password: string }) => {
    return apiRequest('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password, clientType: 'electron' }),
    })
  })

  // Browser-based sign-in (Google or password) via a localhost callback.
  // Spins up a one-shot 127.0.0.1 server, opens the web login with ?port=,
  // and resolves when the web app redirects back with the session token.
  ipcMain.handle('auth:browserLogin', async () => {
    return await new Promise<{ token: string; email: string }>((resolve, reject) => {
      let settled = false
      const server = http.createServer((req, res) => {
        try {
          const u = new URL(req.url || '', 'http://127.0.0.1')
          if (u.pathname !== '/callback') { res.writeHead(404); res.end(); return }
          const token = u.searchParams.get('token') || ''
          const email = u.searchParams.get('email') || ''
          res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
          res.end('<!doctype html><html><head><meta charset="utf-8"></head>'
            + '<body style="font-family:system-ui;background:#0f1117;color:#e2e8f0;text-align:center;padding-top:80px">'
            + '<h2 style="color:#22d3ee">&#10003; Signed in</h2>'
            + '<p>You can close this tab and return to Renderfarm Companion.</p></body></html>')
          if (!settled) {
            settled = true
            try { server.close() } catch { /* noop */ }
            if (token) resolve({ token, email })
            else reject(new Error('No token returned'))
          }
        } catch {
          res.writeHead(500); res.end()
        }
      })
      server.listen(0, '127.0.0.1', () => {
        const addr = server.address()
        const port = typeof addr === 'object' && addr ? addr.port : 0
        shell.openExternal(`${WEB_BASE}/login?port=${port}`)
      })
      server.on('error', (e) => { if (!settled) { settled = true; reject(e) } })
      setTimeout(() => {
        if (!settled) { settled = true; try { server.close() } catch { /* noop */ }; reject(new Error('Sign-in timed out')) }
      }, 5 * 60 * 1000)
    })
  })

  ipcMain.handle('jobs:list', async (_e, token: string) => {
    return apiRequest('/jobs', {
      headers: { Authorization: `Bearer ${token}` } as HeadersInit,
    })
  })

  ipcMain.handle('jobs:refreshOutputs', async (_e, { token, jobNumber }: { token: string; jobNumber: string }) => {
    return apiRequest(`/jobs/${jobNumber}/refresh-outputs`, {
      method:  'POST',
      headers: { Authorization: `Bearer ${token}` } as HeadersInit,
    })
  })

  ipcMain.handle('jobs:create', async (_e, { token, data }: { token: string; data: object }) => {
    return apiRequest('/jobs', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` } as HeadersInit,
      body: JSON.stringify(data),
    })
  })

  // ── Local-worker submit: hash + upload the scene file (and any extra assets)
  // through the same preflight → token → PUT → confirm flow the Blender addon
  // uses, build a real per-asset manifest, then create the job. Without this,
  // a "Renderfarm" (non-GCP) submission had no way to get a scene file onto the
  // server at all — the job was created with an empty manifest and the Python
  // worker immediately failed it ("No scene file found").
  ipcMain.handle('jobs:submitWithScene', async (_e, params: {
    token: string
    data: Record<string, unknown>
    sceneFilePath: string
    assetFilePaths: string[]
    frameStart: number
    frameEnd: number
    chunkSize: number
    renderer: string
    blenderVersion: string
    gpuEnabled: boolean
  }) => {
    const fs     = await import('fs')
    const path   = await import('path')
    const crypto = await import('crypto')
    const https  = await import('https')
    const { URL } = await import('url')

    const {
      token, data, sceneFilePath, assetFilePaths,
      frameStart, frameEnd, chunkSize, renderer, blenderVersion, gpuEnabled,
    } = params

    if (!sceneFilePath) {
      throw new Error('No scene file selected. Pick a scene file in the FILES tab before submitting.')
    }
    if (!fs.existsSync(sceneFilePath)) {
      throw new Error(`Scene file not found on disk: ${sceneFilePath}. Re-select it in the FILES tab.`)
    }
    for (const p of assetFilePaths) {
      if (!fs.existsSync(p)) {
        throw new Error(`Asset file not found on disk: ${p}. Remove it or re-select it in the FILES tab.`)
      }
    }

    // ── SHA-256 a file, streaming so large scenes don't get buffered in memory ──
    const sha256File = (p: string): Promise<string> =>
      new Promise((resolve, reject) => {
        const hash   = crypto.createHash('sha256')
        const stream = fs.createReadStream(p)
        stream.on('data',  (chunk) => hash.update(chunk as Buffer))
        stream.on('end',   () => resolve(hash.digest('hex')))
        stream.on('error', reject)
      })

    const inferType = (p: string): string => {
      const ext = path.extname(p).toLowerCase()
      if (['.png', '.jpg', '.jpeg', '.exr', '.tif', '.tiff', '.hdr', '.tga', '.webp'].includes(ext)) return 'image'
      if (ext === '.blend' || ext === '.blend1') return 'library'
      return 'asset'
    }

    // First entry is always the primary scene file — flagged type "blend" so the
    // worker's `next(a for a in assets if a.get("type") == "blend")` check finds it,
    // matching the manifest contract the Blender addon produces.
    const allFiles = [
      { absPath: sceneFilePath, type: 'blend' },
      ...assetFilePaths.map((p) => ({ absPath: p, type: inferType(p) })),
    ]

    // 1. Stat + hash every file up front
    const filesState = await Promise.all(allFiles.map(async (f) => {
      const stat   = fs.statSync(f.absPath)
      const sha256 = await sha256File(f.absPath)
      return { ...f, name: path.basename(f.absPath), size: stat.size, sha256 }
    }))

    // 2. Preflight — ask the server which of these hashes it doesn't have yet
    const total = filesState.length
    getMainWindow()?.webContents.send('assets:uploadProgress', {
      index: 0, total, filename: '', pct: 0, phase: 'preflight',
    })
    await apiRequest('/jobs/preflight', {
      method:  'POST',
      headers: { Authorization: `Bearer ${token}` } as HeadersInit,
      body:    JSON.stringify({ assets: filesState.map((f) => ({ sha256: f.sha256 })) }),
    })

    // 3. For each file: get a client upload token (or the cached URL if the
    //    asset already exists), PUT the bytes to Vercel Blob if needed, confirm.
    const sha256ToUrl: Record<string, string> = {}

    for (let i = 0; i < filesState.length; i++) {
      const f = filesState[i]
      getMainWindow()?.webContents.send('assets:uploadProgress', {
        index: i, total, filename: f.name, pct: 0, phase: 'uploading',
      })

      const tokenResp = await apiRequest('/assets?action=token', {
        method:  'POST',
        headers: { Authorization: `Bearer ${token}` } as HeadersInit,
        body:    JSON.stringify({ sha256: f.sha256, filename: f.name, size_bytes: f.size }),
      })

      if (tokenResp.exists) {
        sha256ToUrl[f.sha256] = tokenResp.url
      } else {
        const clientToken: string = tokenResp.clientToken
        const uploadUrl:   string = tokenResp.uploadUrl

        await new Promise<void>((resolve, reject) => {
          const url = new URL(uploadUrl)
          let uploaded = 0
          const req = https.request({
            hostname: url.hostname,
            path:     url.pathname + url.search,
            method:   'PUT',
            headers:  {
              'Authorization':   `Bearer ${clientToken}`,
              'Content-Type':    'application/octet-stream',
              'Content-Length':  f.size,
              'x-content-type':  'application/octet-stream',
            },
          }, (res) => {
            res.resume()
            if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) resolve()
            else reject(new Error(`Asset upload failed for ${f.name}: HTTP ${res.statusCode}`))
          })
          req.on('error', reject)

          const fileStream = fs.createReadStream(f.absPath)
          fileStream.on('data', (chunk) => {
            uploaded += (chunk as Buffer).length
            const pct = f.size > 0 ? Math.round((uploaded / f.size) * 100) : 100
            getMainWindow()?.webContents.send('assets:uploadProgress', {
              index: i, total, filename: f.name, pct, phase: 'uploading',
            })
          })
          fileStream.pipe(req)
        })

        await apiRequest('/assets?action=confirm', {
          method:  'POST',
          headers: { Authorization: `Bearer ${token}` } as HeadersInit,
          body:    JSON.stringify({ sha256: f.sha256, url: uploadUrl, filename: f.name, size_bytes: f.size }),
        })
        sha256ToUrl[f.sha256] = uploadUrl
      }
    }

    getMainWindow()?.webContents.send('assets:uploadProgress', {
      index: total, total, filename: '', pct: 100, phase: 'done',
    })

    // 4. Build the manifest the render worker expects (v7 format)
    const manifest = {
      scene:           filesState[0].name,
      blender_version: blenderVersion || '',
      renderer:        renderer || '',
      instance_type:   gpuEnabled ? 'GPU' : 'CPU',
      frame_start:     frameStart,
      frame_end:       frameEnd,
      chunk_size:      chunkSize,
      assets: filesState.map((f) => ({
        path:       f.name,
        sha256:     f.sha256,
        size_bytes: f.size,
        type:       f.type,
        blob_url:   sha256ToUrl[f.sha256] || '',
      })),
    }

    // 5. Create the job with the resolved manifest attached
    return apiRequest('/jobs', {
      method:  'POST',
      headers: { Authorization: `Bearer ${token}` } as HeadersInit,
      body:    JSON.stringify({ ...data, manifest }),
    })
  })

  ipcMain.handle('projects:list', async (_e, token: string) => {
    return apiRequest('/projects', {
      headers: { Authorization: `Bearer ${token}` } as HeadersInit,
    })
  })

  ipcMain.handle('shell:open', async (_e, url: string) => {
    await shell.openExternal(url)
  })

  // Open a local folder in Windows Explorer / Finder
  ipcMain.handle('shell:openPath', async (_e, folderPath: string) => {
    await shell.openPath(folderPath)
  })

  // ── File picker ──────────────────────────────────────────────────────────────
  ipcMain.handle('dialog:pickFile', async (_e, { title, extensions }: { title: string; extensions: string[] }) => {
    const { dialog } = await import('electron')
    // An empty extensions list means "any file" — Electron needs an explicit
    // wildcard filter for that rather than an empty extensions array.
    const filters = extensions && extensions.length > 0
      ? [{ name: 'Files', extensions }]
      : [{ name: 'All Files', extensions: ['*'] }]
    const { filePaths } = await dialog.showOpenDialog({
      title,
      filters,
      properties: ['openFile'],
    })
    return filePaths[0] ?? null
  })

  // ── GCP submit: upload .blend to GCS, then create job ────────────────────────
  ipcMain.handle('gcp:submit', async (_e, params: {
    token: string; blendFilePath: string; title: string; frames: string
    software: string; outputFolder: string; machineType: string; preemptible: boolean
    projectId: string
  }) => {
    const fs   = await import('fs')
    const path = await import('path')
    const https = await import('https')
    const { URL } = await import('url')

    const { token, blendFilePath, title, frames, software, outputFolder, machineType, preemptible, projectId } = params

    // 1. Generate a temp job ID for the GCS upload path
    const tempJobId = Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
    const filename  = path.basename(blendFilePath)

    // 2. Get signed upload URL from the API
    const uploadResp = await apiRequest('/gcp/upload-url', {
      method:  'POST',
      headers: { Authorization: `Bearer ${token}` } as HeadersInit,
      body:    JSON.stringify({ jobId: tempJobId, filename }),
    })

    // 3. Stream the blend file directly to GCS (no temp buffer — handles large files)
    const stats = fs.statSync(blendFilePath)
    const fileSize = stats.size
    let uploaded = 0

    await new Promise<void>((resolve, reject) => {
      const url = new URL(uploadResp.uploadUrl)
      const req = https.request({
        hostname: url.hostname,
        path:     url.pathname + url.search,
        method:   'PUT',
        headers:  {
          'Content-Type':   'application/octet-stream',
          'Content-Length': fileSize,
        },
      }, (res) => {
        res.resume()
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) resolve()
        else reject(new Error(`GCS upload failed: HTTP ${res.statusCode}`))
      })

      req.on('error', reject)

      const fileStream = fs.createReadStream(blendFilePath)
      fileStream.on('data', (chunk) => {
        uploaded += (chunk as Buffer).length
        const pct = Math.round((uploaded / fileSize) * 100)
        getMainWindow()?.webContents.send('gcp:uploadProgress', pct)
      })
      fileStream.pipe(req)
    })

    // 4. Create the job — API auto-dispatches the render VM
    return apiRequest('/jobs', {
      method:  'POST',
      headers: { Authorization: `Bearer ${token}` } as HeadersInit,
      body:    JSON.stringify({
        title,
        frames,
        software,
        output_folder:  outputFolder,
        provider:       'gcp',
        gcs_scene_path: uploadResp.gcsPath,
        machine_type:   machineType,
        preemptible,
        project_id:     projectId,
      }),
    })
  })

  // ── Folder picker (for output path override) ─────────────────────────────────
  ipcMain.handle('dialog:pickFolder', async (_e, defaultPath?: string) => {
    const { dialog } = await import('electron')
    const { filePaths } = await dialog.showOpenDialog({
      title:       'Select output folder',
      defaultPath: defaultPath || undefined,
      properties:  ['openDirectory', 'createDirectory'],
    })
    return filePaths[0] ?? null
  })

  // ── Submission file I/O ──────────────────────────────────────────────────────
  // Open a .json file and return its path + raw text
  ipcMain.handle('submission:load', async () => {
    const { dialog } = await import('electron')
    const fs = await import('fs')
    const { filePaths } = await dialog.showOpenDialog({
      title:   'Load submission recipe',
      filters: [{ name: 'JSON', extensions: ['json'] }],
      properties: ['openFile'],
    })
    if (!filePaths.length) return null
    const content = fs.readFileSync(filePaths[0], 'utf-8')
    return { filePath: filePaths[0], content }
  })

  // Save content to a known path (no dialog)
  ipcMain.handle('submission:save', async (_e, { filePath, content }: { filePath: string; content: string }) => {
    const fs = await import('fs')
    fs.writeFileSync(filePath, content, 'utf-8')
    return { filePath }
  })

  // Show save-dialog then write the file
  ipcMain.handle('submission:saveAs', async (_e, { content, defaultName }: { content: string; defaultName: string }) => {
    const { dialog } = await import('electron')
    const fs = await import('fs')
    const { filePath } = await dialog.showSaveDialog({
      title:       'Save submission recipe',
      defaultPath: defaultName,
      filters:     [{ name: 'JSON', extensions: ['json'] }],
    })
    if (!filePath) return null
    fs.writeFileSync(filePath, content, 'utf-8')
    return { filePath }
  })

  // Export resolved JSON + Python runner, let user choose save folder
  ipcMain.handle('submission:exportPython', async (_e, { jsonContent, pyContent, baseName }: { jsonContent: string; pyContent: string; baseName: string }) => {
    const { dialog } = await import('electron')
    const fs   = await import('fs')
    const path = await import('path')
    const { filePath } = await dialog.showSaveDialog({
      title:       'Export Python script',
      defaultPath: `${baseName}.py`,
      filters:     [{ name: 'Python', extensions: ['py'] }],
    })
    if (!filePath) return null
    const jsonPath = filePath.replace(/\.py$/, '.json')
    fs.writeFileSync(filePath,  pyContent,   'utf-8')
    fs.writeFileSync(jsonPath,  jsonContent,  'utf-8')
    return { pyPath: filePath, jsonPath, folder: path.dirname(filePath) }
  })

  // ── Count already-downloaded frames ─────────────────────────────────────────
  // `frames` is the job's frame range string e.g. "7-9" or "1,3,5-10"
  ipcMain.handle('frames:countExisting', async (_e, { folder, frames }: { folder: string; frames: string }) => {
    const fs = await import('fs')
    if (!folder || !fs.existsSync(folder)) return { existing: 0 }

    let dirFiles: string[]
    try { dirFiles = fs.readdirSync(folder) } catch { return { existing: 0 } }

    // Parse the frame range into individual frame numbers
    const frameNums: number[] = []
    for (const part of frames.split(',')) {
      const m = part.trim().match(/^(\d+)(?:-(\d+))?$/)
      if (!m) continue
      const s = parseInt(m[1]), e = m[2] ? parseInt(m[2]) : s
      for (let i = s; i <= e; i++) frameNums.push(i)
    }

    if (!frameNums.length) {
      // Fallback: count any file named with 4 leading digits (e.g. 0007.png)
      const existing = dirFiles.filter(f => /^\d{4}\./.test(f)).length
      return { existing }
    }

    let existing = 0
    for (const n of frameNums) {
      const base = String(n).padStart(4, '0')
      if (dirFiles.some(f => f.startsWith(base + '.'))) existing++
    }
    return { existing }
  })

  // ── Frame download IPC ───────────────────────────────────────────────────────
  ipcMain.handle('frames:download', async (_e, { outputs, jobNumber, outputPath, token }: { outputs: string[]; jobNumber: string; outputPath?: string; token?: string }) => {
    const { dialog } = await import('electron')
    const https      = await import('https')
    const fs         = await import('fs')
    const path       = await import('path')

    let baseFolder: string

    // Use the output path from the job if it exists on disk, otherwise ask
    if (outputPath && fs.existsSync(outputPath)) {
      baseFolder = outputPath
    } else {
      const { filePaths } = await dialog.showOpenDialog({
        title:         'Select folder to save frames',
        defaultPath:   outputPath || undefined,
        properties:    ['openDirectory', 'createDirectory'],
      })
      if (!filePaths.length) return { success: false, error: 'Cancelled' }
      baseFolder = filePaths[0]
    }

    // Save directly into the output folder — no job-number subfolder
    const folder = baseFolder
    fs.mkdirSync(folder, { recursive: true })

    // Scan the folder once and build a set of already-present frame numbers.
    // This is more robust than existsSync(dest) because it handles extension
    // differences (e.g. .PNG vs .png) and zero-padding variations, so we
    // never overwrite a file that's already on disk.
    let existingFrameNums: Set<number>
    try {
      existingFrameNums = new Set(
        fs.readdirSync(folder).flatMap(f => {
          const m = path.basename(f, path.extname(f)).match(/(\d+)$/)
          return m ? [parseInt(m[1], 10)] : []
        })
      )
    } catch {
      existingFrameNums = new Set()
    }

    let count         = 0
    const failedNums:    number[] = []
    const successNums:   number[] = []   // frame numbers now on disk (skipped + newly downloaded)

    for (let i = 0; i < outputs.length; i++) {
      const url      = outputs[i]
      const urlPath  = new URL(url).pathname
      const ext      = path.extname(urlPath) || '.png'

      // Extract the actual frame number from the URL filename.
      // Handles names like "0007.png", "frame_0007.exr", "render_007_final.png"
      // by taking the last run of digits in the basename (without extension).
      const basename = path.basename(urlPath, ext)
      const digitMatch = basename.match(/(\d+)$/) ?? basename.match(/(\d+)/)
      const frameNum = digitMatch ? parseInt(digitMatch[1], 10) : i + 1

      // Name the file with the real frame number, zero-padded to 4 digits
      const filename = String(frameNum).padStart(4, '0') + ext
      const dest     = path.join(folder, filename)

      // Skip frames that are already on disk (checked by frame number, not exact path)
      if (existingFrameNums.has(frameNum)) {
        count++
        successNums.push(frameNum)
        getMainWindow()?.webContents.send('frames:progress', {
          jobNumber, count, failedNums, total: outputs.length,
        })
        continue
      }

      try {
        await new Promise<void>((resolve, reject) => {
          const file = fs.createWriteStream(dest)
          https.get(url, (res) => {
            res.pipe(file)
            file.on('finish', () => { file.close(); resolve() })
          }).on('error', (err) => { fs.unlink(dest, () => {}); reject(err) })
        })
        count++
        successNums.push(frameNum)
      } catch {
        failedNums.push(frameNum)
      }

      // Send live progress back to renderer
      getMainWindow()?.webContents.send('frames:progress', {
        jobNumber, count, failedNums, total: outputs.length,
      })
    }

    // Notify the web API which frames are now on disk so it can mark tasks as 'downloaded'
    if (token && successNums.length > 0) {
      apiRequest(`/jobs/${jobNumber}/frames-downloaded`, {
        method:  'POST',
        headers: { Authorization: `Bearer ${token}` } as HeadersInit,
        body:    JSON.stringify({ frames: successNums }),
      }).catch(() => { /* best effort */ })
    }

    return { success: true, count, failedNums, folder }
  })

  // ── Updater IPC ─────────────────────────────────────────────────────────────
  ipcMain.handle('updater:check', async () => {
    if (isDev) return null          // skip in dev — no published release to check
    try {
      const result = await autoUpdater.checkForUpdates()
      return result?.updateInfo?.version ?? null
    } catch {
      return null
    }
  })

  ipcMain.handle('updater:download', async () => {
    await autoUpdater.downloadUpdate()
  })

  ipcMain.handle('updater:install', () => {
    autoUpdater.quitAndInstall(false, true)
  })

  createWindow()

  // Check for updates silently 3 seconds after launch (production only)
  if (!isDev) {
    setTimeout(() => autoUpdater.checkForUpdates().catch(() => {}), 3000)
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
