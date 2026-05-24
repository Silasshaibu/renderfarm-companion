import { useState, useEffect, useMemo, useCallback } from 'react'
import type { AuthState } from '../App'

// ── Shape returned by GET /api/jobs ──────────────────────────────────────────
interface ApiJob {
  id:          string
  jobNumber:   string
  title:       string
  status:      'queued' | 'running' | 'done' | 'failed' | 'holding' | 'uploading'
  frames:      string
  software:    string
  createdAt:   string
  outputs?:    string[]   // frame download URLs — populated by render worker
}

// ── Internal display shape ────────────────────────────────────────────────────
interface RenderJob {
  id:        string
  num:       string
  title:     string
  date:      string
  frames:    string
  software:  string
  status:    'queued' | 'running' | 'done' | 'failed' | 'holding' | 'uploading'
  outputs?:  string[]
}

function mapJob(j: ApiJob): RenderJob {
  return {
    id:       j.id,
    num:      j.jobNumber,
    title:    j.title,
    date:     new Date(j.createdAt).toLocaleString('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    }),
    frames:   j.frames,
    software: j.software,
    status:   j.status,
    outputs:  j.outputs,
  }
}

const STATUS_DOT: Record<string, string> = {
  done:      '#22c55e',
  running:   '#22d3ee',
  failed:    '#ef4444',
  queued:    '#3b82f6',
  holding:   '#f59e0b',
  uploading: '#a78bfa',
}

const STATUS_LABEL: Record<string, string> = {
  done:      'Completed',
  running:   'Running',
  failed:    'Failed',
  queued:    'Queued',
  holding:   'Holding',
  uploading: 'Uploading',
}

const FILTER_OPTIONS = ['Last 5 jobs', 'Last 10 jobs', 'Last 25 jobs', 'Last 50 jobs', 'All jobs']

// ── Component ─────────────────────────────────────────────────────────────────
export default function DownloaderPage({
  auth,
  setStatus,
}: {
  auth:      AuthState
  setStatus: (s: string) => void
}) {
  const [jobs,     setJobs]     = useState<RenderJob[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState('')
  const [search,   setSearch]   = useState('')
  const [filter,   setFilter]   = useState('Last 10 jobs')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // ── Fetch jobs from API ───────────────────────────────────────────────────
  const fetchJobs = useCallback(async (silent = false) => {
    try {
      const raw = (await window.rfApi.jobs.list(auth.token)) as ApiJob[]
      // Newest first
      setJobs([...raw].reverse().map(mapJob))
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
    // Poll every 10 s so running jobs update live
    const timer = setInterval(() => fetchJobs(true), 10_000)
    return () => clearInterval(timer)
  }, [fetchJobs])

  // ── UI helpers ────────────────────────────────────────────────────────────
  const toggle = (id: string) =>
    setExpanded(s => {
      const n = new Set(s)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })

  const filterCount = parseInt(filter) || jobs.length
  const visible = useMemo(() => {
    let list = jobs.slice(0, filterCount)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(j =>
        j.title.toLowerCase().includes(q) ||
        j.num.toLowerCase().includes(q)
      )
    }
    return list
  }, [jobs, search, filterCount])

  const handleRefresh = () => {
    setLoading(true)
    setStatus('Refreshing…')
    fetchJobs()
  }

  const handleDownload = async (job: RenderJob) => {
    if (!job.outputs?.length) {
      setStatus(`Job ${job.num}: no rendered frames available yet`)
      return
    }

    setStatus(`Choosing folder for ${job.num}…`)

    // Listen for per-frame progress updates
    window.rfApi.frames.onProgress(({ count, total }) => {
      setStatus(`Downloading ${job.num}: ${count} / ${total} frames…`)
    })

    const result = await window.rfApi.frames.download(job.outputs, job.num)

    if (result.success) {
      setStatus(`✓ ${result.count} frames saved to ${result.folder}`)
    } else {
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
          <select
            className="dl-filter-select"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          >
            {FILTER_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
          </select>

          <button className="dl-refresh-btn" onClick={handleRefresh} title="Refresh">
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
          const dot    = STATUS_DOT[job.status] ?? '#888'
          const label  = STATUS_LABEL[job.status] ?? job.status

          return (
            <div key={job.id} className="dl-job">

              {/* Collapsed row */}
              <button
                className="dl-job-row"
                onClick={() => toggle(job.id)}
                aria-expanded={isOpen}
              >
                <span className="dl-job-num">{job.num}</span>

                <div className="dl-job-body">
                  <span className="dl-job-title">{job.title}</span>
                  <div className="dl-job-meta">
                    <span className="dl-job-date">{job.date}</span>
                    <span className="dl-job-tag">{job.frames} frames</span>
                    <span className="dl-job-tag">{job.software}</span>
                    <span
                      className="dl-job-dot"
                      style={{ background: dot }}
                      title={label}
                    />
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

              {/* Expanded detail */}
              {isOpen && (
                <div className="dl-job-detail">
                  <div className="dl-detail-grid">
                    <div className="dl-detail-row">
                      <span className="dl-detail-label">Software</span>
                      <span className="dl-detail-val">{job.software}</span>
                    </div>
                    <div className="dl-detail-row">
                      <span className="dl-detail-label">Frames</span>
                      <span className="dl-detail-val">{job.frames}</span>
                    </div>
                    <div className="dl-detail-row">
                      <span className="dl-detail-label">Status</span>
                      <span
                        className={`dl-detail-val dl-status--${job.status}`}
                      >
                        {label}
                      </span>
                    </div>
                    {job.outputs?.length ? (
                      <div className="dl-detail-row">
                        <span className="dl-detail-label">Frames ready</span>
                        <span className="dl-detail-val">{job.outputs.length}</span>
                      </div>
                    ) : job.status === 'done' && (
                      <div className="dl-detail-row">
                        <span className="dl-detail-label">Frames</span>
                        <span className="dl-detail-val" style={{ color: '#8888aa' }}>
                          Awaiting render worker
                        </span>
                      </div>
                    )}
                  </div>

                  <button
                    type="button"
                    className="dl-download-btn"
                    onClick={() => handleDownload(job)}
                    disabled={job.status !== 'done' || !job.outputs?.length}
                    title={
                      job.status === 'holding'
                        ? 'Job is on hold — click Unhold on the dashboard to release it'
                        : job.status !== 'done'
                        ? `Job is ${label.toLowerCase()} — cannot download yet`
                        : !job.outputs?.length
                        ? 'No frames yet — render worker not connected'
                        : 'Download rendered frames to a local folder'
                    }
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                      <polyline points="7 10 12 15 17 10"/>
                      <line x1="12" y1="15" x2="12" y2="3"/>
                    </svg>
                    {job.status === 'done' ? 'Download outputs' : label}
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
