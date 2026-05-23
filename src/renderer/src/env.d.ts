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
      open: (url: string) => Promise<void>
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
