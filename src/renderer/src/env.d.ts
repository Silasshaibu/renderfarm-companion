/// <reference types="vite/client" />

interface Window {
  rfApi: {
    auth: {
      login: (email: string, password: string) => Promise<{ access_token: string; user: { id: string; email: string; isAdmin: boolean } }>
    }
    jobs: {
      list:   (token: string) => Promise<unknown[]>
      create: (token: string, data: object) => Promise<{ jobNumber: string }>
    }
    projects: {
      list: (token: string) => Promise<{ id: string; name: string; isActive: boolean }[]>
    }
    shell: {
      open:     (url: string)  => Promise<void>
      openPath: (path: string) => Promise<void>
    }
    dialog: {
      pickFolder: (defaultPath?: string) => Promise<string | null>
      pickFile:   (title: string, extensions: string[]) => Promise<string | null>
    }
    gcp: {
      submit: (params: {
        token: string; blendFilePath: string; title: string; frames: string
        software: string; outputFolder: string; machineType: string; preemptible: boolean
        projectId: string
      }) => Promise<{ jobNumber: string; id: string }>
      onUploadProgress: (cb: (pct: number) => void) => void
    }
    submission: {
      load:         () => Promise<{ filePath: string; content: string } | null>
      save:         (filePath: string, content: string) => Promise<{ filePath: string }>
      saveAs:       (content: string, defaultName: string) => Promise<{ filePath: string } | null>
      exportPython: (jsonContent: string, pyContent: string, baseName: string) => Promise<{ pyPath: string; jsonPath: string; folder: string } | null>
    }
    frames: {
      download: (outputs: string[], jobNumber: string, outputPath?: string) => Promise<{ success: boolean; count?: number; failedNums?: number[]; folder?: string; error?: string }>
      countExisting: (folder: string, frames: string) => Promise<{ existing: number }>
      onProgress: (cb: (data: { jobNumber: string; count: number; failedNums: number[]; total: number }) => void) => void
    }
    updater: {
      check:          () => Promise<string | null>
      download:       () => Promise<void>
      install:        () => Promise<void>
      onAvailable:    (cb: (version: string) => void) => void
      onNotAvailable: (cb: () => void) => void
      onProgress:     (cb: (pct: number) => void) => void
      onDownloaded:   (cb: () => void) => void
      onError:        (cb: (msg: string) => void) => void
    }
  }
}
