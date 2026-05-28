import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { autoUpdater } from 'electron-updater'

const isDev = process.env.NODE_ENV === 'development'

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
  ipcMain.handle('auth:login', async (_e, { email, password }: { email: string; password: string }) => {
    return apiRequest('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
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
    const { filePaths } = await dialog.showOpenDialog({
      title,
      filters: [{ name: 'Files', extensions }],
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
