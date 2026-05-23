import { contextBridge, ipcRenderer } from 'electron'

const api = {
  auth: {
    login: (email: string, password: string) =>
      ipcRenderer.invoke('auth:login', { email, password }),
  },
  jobs: {
    list:   (token: string)             => ipcRenderer.invoke('jobs:list', token),
    create: (token: string, data: object) => ipcRenderer.invoke('jobs:create', { token, data }),
  },
  projects: {
    list: (token: string) => ipcRenderer.invoke('projects:list', token),
  },
  shell: {
    open: (url: string) => ipcRenderer.invoke('shell:open', url),
  },
  updater: {
    check:    ()  => ipcRenderer.invoke('updater:check')    as Promise<string | null>,
    download: ()  => ipcRenderer.invoke('updater:download') as Promise<void>,
    install:  ()  => ipcRenderer.invoke('updater:install')  as Promise<void>,
    // Push listeners — call once in a component useEffect
    onAvailable:    (cb: (version: string) => void) =>
      ipcRenderer.on('updater:available',    (_e, v) => cb(v)),
    onNotAvailable: (cb: () => void) =>
      ipcRenderer.on('updater:not-available', cb),
    onProgress:     (cb: (pct: number) => void) =>
      ipcRenderer.on('updater:progress',     (_e, pct) => cb(pct)),
    onDownloaded:   (cb: () => void) =>
      ipcRenderer.on('updater:downloaded',   cb),
    onError:        (cb: (msg: string) => void) =>
      ipcRenderer.on('updater:error',        (_e, msg) => cb(msg)),
  },
}

contextBridge.exposeInMainWorld('rfApi', api)

// Type helper — used by renderer TypeScript
export type RfApi = typeof api
