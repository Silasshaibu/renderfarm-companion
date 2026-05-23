import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'fs'

const { version } = JSON.parse(readFileSync('./package.json', 'utf-8')) as { version: string }

export default defineConfig({
  main: {
    build: {
      rollupOptions: {
        external: ['electron'],
        output: { format: 'cjs' },
      },
    },
  },
  preload: {
    build: {
      rollupOptions: {
        external: ['electron'],
        output: { format: 'cjs' },
      },
    },
  },
  renderer: {
    define: {
      'import.meta.env.VITE_APP_VERSION': JSON.stringify(version),
    },
    resolve: {
      alias: { '@renderer': resolve('src/renderer/src') },
    },
    plugins: [react()],
  },
})
