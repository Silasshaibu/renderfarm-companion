import { useState, useEffect } from 'react'
import type { Page } from '../App'

type UpdateState = 'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'error'

// Injected by electron-vite via package.json version field
const APP_VERSION: string = import.meta.env.VITE_APP_VERSION ?? '1.0.1'

interface Props {
  activePage: Page
  onNavigate: (p: Page) => void
  onLogout: () => void
  user: { email: string }
}

interface NavItem { id: Page; label: string; icon: React.ReactNode }

const IconSignIn = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/>
    <polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/>
  </svg>
)
const IconPlugins = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
  </svg>
)
const IconDownload = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
    <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
  </svg>
)
const IconKit = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
  </svg>
)
const IconHelp = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <circle cx="12" cy="12" r="10"/>
    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
    <line x1="12" y1="17" x2="12.01" y2="17" strokeWidth="3"/>
  </svg>
)
const IconDashboard = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
    <rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>
  </svg>
)

const NAV: NavItem[] = [
  { id: 'plugins',        label: 'Plugins',        icon: <IconPlugins />   },
  { id: 'downloader',     label: 'Downloader',     icon: <IconDownload />  },
  { id: 'submission-kit', label: 'Submission Kit', icon: <IconKit />       },
  { id: 'help',           label: 'Help & Resources', icon: <IconHelp />    },
]

export default function Sidebar({ activePage, onNavigate, onLogout, user }: Props) {
  const [updateState,   setUpdateState]   = useState<UpdateState>('idle')
  const [updateVersion, setUpdateVersion] = useState<string>('')
  const [dlProgress,    setDlProgress]    = useState(0)

  // Register push-event listeners once on mount
  useEffect(() => {
    window.rfApi.updater.onAvailable((v) => { setUpdateVersion(v); setUpdateState('available') })
    window.rfApi.updater.onNotAvailable(() => setUpdateState('idle'))
    window.rfApi.updater.onProgress((pct) => { setDlProgress(pct); setUpdateState('downloading') })
    window.rfApi.updater.onDownloaded(() => setUpdateState('ready'))
    window.rfApi.updater.onError(() => setUpdateState('error'))
  }, [])

  const handleUpdateBtn = async () => {
    if (updateState === 'idle' || updateState === 'error') {
      setUpdateState('checking')
      await window.rfApi.updater.check()
      // result comes back via the push events above
    } else if (updateState === 'available') {
      setUpdateState('downloading')
      setDlProgress(0)
      window.rfApi.updater.download()
    } else if (updateState === 'ready') {
      window.rfApi.updater.install()
    }
  }

  const updateBtnLabel = () => {
    switch (updateState) {
      case 'checking':    return 'Checking…'
      case 'available':   return `Download ${updateVersion}`
      case 'downloading': return `Downloading ${dlProgress}%`
      case 'ready':       return 'Restart to update'
      case 'error':       return 'Retry update check'
      default:            return 'Check for updates'
    }
  }

  const updateBtnClass = () => {
    if (updateState === 'ready')  return 'sidebar-update-btn sidebar-update-btn--ready'
    if (updateState === 'error')  return 'sidebar-update-btn sidebar-update-btn--error'
    return 'sidebar-update-btn'
  }

  return (
    <aside className="sidebar">
      {/* Brand */}
      <div className="sidebar-brand">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" strokeWidth="2">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
        </svg>
        <span className="sidebar-brand-text">Renderfarm</span>
      </div>

      {/* User row */}
      <button type="button" className="sidebar-user-row" onClick={onLogout} title="Click to sign out">
        <div className="sidebar-avatar">
          {user.email.charAt(0).toUpperCase()}
        </div>
        <div className="sidebar-user-info">
          <span className="sidebar-user-email">{user.email}</span>
          <span className="sidebar-sign-out">Sign out</span>
        </div>
        <IconSignIn />
      </button>

      {/* Nav */}
      <nav className="sidebar-nav">
        {NAV.map((item) => (
          <button
            type="button"
            key={item.id}
            className={`sidebar-nav-item ${activePage === item.id ? 'sidebar-nav-item--active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            <span className="sidebar-nav-icon">{item.icon}</span>
            {item.label}
          </button>
        ))}

        <div className="sidebar-divider" />

        <button
          type="button"
          className="sidebar-nav-item"
          onClick={() => window.rfApi.shell.open('http://localhost:3000/login')}
        >
          <span className="sidebar-nav-icon"><IconDashboard /></span>
          Web dashboard
        </button>
      </nav>

      {/* Footer */}
      <div className="sidebar-footer">
        <p className="sidebar-footer-brand">RENDERFARM</p>
        <p className="sidebar-version">Version: {APP_VERSION}</p>
        <p className="sidebar-version">Core: 8.1.0</p>

        {/* Download progress bar — visible while downloading */}
        {updateState === 'downloading' && (
          <div className="sidebar-update-track">
            {/* CSS var drives the width — no inline style needed */}
            <div
              className="sidebar-update-bar"
              // @ts-expect-error custom CSS property
              style={{ '--pct': `${dlProgress}%` }}
            />
          </div>
        )}

        <button
          type="button"
          className={updateBtnClass()}
          onClick={handleUpdateBtn}
          disabled={updateState === 'downloading' || updateState === 'checking'}
        >
          {updateBtnLabel()}
        </button>
      </div>
    </aside>
  )
}
