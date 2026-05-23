/**
 * Launch the Electron app, explicitly clearing ELECTRON_RUN_AS_NODE
 * (which is set by Claude Code / some CI tools and prevents Electron GUI from starting).
 */
const { spawn } = require('child_process')
const path    = require('path')
const fs      = require('fs')

// Resolve the Electron binary via the npm package
const electronPkgDir  = path.dirname(require.resolve('electron/package.json'))
const executableName  = fs.readFileSync(path.join(electronPkgDir, 'path.txt'), 'utf-8').trim()
const electronBin     = path.join(electronPkgDir, 'dist', executableName)

// Build a clean env without ELECTRON_RUN_AS_NODE
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE

console.log('Launching Electron from:', electronBin)
console.log('App directory: .')

const proc = spawn(electronBin, ['.'], {
  stdio: 'inherit',
  cwd: __dirname,
  env,
})

proc.on('close', (code) => process.exit(code ?? 0))
