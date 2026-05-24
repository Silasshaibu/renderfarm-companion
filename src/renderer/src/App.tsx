import { useState } from 'react'
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

export default function App() {
  const [auth,        setAuth]        = useState<AuthState | null>(null)
  const [activePage,  setActivePage]  = useState<Page>('signin')
  const [statusMsg,   setStatusMsg]   = useState('Download queue initialized')

  const handleLogin = (a: AuthState) => {
    setAuth(a)
    setActivePage('plugins')
    setStatusMsg('Signed in as ' + a.user.email)
  }

  const handleLogout = () => {
    setAuth(null)
    setActivePage('signin')
    setStatusMsg('Signed out')
  }

  if (!auth) {
    return <SignInPage onLogin={handleLogin} />
  }

  const renderPage = () => {
    switch (activePage) {
      case 'plugins':        return <PluginsPage />
      case 'downloader':     return <DownloaderPage auth={auth} setStatus={setStatusMsg} />
      case 'submission-kit': return <SubmissionKitPage auth={auth} setStatus={setStatusMsg} />
      case 'help':           return <HelpPage />
      default:               return <PluginsPage />
    }
  }

  return (
    <div className="app-shell">
      <Sidebar
        activePage={activePage}
        onNavigate={setActivePage}
        onLogout={handleLogout}
        user={auth.user}
      />

      <div className="app-content">
        {/* Top path bar */}
        <div className="app-topbar">
          <span className="app-topbar-path">
            {activePage === 'plugins'        && 'Plugins'}
            {activePage === 'downloader'     && 'Downloader'}
            {activePage === 'submission-kit' && 'Submission Kit'}
            {activePage === 'help'           && 'Help & Resources'}
          </span>
          <div className="app-topbar-actions">
            <button
              title="Open web dashboard"
              className="topbar-icon-btn"
              onClick={() => window.rfApi.shell.open('https://renderfarm-web.vercel.app')}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
                <rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>
              </svg>
            </button>
            <button
              title="Open web dashboard in browser"
              className="topbar-icon-btn"
              onClick={() => window.rfApi.shell.open('https://renderfarm-web.vercel.app')}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"/>
                <line x1="2" y1="12" x2="22" y2="12"/>
                <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
              </svg>
            </button>
          </div>
        </div>

        {/* Page content */}
        <div className="app-page">{renderPage()}</div>

        {/* Status bar */}
        <div className="app-statusbar">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/>
            <line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6" strokeWidth="3" strokeLinecap="round"/>
            <line x1="3" y1="12" x2="3.01" y2="12" strokeWidth="3" strokeLinecap="round"/>
            <line x1="3" y1="18" x2="3.01" y2="18" strokeWidth="3" strokeLinecap="round"/>
          </svg>
          <span>{new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} {new Date().toLocaleTimeString('en-US', { hour12: false })}: {statusMsg}</span>
        </div>
      </div>
    </div>
  )
}
