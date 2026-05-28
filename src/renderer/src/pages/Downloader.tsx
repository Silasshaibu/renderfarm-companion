import { useState, useEffect, useMemo, useCallback, useRef } from 'react'

// ── Format a list of 1-based frame numbers into a compact range string ─────────
// e.g. [2, 5,6,7,8,9,10, 210] → "0002, 0005-0010, 0210"
function formatFrameRanges(nums: number[]): string {
  if (!nums.length) return ''
  const sorted = [...nums].sort((a, b) => a - b)
  const ranges: string[] = []
  let start = sorted[0], end = sorted[0]

  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] === end + 1) {
      end = sorted[i]
    } else {
      ranges.push(start === end
        ? String(start).padStart(4, '0')
        : `${String(start).padStart(4, '0')}-${String(end).padStart(4, '0')}`)
      start = end = sorted[i]
    }
  }
  ranges.push(start === end
    ? String(start).padStart(4, '0')
    : `${String(start).padStart(4, '0')}-${String(end).padStart(4, '0')}`)
  return ranges.join(', ')
}

// ── Progress bar — sets --pct via ref to avoid inline style lint warning ──────
function ProgressBar({ pct, active }: { pct: number; active: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    ref.current?.style.setProperty('--pct', `${pct}%`)
  }, [pct])
  return (
    <div className="dl-progress-wrap">
      <div
        ref={ref}
        className={`dl-progress-bar ${active ? 'dl-progress-bar--active' : 'dl-progress-bar--idle'}`}
      />
    </div>
  )
}
import type { AuthState } from '../App'

// ── Shape returned by GET /api/jobs ──────────────────────────────────────────
interface ApiJob {
  id:          string
  jobNumber:   string
  title:       string
  status:      string   // queued | pending | running | holding | uploading |
                        // upload_pending | sync_pending | syncing | sync_failed |
                        // success | downloaded | failed | killed | preempted
  frames:      string
  software:    string
  createdAt:   string
  outputs?:    string[]   // frame download URLs — populated by render worker
  outputPath?: string     // local output folder set in the Blender addon
}

// ── Internal display shape ────────────────────────────────────────────────────
interface RenderJob {
  id:          string
  num:         string
  title:       string
  date:        string
  createdAt:   string   // ISO string — kept for date-based filtering
  frames:      string
  software:    string
  status:      string
  outputs?:    string[]
  outputPath?: string
}

function mapJob(j: ApiJob): RenderJob {
  return {
    id:         j.id,
    num:        j.jobNumber.replace(/^RF-/i, ''),
    title:      j.title,
    date:       new Date(j.createdAt).toLocaleString('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    }),
    createdAt:  j.createdAt,
    frames:     j.frames,
    software:   j.software,
    status:     j.status,
    outputs:    j.outputs,
    outputPath: j.outputPath,
  }
}



const FILTER_OPTIONS = [
  'Last 5 jobs',
  'Last 10 jobs',
  'Last 25 jobs',
  'Last 50 jobs',
  'Last 3 months',
  'Last 12 months',
  'All Time',
]

// ── Parse filter string → predicate + slice ───────────────────────────────────
function applyFilter(jobs: RenderJob[], filter: string): RenderJob[] {
  const f = filter.trim()
  const now = Date.now()

  // "Last N jobs"
  const countMatch = f.match(/^last\s+(\d+)\s+jobs?$/i)
  if (countMatch) return jobs.slice(0, parseInt(countMatch[1]))

  // "Last N months"
  const monthMatch = f.match(/^last\s+(\d+)\s+months?$/i)
  if (monthMatch) {
    const ms = parseInt(monthMatch[1]) * 30 * 24 * 3600 * 1000
    return jobs.filter(j => now - new Date(j.createdAt).getTime() <= ms)
  }

  // "All Time"
  if (/^all\s*time$/i.test(f)) return jobs

  // Job-number range: "00010 to 00015"  or  "10 to 15"  or  "10 - 15"
  const rangeMatch = f.match(/^(\d+)\s*(?:to|-)\s*(\d+)$/i)
  if (rangeMatch) {
    const lo = parseInt(rangeMatch[1])
    const hi = parseInt(rangeMatch[2])
    return jobs.filter(j => { const n = parseInt(j.num); return n >= lo && n <= hi })
  }

  // Single job number: "00012"
  if (/^\d+$/.test(f)) {
    const target = parseInt(f)
    return jobs.filter(j => parseInt(j.num) === target)
  }

  // Fallback — treat as "Last 10 jobs"
  return jobs.slice(0, 10)
}

// ── Filter combo — text input + preset dropdown ───────────────────────────────
function FilterCombo({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div ref={ref} className="dl-filter-wrap">
      <div className="dl-filter-field">
        <input
          className="dl-filter-input"
          aria-label="Filter jobs"
          title="Filter jobs"
          value={value}
          onChange={e => onChange(e.target.value)}
          onFocus={() => setOpen(true)}
          placeholder="Filter…"
        />
        <button
          type="button"
          className="dl-filter-arrow"
          title="Show filter presets"
          tabIndex={-1}
          onClick={() => setOpen(o => !o)}
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </button>
      </div>

      {open && (
        <div className="dl-filter-menu" role="listbox">
          {FILTER_OPTIONS.map(opt => (
            <button
              key={opt}
              type="button"
              role="option"
              className={`dl-filter-item ${value === opt ? 'dl-filter-item--active' : ''}`}
              onClick={() => { onChange(opt); setOpen(false) }}
            >
              {opt}
            </button>
          ))}
          <div className="dl-filter-hint">
            Or type: a job number <em>00012</em>, or a range <em>10 to 20</em>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function DownloaderPage({
  auth,
  setStatus,
}: {
  auth:      AuthState
  setStatus: (s: string) => void
}) {
  const [jobs,       setJobs]       = useState<RenderJob[]>([])
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState('')
  const [search,     setSearch]     = useState('')
  const [filter,     setFilter]     = useState('Last 10 jobs')
  const [expanded,   setExpanded]   = useState<Set<string>>(new Set())
  // Per-job overrideable output paths and download progress
  const [pathOverrides, setPathOverrides] = useState<Record<string, string>>({})
  const [dlProgress,    setDlProgress]    = useState<Record<string, { count: number; total: number }>>({})
  const [dlFailed,      setDlFailed]      = useState<Record<string, number[]>>({})   // failed frame numbers (1-based)
  const [dlFailLog,     setDlFailLog]     = useState<string | null>(null)             // job id with open fail-log popover
  const [dlExisting,    setDlExisting]    = useState<Record<string, number>>({})
  const [dlActive,      setDlActive]      = useState<Set<string>>(new Set())
  // Kebab menu open state (stores job id of open menu, or null)
  const [kebabOpen,     setKebabOpen]     = useState<string | null>(null)

  // ── Fetch jobs from API ───────────────────────────────────────────────────
  const fetchJobs = useCallback(async (silent = false) => {
    try {
      const raw = (await window.rfApi.jobs.list(auth.token)) as ApiJob[]
      // API already returns newest first (ORDER BY created_at DESC)
      setJobs(raw.map(mapJob))
      setError('')
      if (!silent) setStatus('Job list refreshed')
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load jobs'
      setError(msg)
      setStatus(`Error: ${msg}`)
    } finally {
      setLoading(false)
    }
  }, [auth.token, setStatus])

  useEffect(() => {
    fetchJobs()
    // Poll every 15 s; also refresh outputs for any running jobs so counts stay current
    const timer = setInterval(async () => {
      await fetchJobs(true)
      setJobs(current => {
        const running = current.filter(j => j.status === 'running')
        if (running.length) {
          Promise.allSettled(
            running.map(j =>
              window.rfApi.jobs.refreshOutputs(auth.token, j.num).catch(() => {})
            )
          ).then(() => fetchJobs(true))
        }
        return current
      })
    }, 15_000)
    return () => clearInterval(timer)
  }, [fetchJobs, auth.token])

  // ── UI helpers ────────────────────────────────────────────────────────────
  const toggle = (id: string) => {
    setExpanded(s => {
      const n = new Set(s)
      if (n.has(id)) {
        n.delete(id)
      } else {
        n.add(id)
        const job = jobs.find(j => j.id === id)
        if (!job) return n

        // If job has completed tasks but no outputs yet, ask the server to scan GCS now
        const hasCompletedTasks = !['pending','queued','uploading','upload_pending','sync_pending','syncing'].includes(job.status)
        if (hasCompletedTasks && !job.outputs?.length) {
          window.rfApi.jobs.refreshOutputs(auth.token, job.num).then(() => {
            fetchJobs(true)
          }).catch(() => { /* best-effort */ })
        }

        // Check how many frames are already on disk when expanding
        if (job.outputs?.length) {
          const folder = pathOverrides[id] !== undefined ? pathOverrides[id] : (job.outputPath ?? '')
          if (folder) {
            window.rfApi.frames.countExisting(folder, job.frames).then(({ existing }) => {
              setDlExisting(e => ({ ...e, [id]: existing }))
            })
          }
        }
      }
      return n
    })
  }

  const visible = useMemo(() => {
    let list = applyFilter(jobs, filter)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(j =>
        j.title.toLowerCase().includes(q) ||
        j.num.toLowerCase().includes(q)
      )
    }
    return list
  }, [jobs, search, filter])

  const TERMINAL = new Set(['success', 'downloaded', 'failed', 'killed', 'done'])

  // Re-count frames on disk for a single job and update dlExisting
  const recountDisk = useCallback((job: RenderJob, overridePath: string) => {
    const folder = overridePath !== undefined ? overridePath : (job.outputPath ?? '')
    if (!folder) return
    window.rfApi.frames.countExisting(folder, job.frames).then(({ existing }) => {
      setDlExisting(e => ({ ...e, [job.id]: existing }))
    }).catch(() => {})
  }, [])

  const handleRefresh = async () => {
    setLoading(true)
    setStatus('Refreshing…')
    await fetchJobs()

    // Clear stale download progress for jobs not currently downloading so
    // the disk recount (below) takes effect instead of the last download count
    setDlProgress(p => {
      const n = { ...p }
      for (const jobId of Object.keys(n)) {
        if (!dlActive.has(jobId)) delete n[jobId]
      }
      return n
    })

    // For every non-terminal job, hit refresh-outputs so the signed-URL list is current
    setJobs(current => {
      const nonTerminal = current.filter(j => !TERMINAL.has(j.status))
      if (nonTerminal.length) {
        Promise.allSettled(
          nonTerminal.map(j =>
            window.rfApi.jobs.refreshOutputs(auth.token, j.num).catch(() => {})
          )
        ).then(() => fetchJobs(true))
      }

      // Re-count frames on disk for all jobs that have an output path
      for (const job of current) {
        const folder = pathOverrides[job.id] !== undefined ? pathOverrides[job.id] : (job.outputPath ?? '')
        if (folder) recountDisk(job, folder)
      }

      return current
    })
  }

  const handleDownload = async (job: RenderJob) => {
    if (!job.outputs?.length) {
      setStatus(`Job ${job.num}: no rendered frames available yet`)
      return
    }

    const overridePath = pathOverrides[job.id]
    const resolvedPath = overridePath !== undefined ? overridePath : job.outputPath

    setStatus(resolvedPath
      ? `Downloading ${job.num} to ${resolvedPath}…`
      : `Choosing folder for ${job.num}…`
    )

    setDlActive(s => { const n = new Set(s); n.add(job.id); return n })
    setDlProgress(p => ({ ...p, [job.id]: { count: 0, total: job.outputs!.length } }))
    setDlFailed(f => { const n = { ...f }; delete n[job.id]; return n })

    // Listen for per-frame progress updates
    window.rfApi.frames.onProgress(({ jobNumber, count, failedNums, total }) => {
      if (jobNumber === job.num) {
        setDlProgress(p => ({ ...p, [job.id]: { count, total } }))
        if (failedNums?.length) setDlFailed(f => ({ ...f, [job.id]: failedNums }))
        setStatus(`Downloading ${job.num}: ${count} / ${total} frames…`)
      }
    })

    const result = await window.rfApi.frames.download(job.outputs, job.num, resolvedPath || undefined, auth.token)

    setDlActive(s => { const n = new Set(s); n.delete(job.id); return n })

    if (result.success) {
      const saved     = result.count   ?? 0
      const failNums  = result.failedNums ?? []
      setDlProgress(p => ({ ...p, [job.id]: { count: saved, total: saved + failNums.length } }))
      if (failNums.length) {
        setDlFailed(f => ({ ...f, [job.id]: failNums }))
        setStatus(`✓ ${saved} frames saved, ${failNums.length} failed — ${result.folder}`)
      } else {
        setDlFailed(f => { const n = { ...f }; delete n[job.id]; return n })
        setStatus(`✓ ${saved} frames saved to ${result.folder}`)
      }
      // Refresh existing count so the counter updates after download
      if (result.folder && job.outputs?.length) {
        window.rfApi.frames.countExisting(result.folder, job.frames).then(({ existing }) => {
          setDlExisting(e => ({ ...e, [job.id]: existing }))
        })
      }
    } else {
      setDlProgress(p => { const n = { ...p }; delete n[job.id]; return n })
      setStatus(`Download cancelled or failed: ${result.error ?? 'unknown error'}`)
    }
  }


  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="dl-page">

      {/* Top bar */}
      <div className="dl-topbar">
        <h2 className="dl-topbar-title">Downloader</h2>

        <input
          type="text"
          className="dl-search"
          placeholder="Find in page…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />

        <div className="dl-topbar-right">
          {/* Filter combo — type a job number, range (e.g. "10 to 20"), or pick a preset */}
          <FilterCombo value={filter} onChange={setFilter} />

          <button type="button" className="dl-refresh-btn" onClick={handleRefresh} title="Refresh">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <polyline points="23 4 23 10 17 10"/>
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
            </svg>
          </button>
        </div>
      </div>

      {/* Job list */}
      <div className="dl-list">
        {loading && (
          <div className="dl-empty">Loading jobs from Renderfarm…</div>
        )}

        {!loading && error && (
          <div className="dl-empty dl-empty--error">{error}</div>
        )}

        {!loading && !error && visible.length === 0 && (
          <div className="dl-empty">
            No jobs yet. Submit a render from Blender to get started.
          </div>
        )}

        {!loading && visible.map(job => {
          const isOpen = expanded.has(job.id)

          return (
            <div key={job.id} className="dl-job">

              {/* Collapsed row */}
              <button
                type="button"
                className="dl-job-row"
                onClick={() => { toggle(job.id); setKebabOpen(null) }}
              >
                <span className="dl-job-num">{job.num}</span>

                <div className="dl-job-body">
                  <span className="dl-job-title">{job.title}</span>
                  <div className="dl-job-meta">
                    <span className="dl-job-date">{job.date}</span>
                    <span className="dl-job-tag">{job.frames} frames</span>
                    <span className="dl-job-tag">{job.software}</span>
                  </div>
                </div>

                <svg
                  className={`dl-chevron ${isOpen ? 'dl-chevron--open' : ''}`}
                  width="14" height="14" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
                >
                  <polyline points="6 9 12 15 18 9"/>
                </svg>
              </button>

              {/* Expanded detail — Conductor-style */}
              {isOpen && (() => {
                const activePath  = pathOverrides[job.id] !== undefined
                                      ? pathOverrides[job.id]
                                      : (job.outputPath ?? '')
                const prog        = dlProgress[job.id]
                const isActive    = dlActive.has(job.id)
                const canDownload = !!job.outputs?.length
                const availFrames = job.outputs?.length ?? 0   // frames with signed URLs ready
                const existing    = dlExisting[job.id] ?? 0
                // While downloading use live progress; before/after use existing count
                const progCount   = prog ? prog.count : existing
                const progTotal   = prog ? prog.total : availFrames
                const progPct     = progTotal > 0 ? (progCount / progTotal) * 100 : 0
                const failNums    = dlFailed[job.id] ?? []
                const failCount   = failNums.length
                const isKebab     = kebabOpen === job.id
                const isFailLog   = dlFailLog === job.id

                return (
                  <div className="dl-job-detail">
                    <div className="dl-output-row">
                      {/* Output path label */}
                      <span className="dl-output-label">Output path</span>

                      {/* Path input + folder browse */}
                      <div className="dl-output-field">
                        <input
                          aria-label="Output folder path"
                          className="dl-output-input"
                          type="text"
                          value={activePath}
                          placeholder="Choose output folder…"
                          onChange={e => setPathOverrides(p => ({ ...p, [job.id]: e.target.value }))}
                        />
                        <button
                          type="button"
                          className="dl-output-browse"
                          title="Browse for folder"
                          onClick={async () => {
                            const picked = await window.rfApi.dialog.pickFolder(activePath || undefined)
                            if (picked) setPathOverrides(p => ({ ...p, [job.id]: picked }))
                          }}
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M10 4H2v16h20V6H12l-2-2z"/>
                          </svg>
                        </button>
                      </div>

                      {/* Progress bar */}
                      <ProgressBar pct={progPct} active={!!prog} />
                      <div className="dl-progress-counts">
                        <span className="dl-progress-label">{progCount} / {progTotal}</span>
                        {failCount > 0 && (
                          <span className="dl-failed-label">
                            {failCount} failed download{failCount > 1 ? 's' : ''}
                          </span>
                        )}
                      </div>

                      {/* Three-dot kebab menu */}
                      <div className="dl-kebab-wrap">
                        <button
                          type="button"
                          className={`dl-kebab-btn ${isKebab ? 'dl-kebab-btn--open' : ''}`}
                          title="More options"
                          onClick={e => { e.stopPropagation(); setKebabOpen(isKebab ? null : job.id) }}
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                            <circle cx="12" cy="5"  r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/>
                          </svg>
                        </button>

                        {isKebab && (
                          <div className="dl-kebab-menu" role="menu">
                            <button
                              type="button"
                              role="menuitem"
                              className="dl-kebab-item"
                              onClick={() => { setKebabOpen(null); handleRefresh() }}
                            >
                              Refresh all
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              className="dl-kebab-item"
                              onClick={() => {
                                setPathOverrides(p => { const n = { ...p }; delete n[job.id]; return n })
                                setKebabOpen(null)
                              }}
                            >
                              Reset output path
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              className="dl-kebab-item"
                              disabled={!activePath}
                              onClick={async () => {
                                setKebabOpen(null)
                                if (activePath) await window.rfApi.shell.openPath(activePath)
                              }}
                            >
                              View in finder
                            </button>
                          </div>
                        )}
                      </div>

                      {/* Failed-frames log button — only when failures exist */}
                      {failCount > 0 && (
                        <div className="dl-faillog-wrap">
                          <button
                            type="button"
                            className={`dl-faillog-btn ${isFailLog ? 'dl-faillog-btn--open' : ''}`}
                            title="View failed frames"
                            onClick={e => {
                              e.stopPropagation()
                              setDlFailLog(isFailLog ? null : job.id)
                              setKebabOpen(null)
                            }}
                          >
                            {/* Scroll / list icon */}
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                              stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                              <line x1="8"  y1="6"  x2="21" y2="6"/>
                              <line x1="8"  y1="12" x2="21" y2="12"/>
                              <line x1="8"  y1="18" x2="21" y2="18"/>
                              <line x1="3"  y1="6"  x2="3.01" y2="6"  strokeWidth="3"/>
                              <line x1="3"  y1="12" x2="3.01" y2="12" strokeWidth="3"/>
                              <line x1="3"  y1="18" x2="3.01" y2="18" strokeWidth="3"/>
                            </svg>
                          </button>

                          {isFailLog && (() => {
                            const rangeText = formatFrameRanges(failNums)
                            const saveTxt = () => {
                              const lines = [
                                `Failed frames for job ${job.num} — ${job.title}`,
                                `Total failed: ${failCount}`,
                                '',
                                rangeText,
                                '',
                                'Individual frame numbers:',
                                ...failNums.map(n => String(n).padStart(4, '0')),
                              ].join('\n')
                              const blob = new Blob([lines], { type: 'text/plain' })
                              const url  = URL.createObjectURL(blob)
                              const a    = document.createElement('a')
                              a.href     = url
                              a.download = `failed-frames-${job.num}.txt`
                              a.click()
                              URL.revokeObjectURL(url)
                            }
                            return (
                              <div className="dl-faillog-popover" role="dialog">
                                <div className="dl-faillog-header">
                                  <span className="dl-faillog-title">
                                    {failCount} failed frame{failCount > 1 ? 's' : ''}
                                  </span>
                                  <button
                                    type="button"
                                    className="dl-faillog-save"
                                    title="Save as .txt"
                                    onClick={saveTxt}
                                  >
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                                      stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                      <polyline points="7 10 12 15 17 10"/>
                                      <line x1="12" y1="15" x2="12" y2="3"/>
                                    </svg>
                                    Save .txt
                                  </button>
                                </div>
                                <p className="dl-faillog-ranges">{rangeText}</p>
                                <div className="dl-faillog-nums">
                                  {failNums.map(n => (
                                    <span key={n} className="dl-faillog-chip">
                                      {String(n).padStart(4, '0')}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )
                          })()}
                        </div>
                      )}

                      {/* Download button */}
                      <button
                        type="button"
                        className={`dl-download-btn ${isActive ? 'dl-download-btn--active' : ''}`}
                        onClick={() => handleDownload(job)}
                        disabled={!canDownload || isActive}
                        title={
                          !canDownload
                            ? 'No frames ready yet — waiting for first frame to complete'
                            : isActive ? 'Download in progress…'
                            : availFrames > 0 && !['success','downloaded'].includes(job.status)
                              ? `Download ${availFrames} completed frame${availFrames > 1 ? 's' : ''} (job still running)`
                              : 'Download rendered frames'
                        }
                      >
                        {isActive ? 'Downloading…' : 'DOWNLOAD'}
                      </button>
                    </div>
                  </div>
                )
              })()}
            </div>
          )
        })}
      </div>
    </div>
  )
}
