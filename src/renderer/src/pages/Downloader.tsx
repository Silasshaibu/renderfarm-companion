import { useState, useMemo } from 'react'

interface RenderJob {
  id:        string
  num:       string
  title:     string
  date:      string
  projectId: string
  user:      string
  frames:    number
  software:  string
  status:    'completed' | 'running' | 'failed'
}

const MOCK_JOBS: RenderJob[] = [
  { id: 'j161', num: '00161', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_2x4', date: '23rd May 2026, 22:29', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 240, software: 'Blender 3.1.0', status: 'completed' },
  { id: 'j160', num: '00160', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_2x5', date: '23rd May 2026, 22:12', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 300, software: 'Blender 3.1.0', status: 'completed' },
  { id: 'j159', num: '00159', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_2x5', date: '23rd May 2026, 20:48', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 300, software: 'Blender 3.1.0', status: 'completed' },
  { id: 'j158', num: '00158', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_0x7', date: '23rd May 2026, 17:32', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 420, software: 'Blender 3.1.0', status: 'failed' },
  { id: 'j157', num: '00157', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_0x6', date: '23rd May 2026, 17:23', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 360, software: 'Blender 3.1.0', status: 'completed' },
  { id: 'j156', num: '00156', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_0x5', date: '23rd May 2026, 17:00', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 300, software: 'Blender 3.1.0', status: 'completed' },
  { id: 'j155', num: '00155', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_0x4', date: '23rd May 2026, 16:53', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 240, software: 'Blender 3.1.0', status: 'running' },
  { id: 'j154', num: '00154', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_0x3', date: '23rd May 2026, 16:44', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 180, software: 'Blender 3.1.0', status: 'completed' },
  { id: 'j153', num: '00153', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_1x6', date: '23rd May 2026, 16:24', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 360, software: 'Blender 3.1.0', status: 'completed' },
  { id: 'j152', num: '00152', title: 'Blender 3.1.0 Linux Render BoardPromo_FullLine_3x5', date: '23rd May 2026, 15:51', projectId: '81ea28790a27e80eb2f88f76b8f93a09', user: 'Administrator', frames: 300, software: 'Blender 3.1.0', status: 'completed' },
]

const FILTER_OPTIONS = ['Last 5 jobs', 'Last 10 jobs', 'Last 25 jobs', 'Last 50 jobs', 'All jobs']

const STATUS_DOT: Record<RenderJob['status'], string> = {
  completed: '#22c55e',
  running:   '#22d3ee',
  failed:    '#ef4444',
}

export default function DownloaderPage({ setStatus }: { setStatus: (s: string) => void }) {
  const [search,   setSearch]   = useState('')
  const [filter,   setFilter]   = useState('Last 10 jobs')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const toggle = (id: string) =>
    setExpanded((s) => {
      const n = new Set(s)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })

  const filterCount = parseInt(filter) || MOCK_JOBS.length
  const visible = useMemo(() => {
    let jobs = MOCK_JOBS.slice(0, filterCount)
    if (search.trim()) {
      const q = search.toLowerCase()
      jobs = jobs.filter(
        (j) => j.title.toLowerCase().includes(q) || j.num.includes(q) || j.user.toLowerCase().includes(q)
      )
    }
    return jobs
  }, [search, filterCount])

  const handleRefresh = () => {
    setStatus('Refreshing job list…')
    setTimeout(() => setStatus('Job list refreshed'), 1000)
  }

  return (
    <div className="dl-page">

      {/* ── Top bar ────────────────────────────────────────────────────── */}
      <div className="dl-topbar">
        <h2 className="dl-topbar-title">Downloader</h2>

        <input
          type="text"
          className="dl-search"
          placeholder="Find in page…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />

        <div className="dl-topbar-right">
          <select
            className="dl-filter-select"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            {FILTER_OPTIONS.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>

          <button className="dl-refresh-btn" onClick={handleRefresh} title="Refresh">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <polyline points="23 4 23 10 17 10"/>
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
            </svg>
          </button>
        </div>
      </div>

      {/* ── Job list ───────────────────────────────────────────────────── */}
      <div className="dl-list">
        {visible.length === 0 && (
          <div className="dl-empty">No jobs match your search.</div>
        )}

        {visible.map((job) => {
          const isOpen = expanded.has(job.id)
          return (
            <div key={job.id} className="dl-job">
              {/* Collapsed row */}
              <button
                className="dl-job-row"
                onClick={() => toggle(job.id)}
                aria-expanded={isOpen}
              >
                {/* Job number */}
                <span className="dl-job-num">{job.num}</span>

                {/* Body */}
                <div className="dl-job-body">
                  <span className="dl-job-title">{job.title}</span>
                  <div className="dl-job-meta">
                    <span className="dl-job-date">{job.date}</span>
                    <span className="dl-job-tag dl-job-tag--id">{job.projectId}</span>
                    <span className="dl-job-tag">{job.user}</span>
                    {/* Status dot */}
                    <span
                      className="dl-job-dot"
                      style={{ background: STATUS_DOT[job.status] }}
                      title={job.status}
                    />
                  </div>
                </div>

                {/* Chevron */}
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
                      <span className="dl-detail-val" style={{ color: STATUS_DOT[job.status], textTransform: 'capitalize' }}>
                        {job.status}
                      </span>
                    </div>
                    <div className="dl-detail-row">
                      <span className="dl-detail-label">Output path</span>
                      <span className="dl-detail-val dl-detail-mono">/renders/{job.num}/</span>
                    </div>
                  </div>
                  <button
                    className="dl-download-btn"
                    onClick={() => { setStatus(`Downloading outputs for job ${job.num}…`) }}
                    disabled={job.status !== 'completed'}
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                      <polyline points="7 10 12 15 17 10"/>
                      <line x1="12" y1="15" x2="12" y2="3"/>
                    </svg>
                    Download outputs
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
