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
const API_BASE = 'http://localhost:3001/api'

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
