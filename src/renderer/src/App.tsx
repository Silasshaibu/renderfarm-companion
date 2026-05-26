import { useState, useCallback, useRef, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import SignInPage from './pages/SignIn'
import PluginsPage from './pages/Plugins'
import DownloaderPage from './pages/Downloader'
import SubmissionKitPage from './pages/SubmissionKit'
import HelpPage from './pages/Help'

export type Page = 'signin' | 'plugins' | 'downloader' | 'submission-kit' | 'help'

export interface AuthState {
  token: string
  user: { id: string; email: string; isAdmin: boolean }
}

interface LogEntry { ts: string; msg: string }

function nowTs() {
  const d = new Date()
  return `${d.toISOString().slice(0, 10)} ${d.toLocaleTimeString('en-US', { hour12: false })}`
}

const DEFAULT_PLUGINS_PATH = 'C:\\Users\\Administrator\\Renderfarm'

export default function App() {
  const [auth,          setAuth]          = useState<AuthState | null>(null)
  const [activePage,    setActivePage]    = useState<Page>('signin')
  const [statusMsg,     setStatusMsg]     = useState('Download queue initialized')
  const [logEntries,    setLogEntries]    = useState<LogEntry[]>([
    { ts: nowTs(), msg: 'Download queue initialized' },
  ])
  const [logOpen,       setLogOpen]       = useState(false)
  const [pluginsPath,   setPluginsPath]   = useState(DEFAULT_PLUGINS_PATH)
  // Plugins topbar popover state
  const [topbarPopover, setTopbarPopover] = useState<'pre-release' | 'env-vars' | 'path-info' | null>(null)
  const logEndRef    = useRef<HTMLDivElement>(null)
  const popoverRef   = useRef<HTMLDivElement>(null)

  const pushStatus = useCallback((msg: string) => {
    setStatusMsg(msg)
    setLogEntries(prev => [...prev, { ts: nowTs(), msg }])
  }, [])

  useEffect(() => {
    if (logOpen) logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logEntries, logOpen])

  // Close popover on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!popoverRef.current?.contains(e.target as Node)) setTopbarPopover(null)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleLogin = (a: AuthState) => {
    setAuth(a)
    setActivePage('plugins')
    pushStatus('Signed in as ' + a.user.email)
  }

  const handleLogout = () => {
    setAuth(null)
    setActivePage('signin')
    pushStatus('Signed out')
  }

  if (!auth) return <SignInPage onLogin={handleLogin} />

  const renderPage = () => {
    switch (activePage) {
      case 'plugins':        return <PluginsPage pluginsPath={pluginsPath} onRefresh={() => pushStatus('Plugin info refreshed from GitHub')} />
      case 'downloader':     return <DownloaderPage auth={auth} setStatus={pushStatus} />
      case 'submission-kit': return <SubmissionKitPage auth={auth} setStatus={pushStatus} />
      case 'help':           return <HelpPage />
      default:               return <PluginsPage pluginsPath={pluginsPath} onRefresh={() => pushStatus('Plugin info refreshed from GitHub')} />
    }
  }

  // ── Plugins-specific topbar ────────────────────────────────────────────────
  const PluginsTopbar = () => (
    <>
      {/* Path bar — click to show info popover */}
      <div className="plugins-path-bar-wrap" ref={topbarPopover === 'path-info' ? popoverRef : undefined}>
        <button
          type="button"
          className={`plugins-path-bar ${topbarPopover === 'path-info' ? 'plugins-path-bar--active' : ''}`}
          onClick={() => setTopbarPopover(p => p === 'path-info' ? null : 'path-info')}
          title="Plugins location info"
        >
          {pluginsPath}
        </button>
        {topbarPopover === 'path-info' && (
          <div className="topbar-popover plugins-path-popover">
            <p className="topbar-popover-title">Plugins Location:</p>
            <p className="topbar-popover-path">{pluginsPath}</p>
            <p className="topbar-popover-body">
              The location where plugins are installed. The cards below refer to tools installed in this location.
            </p>
          </div>
        )}
      </div>

      <div className="plugins-topbar-actions" ref={topbarPopover !== 'path-info' ? popoverRef : undefined}>
        {/* Browse for Plugins Location */}
        <button
          type="button"
          className="topbar-icon-btn"
          title="Browse for Plugins Location"
          onClick={async () => {
            const picked = await window.rfApi.dialog.pickFolder(pluginsPath)
            if (picked) { setPluginsPath(picked); pushStatus(`Plugins location set to ${picked}`) }
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M10 4H2v16h20V6H12l-2-2z"/>
          </svg>
        </button>

        {/* Reset Plugins Location */}
        <button
          type="button"
          className="topbar-icon-btn"
          title="Reset Plugins Location"
          onClick={() => { setPluginsPath(DEFAULT_PLUGINS_PATH); pushStatus('Plugins location reset to default') }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
            <polyline points="9 22 9 12 15 12 15 22"/>
          </svg>
        </button>

        {/* Refresh plugin info */}
        <button
          type="button"
          className="topbar-icon-btn"
          title="Refresh plugin info from GitHub and PyPi"
          onClick={() => pushStatus('Refreshing plugin info from GitHub and PyPi…')}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <polyline points="23 4 23 10 17 10"/>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
          </svg>
        </button>

        {/* Enable pre-releases */}
        <div className="topbar-popover-wrap">
          <button
            type="button"
            className={`topbar-icon-btn ${topbarPopover === 'pre-release' ? 'topbar-icon-btn--active' : ''}`}
            title="Enable plugin pre-releases"
            onClick={() => setTopbarPopover(p => p === 'pre-release' ? null : 'pre-release')}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/>
              <path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/>
              <path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/>
              <path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>
            </svg>
          </button>
          {topbarPopover === 'pre-release' && (
            <div className="topbar-popover">
              <p className="topbar-popover-title">Enable plugin pre-releases</p>
              <p className="topbar-popover-body">
                Pre-release versions of Renderfarm plugins provide bleeding edge features and custom
                requests that may take longer to appear in stable versions.
              </p>
              <p className="topbar-popover-body">
                Pre-releases have the letters <code>rc</code> in the version string. For example:{' '}
                <code>1.2.3rc4</code>. If you experience issues while using a pre-release, please
                let us know by sending an email to{' '}
                <strong>support@renderfarm.swade-art.com</strong>.
              </p>
            </div>
          )}
        </div>

        {/* Edit installation variables */}
        <div className="topbar-popover-wrap">
          <button
            type="button"
            className={`topbar-icon-btn ${topbarPopover === 'env-vars' ? 'topbar-icon-btn--active' : ''}`}
            title="Edit installation variables"
            onClick={() => setTopbarPopover(p => p === 'env-vars' ? null : 'env-vars')}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="12" cy="12" r="10"/>
              <line x1="2" y1="12" x2="22" y2="12"/>
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
            </svg>
          </button>
          {topbarPopover === 'env-vars' && (
            <div className="topbar-popover topbar-popover--left">
              <p className="topbar-popover-title">Edit installation variables</p>
              <p className="topbar-popover-body">
                Set environment variables you would like to be in effect during plugin installation.
              </p>
              <p className="topbar-popover-body">
                Click the info button on a plugin card for information on variables that are used by
                the <code>post_install</code> script.
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  )

  return (
    <div className="app-shell">
      <Sidebar
        activePage={activePage}
        onNavigate={setActivePage}
        onLogout={handleLogout}
        user={auth.user}
      />

      <div className="app-content">
        {/* Top bar */}
        <div className="app-topbar">
          <span className="app-topbar-path">
            {activePage === 'plugins'        && 'Plugins'}
            {activePage === 'downloader'     && 'Downloader'}
            {activePage === 'submission-kit' && 'Submission Kit'}
            {activePage === 'help'           && 'Help & Resources'}
          </span>

          {activePage === 'plugins' ? <PluginsTopbar /> : (
            <div className="app-topbar-actions">
              <button
                type="button"
                title="Open web dashboard"
                className="topbar-icon-btn"
                onClick={() => window.rfApi.shell.open('https://renderfarm.swade-art.com')}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10"/>
                  <line x1="2" y1="12" x2="22" y2="12"/>
                  <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                </svg>
              </button>
            </div>
          )}
        </div>

        {/* Page content */}
        <div className="app-page">{renderPage()}</div>

        {/* Log viewer overlay */}
        {logOpen && (
          <div className="log-overlay">
            <div className="log-header">
              <span className="log-title">Log viewer</span>
              <button type="button" className="log-close" title="Close" onClick={() => setLogOpen(false)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            </div>
            <div className="log-body">
              {logEntries.map((e, i) => (
                <div key={i} className="log-line">
                  <span className="log-ts">[{e.ts}]</span>
                  <span className={`log-msg ${e.msg.startsWith('✓') || e.msg.toLowerCase().includes('signed in') ? 'log-msg--ok' : ''}`}>
                    {e.msg}
                  </span>
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          </div>
        )}

        {/* Status bar */}
        <div className="app-statusbar">
          <button type="button" className="statusbar-log-btn" title="Open log viewer" onClick={() => setLogOpen(o => !o)}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/>
              <line x1="8" y1="18" x2="21" y2="18"/>
              <line x1="3" y1="6"  x2="3.01" y2="6"  strokeWidth="3" strokeLinecap="round"/>
              <line x1="3" y1="12" x2="3.01" y2="12" strokeWidth="3" strokeLinecap="round"/>
              <line x1="3" y1="18" x2="3.01" y2="18" strokeWidth="3" strokeLinecap="round"/>
            </svg>
          </button>
          <span className="statusbar-text">
            {new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}{' '}
            {new Date().toLocaleTimeString('en-US', { hour12: false })}: {statusMsg}
          </span>
        </div>
      </div>
    </div>
  )
}
