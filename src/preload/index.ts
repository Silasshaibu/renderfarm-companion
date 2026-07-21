import { contextBridge, ipcRenderer } from 'electron'

const api = {
  app: {
    version: () => ipcRenderer.invoke('app:version') as Promise<string>,
  },
  auth: {
    login: (email: string, password: string) =>
      ipcRenderer.invoke('auth:login', { email, password }),
    browserLogin: () =>
      ipcRenderer.invoke('auth:browserLogin') as Promise<{ token: string; email: string }>,
  },
  jobs: {
    list:           (token: string)                      => ipcRenderer.invoke('jobs:list', token),
    create:         (token: string, data: object)        => ipcRenderer.invoke('jobs:create', { token, data }),
    refreshOutputs: (token: string, jobNumber: string)   => ipcRenderer.invoke('jobs:refreshOutputs', { token, jobNumber }),
    submitWithScene: (params: {
      token: string; data: Record<string, unknown>
      sceneFilePath: string; assetFilePaths: string[]
      frameStart: number; frameEnd: number; chunkSize: number
      renderer: string; blenderVersion: string; gpuEnabled: boolean
    }) => ipcRenderer.invoke('jobs:submitWithScene', params) as Promise<{ jobNumber: string; id: string }>,
  },
  projects: {
    list: (token: string) => ipcRenderer.invoke('projects:list', token),
  },
  shell: {
    open:     (url: string)    => ipcRenderer.invoke('shell:open', url),
    openPath: (path: string)   => ipcRenderer.invoke('shell:openPath', path) as Promise<void>,
  },
  dialog: {
    pickFolder: (defaultPath?: string) =>
      ipcRenderer.invoke('dialog:pickFolder', defaultPath) as Promise<string | null>,
    pickFile: (title: string, extensions: string[]) =>
      ipcRenderer.invoke('dialog:pickFile', { title, extensions }) as Promise<string | null>,
  },
  gcp: {
    submit: (params: {
      token: string; blendFilePath: string; title: string; frames: string
      software: string; outputFolder: string; machineType: string; preemptible: boolean
      projectId: string
    }) => ipcRenderer.invoke('gcp:submit', params) as Promise<{ jobNumber: string; id: string }>,
    onUploadProgress: (cb: (pct: number) => void) =>
      ipcRenderer.on('gcp:uploadProgress', (_e, pct) => cb(pct)),
  },
  assets: {
    onUploadProgress: (cb: (data: { index: number; total: number; filename: string; pct: number; phase: string }) => void) =>
      ipcRenderer.on('assets:uploadProgress', (_e, data) => cb(data)),
  },
  submission: {
    load: () =>
      ipcRenderer.invoke('submission:load') as Promise<{ filePath: string; content: string } | null>,
    save: (filePath: string, content: string) =>
      ipcRenderer.invoke('submission:save', { filePath, content }) as Promise<{ filePath: string }>,
    saveAs: (content: string, defaultName: string) =>
      ipcRenderer.invoke('submission:saveAs', { content, defaultName }) as Promise<{ filePath: string } | null>,
    exportPython: (jsonContent: string, pyContent: string, baseName: string) =>
      ipcRenderer.invoke('submission:exportPython', { jsonContent, pyContent, baseName }) as
        Promise<{ pyPath: string; jsonPath: string; folder: string } | null>,
  },
  frames: {
    download: (outputs: string[], jobNumber: string, outputPath?: string, token?: string) =>
      ipcRenderer.invoke('frames:download', { outputs, jobNumber, outputPath, token }) as
        Promise<{ success: boolean; count?: number; failedNums?: number[]; folder?: string; error?: string }>,
    countExisting: (folder: string, frames: string) =>
      ipcRenderer.invoke('frames:countExisting', { folder, frames }) as Promise<{ existing: number }>,
    onProgress: (cb: (data: { jobNumber: string; count: number; failedNums: number[]; total: number }) => void) =>
      ipcRenderer.on('frames:progress', (_e, data) => cb(data)),
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
