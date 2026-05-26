import { useState, useEffect, useRef, useMemo } from 'react'
import type { AuthState } from '../App'

type Tab = 'general' | 'files' | 'software' | 'environment' | 'preview'

const INSTANCE_OPTIONS = [
  '2 core, 8GB Mem', '4 core, 16GB Mem', '8 core, 32GB Mem',
  '16 core, 64GB Mem', '32 core, 128GB Mem',
]

// Maps instance dropdown labels → GCP machine type strings
const GCP_MACHINE_TYPES: Record<string, string> = {
  '2 core, 8GB Mem':    'n1-standard-2',
  '4 core, 16GB Mem':   'n1-standard-4',
  '8 core, 32GB Mem':   'n1-standard-8',
  '16 core, 64GB Mem':  'n1-standard-16',
  '32 core, 128GB Mem': 'n1-standard-32',
}

const SOFTWARE_PACKAGES: { id: string; versions: string[] }[] = [
  { id: 'SSGI_addon-blender',      versions: ['1.0.0', '1.1.0'] },
  { id: 'animation_nodes-blender', versions: ['2.1.7', '2.2.0'] },
  { id: 'arnold-cinema4d',         versions: ['4.6.4', '4.7.0'] },
  { id: 'arnold-houdini',          versions: ['7.1.4', '7.3.0'] },
  { id: 'arnold-katana',           versions: ['4.2.0', '4.3.0'] },
  { id: 'arnold-maya',             versions: ['5.3.4', '5.4.0'] },
  { id: 'arnold-standalone',       versions: ['7.1.4', '7.3.0'] },
  { id: 'blender',                 versions: ['3.1.0', '3.6.12', '4.0.0', '4.2.0'] },
  { id: 'blenrig-blender',         versions: ['6.0.0', '6.1.0'] },
  { id: 'bricker-blender',         versions: ['2.2.0', '2.3.0'] },
  { id: 'cinema4d',                versions: ['2024.0', '2024.4', '2025.0'] },
  { id: 'deadline',                versions: ['10.3.0', '10.4.0'] },
  { id: 'flip-blender',            versions: ['1.7.0', '2.0.0'] },
  { id: 'fluxdev-kohya',           versions: ['1.0.0'] },
  { id: 'fluxschnell-kohya',       versions: ['1.0.0'] },
  { id: 'golaem',                  versions: ['8.0.0', '9.0.0'] },
  { id: 'guerilla',                versions: ['2.4.0', '2.5.0'] },
  { id: 'houdini',                 versions: ['19.5.368', '20.0.547', '20.5.332'] },
  { id: 'katana',                  versions: ['5.0v3', '6.0v1'] },
  { id: 'keyshot',                 versions: ['11.0', '12.0'] },
  { id: 'maya',                    versions: ['2024.0', '2024.2', '2025.0'] },
  { id: 'nuke',                    versions: ['14.0v5', '15.0v1'] },
  { id: 'renderman',               versions: ['25.0', '26.0'] },
  { id: 'vray-blender',            versions: ['6.0', '6.1'] },
  { id: 'vray-maya',               versions: ['6.0', '6.1'] },
  { id: 'vray-houdini',            versions: ['6.0', '6.1'] },
  { id: '3dsmax',                  versions: ['2024.0', '2025.0'] },
]

interface Project { id: string; name: string; isActive: boolean }
interface EnvRow   { id: string; key: string; value: string }
interface Props    { auth: AuthState; setStatus: (s: string) => void }

// ── Help icon — self-contained popover ───────────────────────────────────────
function HelpIcon({ title, body }: { title: string; body: string | string[] }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const paragraphs = Array.isArray(body) ? body : [body]

  return (
    <div ref={ref} className="sk-help-wrap">
      <button
        type="button"
        className={`sk-help-btn ${open ? 'sk-help-btn--open' : ''}`}
        title={title}
        onClick={() => setOpen(o => !o)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="10"/>
          <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
          <line x1="12" y1="17" x2="12.01" y2="17" strokeWidth="3"/>
        </svg>
      </button>

      {open && (
        <div className="sk-help-popover" role="tooltip">
          <p className="sk-help-popover-title">{title}</p>
          {paragraphs.map((p, i) => <p key={i} className="sk-help-popover-body">{p}</p>)}
        </div>
      )}
    </div>
  )
}

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <polyline points="3 6 5 6 21 6"/>
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
      <path d="M10 11v6"/><path d="M14 11v6"/>
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
    </svg>
  )
}

// ── Toggle switch ─────────────────────────────────────────────────────────
function Toggle({ on, onChange, label = 'Toggle' }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button
      type="button"
      className={`sk-toggle ${on ? 'sk-toggle--on' : ''}`}
      onClick={() => onChange(!on)}
      aria-label={`${label}: ${on ? 'on' : 'off'}`}
    >
      <span className="sk-toggle-thumb" />
    </button>
  )
}

// ── JSON syntax highlighter ───────────────────────────────────────────────
function JsonLine({ text }: { text: string }) {
  // Very simple tokeniser: colour keys, strings, keywords, numbers
  const parts: { text: string; cls: string }[] = []
  let remaining = text

  // Leading indent
  const indent = remaining.match(/^(\s*)/)?.[1] ?? ''
  remaining = remaining.slice(indent.length)
  if (indent) parts.push({ text: indent, cls: '' })

  // Key  "foo":
  const keyMatch = remaining.match(/^("[\w_]+"):(\s*)/)
  if (keyMatch) {
    parts.push({ text: keyMatch[1], cls: 'pv-key' })
    parts.push({ text: `:${keyMatch[2]}`, cls: 'pv-punct' })
    remaining = remaining.slice(keyMatch[0].length)
  }

  // Trailing comma/bracket marker
  const trailingComma = remaining.endsWith(',') ? ',' : ''
  if (trailingComma) remaining = remaining.slice(0, -1)

  if (remaining === '{' || remaining === '}' || remaining === '[' || remaining === ']' ||
      remaining === '{,' || remaining === '},' || remaining === '],' || remaining === '[,') {
    parts.push({ text: remaining + trailingComma, cls: 'pv-punct' })
  } else if (remaining.startsWith('"')) {
    parts.push({ text: remaining, cls: 'pv-string' })
    if (trailingComma) parts.push({ text: trailingComma, cls: 'pv-punct' })
  } else if (remaining === 'true' || remaining === 'false' || remaining === 'null') {
    parts.push({ text: remaining, cls: 'pv-keyword' })
    if (trailingComma) parts.push({ text: trailingComma, cls: 'pv-punct' })
  } else if (/^-?\d/.test(remaining)) {
    parts.push({ text: remaining, cls: 'pv-number' })
    if (trailingComma) parts.push({ text: trailingComma, cls: 'pv-punct' })
  } else {
    parts.push({ text: remaining + trailingComma, cls: 'pv-punct' })
  }

  return (
    <div className="pv-line">
      {parts.map((p, i) => (
        p.cls ? <span key={i} className={p.cls}>{p.text}</span> : <span key={i}>{p.text}</span>
      ))}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────
export default function SubmissionKitPage({ auth, setStatus }: Props) {
  const [activeTab,      setActiveTab]     = useState<Tab>('general')
  const [projects,       setProjects]      = useState<Project[]>([])
  const [showNotice,     setShowNotice]    = useState(true)
  const [saved,          setSaved]         = useState(false)
  const [submitting,     setSubmitting]    = useState(false)
  const [submitted,      setSubmitted]     = useState<string | null>(null)
  const [menuOpen,       setMenuOpen]      = useState(false)
  const [currentFile,    setCurrentFile]   = useState<string | null>(null)  // path of loaded/saved .json
  const menuRef = useRef<HTMLDivElement>(null)

  // Provider — which cloud backend to submit to
  const [provider,       setProvider]      = useState<'renderfarm' | 'gcp'>('renderfarm')

  // GENERAL fields
  const [jobTitle,       setJobTitle]      = useState('')
  const [projectId,      setProjectId]     = useState('')
  const [frames,         setFrames]        = useState('1-10')
  const [chunkSize,      setChunkSize]     = useState('1')
  const [tilesVal,       setTilesVal]      = useState('1-9')
  const [tilesOn,        setTilesOn]       = useState(false)
  const [scoutFrames,    setScoutFrames]   = useState('1')
  const [scoutOn,        setScoutOn]       = useState(true)
  const [platform,       setPlatform]      = useState<'linux'|'windows'>('linux')
  const [gpuEnabled,     setGpuEnabled]    = useState(false)
  const [instance,       setInstance]      = useState('4 core, 16GB Mem')
  const [retries,        setRetries]       = useState('1')
  const [outputFolder,   setOutputFolder]  = useState('/tmp/renders')
  const [taskTemplate,   setTaskTemplate]  = useState('')

  // SOFTWARE tab fields
  const [softwareName,   setSoftwareName]  = useState('')
  const [softwareVersion,setSoftwareVersion] = useState('')

  // FILES tab
  const [uploadPaths,    setUploadPaths]   = useState<string[]>([])
  const [selectedPaths,  setSelectedPaths] = useState<Set<number>>(new Set())

  // ENVIRONMENT tab
  const [envRows,        setEnvRows]       = useState<EnvRow[]>([])

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuOpen])

  const [projectsLoaded, setProjectsLoaded] = useState(false)

  useEffect(() => {
    window.rfApi.projects.list(auth.token)
      .then((list) => {
        const active = list.filter((p) => p.isActive)
        setProjects(active)
        if (active.length > 0) setProjectId(active[0].id)
        setProjectsLoaded(true)
      })
      .catch(() => { setProjectsLoaded(true) })
  }, [auth.token])

  // Derived
  const softwarePkg   = SOFTWARE_PACKAGES.find((p) => p.id === softwareName)
  const softwareLabel = softwareName && softwareVersion ? `${softwareName}:${softwareVersion}` : ''
  // GCP also requires a .blend file to have been selected
  const canSubmit     = Boolean(
    jobTitle.trim() && projectId &&
    (provider !== 'gcp' || uploadPaths.length > 0)
  )

  // Build preview payload
  const previewJson = useMemo(() => {
    const coreCount = parseInt(instance.split(' ')[0]) || 4
    const payload = {
      provider:             provider,
      job_title:            jobTitle || '',
      project:              projects.find((p) => p.id === projectId)?.name ?? '',
      instance_type:        instance,
      software_package_ids: softwareLabel ? [softwareLabel] : [],
      force:                false,
      local_upload:         true,
      preemptible:          false,
      autoretry_policy:     retries === '0' ? null : parseInt(retries),
      output_path:          outputFolder,
      environment:          Object.fromEntries(envRows.filter((r) => r.key).map((r) => [r.key, r.value])),
      upload_paths:         uploadPaths,
      scout_frames:         scoutOn ? scoutFrames : null,
      tiles:                tilesOn ? tilesVal : null,
      gpu_enabled:          gpuEnabled,
      cores:                coreCount,
      tasks_data:           taskTemplate
        ? { commands: [taskTemplate] }
        : { errors: ['Invalid task template. Task commands cannot be empty.'] },
    }
    return JSON.stringify(payload, null, 2)
  }, [provider, jobTitle, projectId, projects, instance, softwareLabel, retries, outputFolder, envRows, uploadPaths, scoutOn, scoutFrames, tilesOn, tilesVal, gpuEnabled, taskTemplate])

  const handleSubmit = async () => {
    if (!canSubmit || submitting) return
    setSubmitting(true)
    try {
      if (provider === 'gcp') {
        // Register upload-progress listener before the call (fires via IPC push)
        window.rfApi.gcp.onUploadProgress((pct) => {
          setStatus(`Uploading .blend file… ${pct}%`)
        })
        const machineType = GCP_MACHINE_TYPES[instance] ?? 'n1-standard-4'
        setStatus('Uploading .blend file…')
        const job = await window.rfApi.gcp.submit({
          token:         auth.token,
          blendFilePath: uploadPaths[0],
          title:         jobTitle.trim(),
          frames,
          software:      softwareLabel || 'blender',
          outputFolder,
          machineType,
          preemptible:   true,
          projectId,
        })
        setSubmitted(job.jobNumber)
        setSaved(true)
        setStatus(`Job ${job.jobNumber} submitted → GCP`)
      } else {
        const job = await window.rfApi.jobs.create(auth.token, {
          provider,
          title: jobTitle.trim(), software: softwareLabel, cores: 4,
          gpuCount: gpuEnabled ? 1 : 0, projectId,
          frames, chunkSize, tiles: tilesOn ? tilesVal : null,
          scoutFrames: scoutOn ? scoutFrames : null,
          outputPath: outputFolder, taskTemplate,
        })
        setSubmitted(job.jobNumber)
        setSaved(true)
        setStatus(`Job ${job.jobNumber} submitted — queued`)
      }
    } catch (e) {
      setStatus(`Job submission failed: ${e instanceof Error ? e.message : 'Unknown error'}`)
    } finally {
      setSubmitting(false)
    }
  }

  const doReset = () => {
    setProvider('renderfarm')
    setJobTitle(''); setSoftwareName(''); setSoftwareVersion('')
    setFrames('1-10'); setChunkSize('1')
    setTilesVal('1-9'); setTilesOn(false); setScoutFrames('1'); setScoutOn(true)
    setPlatform('linux'); setGpuEnabled(false); setInstance('4 core, 16GB Mem')
    setRetries('1'); setOutputFolder('/tmp/renders'); setTaskTemplate('')
    setUploadPaths([]); setSelectedPaths(new Set()); setEnvRows([])
    setSaved(false); setSubmitted(null); setCurrentFile(null)
    setStatus('Submission reset')
  }

  // ── Serialise current state → JSON string ─────────────────────────────────
  const serialise = () => JSON.stringify({
    provider,
    jobTitle, projectId, frames, chunkSize, tilesVal, tilesOn,
    scoutFrames, scoutOn, platform, gpuEnabled, instance, retries,
    outputFolder, taskTemplate, softwareName, softwareVersion,
    uploadPaths, envRows,
  }, null, 2)

  // ── Deserialise a JSON string → repopulate all fields ─────────────────────
  const deserialise = (raw: string) => {
    try {
      const d = JSON.parse(raw)
      if (d.provider         !== undefined) setProvider(d.provider)
      if (d.jobTitle         !== undefined) setJobTitle(d.jobTitle)
      if (d.projectId        !== undefined) setProjectId(d.projectId)
      if (d.frames           !== undefined) setFrames(d.frames)
      if (d.chunkSize        !== undefined) setChunkSize(d.chunkSize)
      if (d.tilesVal         !== undefined) setTilesVal(d.tilesVal)
      if (d.tilesOn          !== undefined) setTilesOn(d.tilesOn)
      if (d.scoutFrames      !== undefined) setScoutFrames(d.scoutFrames)
      if (d.scoutOn          !== undefined) setScoutOn(d.scoutOn)
      if (d.platform         !== undefined) setPlatform(d.platform)
      if (d.gpuEnabled       !== undefined) setGpuEnabled(d.gpuEnabled)
      if (d.instance         !== undefined) setInstance(d.instance)
      if (d.retries          !== undefined) setRetries(d.retries)
      if (d.outputFolder     !== undefined) setOutputFolder(d.outputFolder)
      if (d.taskTemplate     !== undefined) setTaskTemplate(d.taskTemplate)
      if (d.softwareName     !== undefined) setSoftwareName(d.softwareName)
      if (d.softwareVersion  !== undefined) setSoftwareVersion(d.softwareVersion)
      if (d.uploadPaths      !== undefined) setUploadPaths(d.uploadPaths)
      if (d.envRows          !== undefined) setEnvRows(d.envRows)
      setSaved(true); setSubmitted(null)
    } catch {
      setStatus('Error: could not parse submission file')
    }
  }

  // ── Load ──────────────────────────────────────────────────────────────────
  const doLoad = async () => {
    const result = await window.rfApi.submission.load()
    if (!result) return
    deserialise(result.content)
    setCurrentFile(result.filePath)
    setStatus(`Loaded: ${result.filePath}`)
  }

  // ── Save (overwrite current file) ─────────────────────────────────────────
  const doSave = async () => {
    if (!currentFile) return
    await window.rfApi.submission.save(currentFile, serialise())
    setSaved(true)
    setStatus(`Saved: ${currentFile}`)
  }

  // ── Save As ───────────────────────────────────────────────────────────────
  const doSaveAs = async () => {
    const name = (jobTitle.trim() || 'submission').replace(/[^a-z0-9_-]/gi, '_')
    const result = await window.rfApi.submission.saveAs(serialise(), `${name}.json`)
    if (!result) return
    setCurrentFile(result.filePath)
    setSaved(true)
    setStatus(`Saved: ${result.filePath}`)
  }

  // ── Load Blender BMW example ───────────────────────────────────────────────
  const doLoadBmw = () => {
    setJobTitle('Blender_bmw')
    setSoftwareName('blender'); setSoftwareVersion('3.6.12')
    setFrames('1-10'); setChunkSize('1')
    setTilesVal('1-9'); setTilesOn(false)
    setScoutFrames('1'); setScoutOn(true)
    setPlatform('linux'); setGpuEnabled(true)
    setInstance('8 core, 32GB Mem'); setRetries('1')
    setOutputFolder('/tmp/renders/bmw')
    setTaskTemplate(
      'blender -b <scene_file> -E CYCLES -o <output_path>/frame.#### -f <chunk_start>'
    )
    setUploadPaths(['/assets/bmw27.blend'])
    setEnvRows([{ id: crypto.randomUUID(), key: 'CYCLES_DEVICE', value: 'CUDA' }])
    setCurrentFile(null); setSaved(false); setSubmitted(null)
    setStatus('Loaded example: Blender_bmw')
  }

  // ── Export Python Script ───────────────────────────────────────────────────
  const doExportPython = async () => {
    const payload = JSON.parse(previewJson)
    const jsonContent = JSON.stringify(payload, null, 2)
    const baseName = (jobTitle.trim() || 'submission').replace(/[^a-z0-9_-]/gi, '_')

    const pyContent = `#!/usr/bin/env python3
"""
Renderfarm Companion — generated submission script
Job: ${jobTitle || 'Untitled'}
Generated: ${new Date().toISOString()}

Usage:
  1. Set your API token:  export RF_TOKEN="your_token_here"
  2. Run:                 python ${baseName}.py

Requires: requests  (pip install requests)
"""
import json, os, sys
import requests

TOKEN    = os.environ.get('RF_TOKEN', '')
API_BASE = 'https://renderfarm-web.vercel.app/api'

if not TOKEN:
    sys.exit('Error: RF_TOKEN environment variable is not set.')

# Load the resolved submission payload
script_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(script_dir, '${baseName}.json')) as f:
    payload = json.load(f)

# Submit the job
response = requests.post(
    f'{API_BASE}/jobs',
    json=payload,
    headers={'Authorization': f'Bearer {TOKEN}'},
    timeout=30,
)

if response.ok:
    data = response.json()
    print(f"✓ Job submitted: #{data.get('jobNumber', data.get('id', '?'))}")
else:
    sys.exit(f'Error {response.status_code}: {response.text}')
`

    const result = await window.rfApi.submission.exportPython(jsonContent, pyContent, baseName)
    if (!result) return
    setStatus(`Exported: ${result.pyPath} + ${result.jsonPath}`)
    window.rfApi.shell.openPath(result.folder)
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: 'general',     label: 'GENERAL'     },
    { id: 'files',       label: 'FILES'       },
    { id: 'software',    label: 'SOFTWARE'    },
    { id: 'environment', label: 'ENVIRONMENT' },
    { id: 'preview',     label: 'PREVIEW'     },
  ]

  return (
    <div className="sk-page">

      {/* ── Important Notice modal ──────────────────────────────────────── */}
      {showNotice && (
        <div className="sk-modal-overlay">
          <div className="sk-modal">
            <h3 className="sk-modal-title">Important Notice!</h3>
            <p className="sk-modal-body">
              The Submission Kit is not a replacement for the native Renderfarm host
              integrations. If you want to submit renders from Maya, Nuke, or Blender,
              you should use the embedded submitter plugins. They save time by taking
              care of asset dependencies and the format of render commands.
            </p>
            <p className="sk-modal-body sk-modal-body--mt">
              This submission kit is provided for lower-level configuration in situations
              where a native plugin is either not available or not configurable to meet
              your circumstances.
            </p>
            <p className="sk-modal-body sk-modal-body--mt">
              For more information on native plugins:{' '}
              <button type="button" className="sk-modal-link"
                onClick={() => window.rfApi.shell.open('http://localhost:3000')}>
                Consult the documentation.
              </button>
            </p>
            <div className="sk-modal-actions">
              <button type="button" className="sk-modal-btn" onClick={() => setShowNotice(false)}>
                CLOSE FOREVER
              </button>
              <button type="button" className="sk-modal-btn" onClick={() => setShowNotice(false)}>
                CLOSE
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div className="sk-header-bar">
        <span className="sk-header-title">Submission Kit</span>
      </div>

      {/* ── Tab strip ───────────────────────────────────────────────────── */}
      <div className="sk-tab-strip">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`sk-tab ${activeTab === t.id ? 'sk-tab--active' : ''}`}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
          </button>
        ))}

        {/* Files toolbar — right side of tab strip, FILES tab only */}
        {activeTab === 'files' && (
          <div className="sk-files-toolbar">
            <button type="button" className="sk-tb-btn" title="Select all"
              onClick={() => setSelectedPaths(new Set(uploadPaths.map((_, i) => i)))}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2"/>
                <polyline points="9 11 12 14 22 4" strokeWidth="2.5"/>
              </svg>
            </button>
            <button type="button" className="sk-tb-btn" title="Deselect all"
              onClick={() => setSelectedPaths(new Set())}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2"/>
                <line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>
              </svg>
            </button>
            <button type="button" className="sk-tb-btn" title="Tag selected">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>
                <line x1="7" y1="7" x2="7.01" y2="7" strokeWidth="3"/>
              </svg>
            </button>
            <button type="button" className="sk-tb-btn" title="Copy paths">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="9" y="9" width="13" height="13" rx="2"/>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
              </svg>
            </button>
            <button type="button" className="sk-tb-btn sk-tb-btn--primary"
              title={provider === 'gcp' ? 'Select .blend file' : 'Add files'}
              onClick={async () => {
                if (provider === 'gcp') {
                  const filePath = await window.rfApi.dialog.pickFile('Select .blend file', ['blend'])
                  if (!filePath) return
                  setUploadPaths([filePath])   // GCP: one scene file only
                  setStatus(`Selected: ${filePath}`)
                } else {
                  const fake = `/assets/scene_${Date.now()}.blend`
                  setUploadPaths((p) => [...p, fake])
                  setStatus(`Added: ${fake}`)
                }
              }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                <line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>
              </svg>
            </button>
          </div>
        )}
      </div>

      {/* ── No-project warning banner ────────────────────────────────────── */}
      {projectsLoaded && projects.length === 0 && (
        <div className="sk-no-project-banner">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16" strokeWidth="3"/>
          </svg>
          <span>
            <strong>No active project.</strong> You must create and activate a project before submitting jobs.{' '}
            Go to <strong>renderfarm.swade-art.com → Admin → Projects</strong> and click <em>+ New Project</em>.
          </span>
        </div>
      )}

      {/* ── Tab content ─────────────────────────────────────────────────── */}
      <div className="sk-body">

        {/* ─── GENERAL ──────────────────────────────────────────────────── */}
        {activeTab === 'general' && (
          <>
            <div className="sk-row">
              <span className="sk-label">Job title:</span>
              <input className="sk-input" type="text" value={jobTitle}
                onChange={(e) => { setJobTitle(e.target.value); setSaved(false) }}
                placeholder="e.g. Shot_042 Beauty Pass" />
              <HelpIcon
                title="Job Title"
                body="A human-readable name for this submission — shows up in the Jobs column on the web dashboard and has no effect on rendering. Use descriptive names like Shot_042_Beauty_v03 to identify jobs at a glance, especially when multiple artists are submitting simultaneously."
              />
            </div>

            <div className="sk-row">
              <span className="sk-label">Renderfarm project:</span>
              <select className="sk-select" title="Renderfarm project" value={projectId}
                onChange={(e) => setProjectId(e.target.value)}>
                <option value="">Select project…</option>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <HelpIcon
                title="Renderfarm Project"
                body={[
                  'Groups submissions under a named project for organizational and financial tracking.',
                  'Cost limits can be set per project on the admin side. Submitting to the wrong project means costs get attributed incorrectly and your budget guardrails may not apply.',
                ]}
              />
            </div>

            <div className="sk-row">
              <span className="sk-label">Frames:</span>
              <input className="sk-input sk-input--short" title="Frame range" placeholder="1-10" value={frames}
                onChange={(e) => setFrames(e.target.value)} />
              <span className="sk-inline-label">Chunk size:</span>
              <input className="sk-input sk-input--xs" title="Chunk size" placeholder="1" value={chunkSize}
                onChange={(e) => setChunkSize(e.target.value)} />
              <HelpIcon
                title="Frames & Chunk Size"
                body={[
                  'Frames: the range to render. Supports arithmetic progressions — e.g. 1-100, or arbitrary sets like 1,7,10-20,30-60x3,1001 1050. Every frame (or chunk) becomes one task on a cloud machine.',
                  'Chunk Size: how many frames one machine handles per task. Increase it when renders are fast (< 5 min) to amortize spin-up overhead. Use chunk size 1 for long renders so a preemption only loses one frame. Use <chunk_start> and <chunk_end> tokens in your Task Template.',
                ]}
              />
            </div>

            <div className="sk-row">
              <span className="sk-label">Tiles:</span>
              <input className="sk-input sk-input--short" title="Tile range e.g. 1-9 for a 3×3 grid" placeholder="1-9" value={tilesVal}
                onChange={(e) => setTilesVal(e.target.value)} disabled={!tilesOn} />
              <Toggle on={tilesOn} onChange={setTilesOn} label="Tiles" />
              {tilesOn && (() => {
                const m = tilesVal.match(/^(\d+)-(\d+)$/)
                const count = m ? parseInt(m[2]) - parseInt(m[1]) + 1 : NaN
                const side  = Number.isInteger(Math.sqrt(count)) ? Math.sqrt(count) : null
                return (
                  <span className="sk-tiles-hint">
                    {!isNaN(count) && count > 0
                      ? side ? `${count} tiles (${side}×${side} grid)` : `${count} tiles`
                      : null}
                  </span>
                )
              })()}
              <HelpIcon
                title="Tiles (Mosaic / Matrix Rendering)"
                body={[
                  'Splits each frame across multiple cloud machines simultaneously. Enter a range like 1-9 for a 3×3 grid. A task is generated for every tile × every frame — so 10 frames × 9 tiles = 90 parallel tasks.',
                  'Use the <tile> token in your Task Template to pass the tile number to your renderer\'s region/crop argument. Each machine renders its own region; you then stitch the tiles in post.',
                ]}
              />
            </div>

            <div className="sk-row">
              <span className="sk-label">Scout frames:</span>
              <input className="sk-input sk-input--short" title="Scout frames" placeholder="1" value={scoutFrames}
                onChange={(e) => setScoutFrames(e.target.value)} disabled={!scoutOn} />
              <Toggle on={scoutOn} onChange={setScoutOn} />
              <HelpIcon
                title="Scout Frames"
                body={[
                  'A safety mechanism — renders a subset of frames first, then holds all others. Only scout tasks start immediately; the rest wait in a holding state.',
                  'Use this to verify render quality before committing the full job cost. If the scout frame reveals a broken texture or wrong camera, kill the job before paying for the rest.',
                ]}
              />
            </div>

            <div className="sk-row">
              <span className="sk-label">Cloud platform:</span>
              <label className="sk-radio-label">
                <input type="radio" name="platform" className="sk-radio"
                  checked={platform === 'linux'} onChange={() => setPlatform('linux')} />
                Linux
              </label>
              <label className="sk-radio-label sk-radio-label--gap">
                <input type="radio" name="platform" className="sk-radio"
                  checked={platform === 'windows'} onChange={() => setPlatform('windows')} />
                Windows
              </label>
              <span className="sk-inline-label sk-inline-label--gap">GPU enabled:</span>
              <Toggle on={gpuEnabled} onChange={setGpuEnabled} />
              <HelpIcon
                title="Cloud Platform & GPU"
                body={[
                  'Selects the OS and GPU availability of cloud instances. Linux is standard and most cost-effective. Windows is available for software that requires it (e.g. certain 3ds Max configurations).',
                  'GPU enabled adds GPU hardware — required for GPU renderers like Redshift, Octane, or GPU-accelerated Arnold, but significantly more expensive. Enabling GPU when not needed wastes budget; forgetting it when needed causes renders to fail or fall back to slow CPU mode.',
                ]}
              />
            </div>

            <div className="sk-row">
              <span className="sk-label">{platform === 'linux' ? 'Linux' : 'Windows'} instances:</span>
              <select className="sk-select sk-select--mid" title="Instance type" value={instance}
                onChange={(e) => setInstance(e.target.value)}>
                {INSTANCE_OPTIONS.map((o) => <option key={o}>{o}</option>)}
              </select>
              <span className="sk-inline-label sk-inline-label--sm-gap">Retries:</span>
              <input className="sk-input sk-input--xs" type="number" min={0} max={5}
                title="Retry count" placeholder="1"
                value={retries} onChange={(e) => setRetries(e.target.value)} />
              <HelpIcon
                title="Instance Type & Retries"
                body={[
                  'Instance Type: the hardware spec of each cloud machine. Too small → tasks fail from out-of-memory (render nodes have no swap). Too large → wasted cost. Run a test frame first to find the efficient minimum.',
                  'Retries: automatically requeues a task if it fails or gets preempted by the cloud provider. Set above 0 to recover from transient failures without manual intervention.',
                ]}
              />
            </div>

            <div className="sk-row">
              <span className="sk-label">Output folder:</span>
              <input className="sk-input" title="Output folder path" placeholder="/tmp/renders" value={outputFolder}
                onChange={(e) => setOutputFolder(e.target.value)} />
              <button type="button" className="sk-folder-btn" title="Browse">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                </svg>
              </button>
              <HelpIcon
                title="Output Folder"
                body={[
                  'The path on the cloud machine where rendered files are written. All output must be below this path — files written anywhere else are lost when the instance shuts down.',
                  'This must match exactly what your renderer\'s -o (or equivalent) argument specifies. None of your uploaded assets may exist below this path.',
                ]}
              />
            </div>

            <div className="sk-row sk-row--top">
              <span className="sk-label">Task template:</span>
              <textarea className="sk-textarea" value={taskTemplate}
                onChange={(e) => setTaskTemplate(e.target.value)}
                placeholder={tilesOn
                  ? 'e.g. render -frame <chunk_start> -tile <tile> -tilesX 3 -tilesY 3 -output /renders/frame.<chunk_start>.tile<tile>.exr'
                  : 'e.g. render -frame <chunk_start>-<chunk_end> -output /renders/frame.####.exr'}
                spellCheck={false} />
              <HelpIcon
                title="Task Template"
                body={[
                  'The actual command-line instruction that runs on every cloud machine. This is the most critical field — a wrong command means 100% task failure.',
                  'Use tokens wrapped in angle brackets to inject per-task values: <chunk_start>, <chunk_end>, <tile>, <scene>, <output_path>. These resolve to the correct values for each individual task at runtime.',
                ]}
              />
            </div>
          </>
        )}

        {/* ─── FILES ────────────────────────────────────────────────────── */}
        {activeTab === 'files' && (
          <div className="sk-files-wrap">
            {/* File list or empty state */}
            {uploadPaths.length === 0 ? (
              <div className="sk-files-empty">
                {provider === 'gcp'
                  ? 'No .blend file selected. Click the folder+ button above to pick your scene file.'
                  : 'No assets selected for upload.'}</div>
            ) : (
              <div className="sk-files-list">
                {uploadPaths.map((path, i) => (
                  <div key={i}
                    className={`sk-file-row ${selectedPaths.has(i) ? 'sk-file-row--selected' : ''}`}
                    onClick={() => setSelectedPaths((s) => {
                      const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n
                    })}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                      <polyline points="14 2 14 8 20 8"/>
                    </svg>
                    <span className="sk-file-path">{path}</span>
                    <button type="button" className="sk-file-del" title="Remove file"
                      onClick={(e) => {
                        e.stopPropagation()
                        setUploadPaths((p) => p.filter((_, j) => j !== i))
                        setSelectedPaths((s) => { const n = new Set(s); n.delete(i); return n })
                      }}>
                      <TrashIcon />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ─── SOFTWARE ─────────────────────────────────────────────────── */}
        {activeTab === 'software' && (
          <>
            <div className="sk-row">
              <span className="sk-label">Software:</span>
              <select className="sk-select" title="Software package" value={softwareName}
                onChange={(e) => { setSoftwareName(e.target.value); setSoftwareVersion('') }}>
                <option value=""></option>
                {SOFTWARE_PACKAGES.map((p) => <option key={p.id} value={p.id}>{p.id}</option>)}
              </select>

              <span className="sk-inline-label sk-inline-label--sm-gap">Version:</span>
              <select className="sk-select" title="Software version" value={softwareVersion}
                onChange={(e) => setSoftwareVersion(e.target.value)}
                disabled={!softwarePkg}>
                <option value=""></option>
                {softwarePkg?.versions.map((v) => <option key={v}>{v}</option>)}
              </select>

              <button type="button" className="sk-icon-btn" title="Clear software"
                onClick={() => { setSoftwareName(''); setSoftwareVersion('') }}>
                <TrashIcon />
              </button>
              <HelpIcon
                title="Software Package & Version"
                body={[
                  'Selects the DCC application and exact version installed on the cloud machines. The version must match what your scene was saved with — rendering with a different version can cause crashes or unexpected output.',
                  'If your required version is not listed, contact your admin to have it added to the farm.',
                ]}
              />
            </div>

            {!softwareName && (
              <div className="sk-empty-tab">
                <p>Select a software package to configure version-specific settings.</p>
              </div>
            )}
            {softwareName && softwareVersion && (
              <div className="sk-sw-info">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2.5">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
                <span>{softwareName}:{softwareVersion} selected</span>
              </div>
            )}
          </>
        )}

        {/* ─── ENVIRONMENT ──────────────────────────────────────────────── */}
        {activeTab === 'environment' && (
          <div className="sk-env-wrap">
            <div className="sk-env-header">
              <span className="sk-env-title">Remote environment overrides</span>
              <HelpIcon
                title="Environment Variables"
                body={[
                  'Key/value pairs injected into the environment of every task process on the cloud machine. Use these to pass renderer licence paths, CUDA device flags, plugin directories, or any setting your software reads from the environment at startup.',
                  'Variables set here override any defaults on the farm. Keys are case-sensitive. Do not store passwords or tokens here — use your project\'s secret store instead.',
                ]}
              />
            </div>

            <div className="sk-env-table">
              {/* Column headers */}
              <div className="sk-env-thead">
                <span className="sk-env-th sk-env-th--key">KEY</span>
                <span className="sk-env-th sk-env-th--val">VALUE</span>
                <span className="sk-env-th sk-env-th--del">
                  <button type="button" className="sk-icon-btn" title="Clear all"
                    onClick={() => setEnvRows([])}>
                    <TrashIcon />
                  </button>
                </span>
              </div>

              {/* Rows */}
              {envRows.map((row) => (
                <div key={row.id} className="sk-env-row">
                  <input className="sk-env-input sk-env-input--key"
                    placeholder="KEY"
                    value={row.key}
                    onChange={(e) => setEnvRows((rs) =>
                      rs.map((r) => r.id === row.id ? { ...r, key: e.target.value } : r)
                    )} />
                  <input className="sk-env-input sk-env-input--val"
                    placeholder="value"
                    value={row.value}
                    onChange={(e) => setEnvRows((rs) =>
                      rs.map((r) => r.id === row.id ? { ...r, value: e.target.value } : r)
                    )} />
                  <button type="button" className="sk-icon-btn" title="Remove row"
                    onClick={() => setEnvRows((rs) => rs.filter((r) => r.id !== row.id))}>
                    <TrashIcon />
                  </button>
                </div>
              ))}
            </div>

            {/* Add row */}
            <button type="button" className="sk-env-add"
              onClick={() => setEnvRows((rs) => [...rs, { id: crypto.randomUUID(), key: '', value: '' }])}>
              + Add variable
            </button>
          </div>
        )}

        {/* ─── PREVIEW ──────────────────────────────────────────────────── */}
        {activeTab === 'preview' && (
          <div className="pv-wrap">
            <div className="pv-block">
              {previewJson.split('\n').map((line, i) => (
                <JsonLine key={i} text={line} />
              ))}
            </div>
          </div>
        )}

        {/* Success banner (all tabs) */}
        {submitted && (
          <div className="sk-success-banner">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            Job <strong>#{submitted}</strong> submitted.{' '}
            <button type="button" className="sk-link"
              onClick={() => window.rfApi.shell.open('https://renderfarm.swade-art.com')}>
              View in dashboard →
            </button>
          </div>
        )}
      </div>

      {/* ── Bottom bar ──────────────────────────────────────────────────── */}
      <div className="sk-bottom-bar">
        <div className="sk-bottom-left" ref={menuRef}>

          <button type="button" className="sk-menu-trigger"
            onClick={() => setMenuOpen((o) => !o)} title="Options">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="5" r="1" fill="currentColor"/>
              <circle cx="12" cy="12" r="1" fill="currentColor"/>
              <circle cx="12" cy="19" r="1" fill="currentColor"/>
            </svg>
          </button>

          <span>
            {currentFile
              ? saved
                ? `Saved — ${currentFile.split(/[\\/]/).pop()}`
                : `Unsaved changes — ${currentFile.split(/[\\/]/).pop()}`
              : saved
              ? 'Submission saved'
              : 'This submission has not been saved'}
          </span>

          {menuOpen && (
            <div className="sk-menu">
              <p className="sk-menu-section">SUBMISSION JSON FILES</p>

              <button type="button" className="sk-menu-item"
                onClick={() => { setMenuOpen(false); doLoad() }}>
                Load
              </button>

              <button
                type="button"
                className={`sk-menu-item ${!currentFile ? 'sk-menu-item--disabled' : ''}`}
                disabled={!currentFile}
                title={!currentFile ? 'No file loaded — use Save As first' : `Save to ${currentFile}`}
                onClick={() => { setMenuOpen(false); doSave() }}>
                Save
              </button>

              <button type="button" className="sk-menu-item"
                onClick={() => { setMenuOpen(false); doSaveAs() }}>
                Save As
              </button>

              <div className="sk-menu-divider" />

              <p className="sk-menu-section">EXAMPLES</p>
              <button type="button" className="sk-menu-item"
                onClick={() => { setMenuOpen(false); doLoadBmw() }}>
                Load Blender_bmw
              </button>

              <div className="sk-menu-divider" />

              <p className="sk-menu-section">EXPORT</p>
              <button type="button" className="sk-menu-item"
                onClick={() => { setMenuOpen(false); doExportPython() }}>
                Python Script
              </button>

              <div className="sk-menu-divider" />

              <button type="button" className="sk-menu-item sk-menu-item--danger"
                onClick={() => { setMenuOpen(false); doReset() }}>
                Reset
              </button>
            </div>
          )}
        </div>

        {/* Provider selector + submit */}
        <div className="sk-bottom-right">
          <div className="sk-provider-group" title="Select render backend">
            <button
              type="button"
              className={`sk-provider-btn ${provider === 'renderfarm' ? 'sk-provider-btn--active' : ''}`}
              onClick={() => setProvider('renderfarm')}
            >
              Renderfarm
            </button>
            <button
              type="button"
              className={`sk-provider-btn ${provider === 'gcp' ? 'sk-provider-btn--active' : ''}`}
              onClick={() => setProvider('gcp')}
            >
              GCP
            </button>
          </div>

          <button type="button" className="sk-submit-btn"
            disabled={!canSubmit || submitting || projects.length === 0}
            title={projects.length === 0 ? 'Create an active project first (Admin → Projects)' : undefined}
            onClick={handleSubmit}>
            {submitting ? 'SUBMITTING…' : provider === 'gcp' ? 'SUBMIT → GCP' : 'SUBMIT'}
          </button>
        </div>
      </div>
    </div>
  )
}
