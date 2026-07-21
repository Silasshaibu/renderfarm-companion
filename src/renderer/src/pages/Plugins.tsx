import { useState, useRef, useEffect } from 'react'

// Sets --icon-color and --icon-bg via ref to avoid inline style lint warning
function PluginIcon({ letter, color }: { letter: string; color: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    ref.current?.style.setProperty('--icon-color', color)
    ref.current?.style.setProperty('--icon-bg',    color + '22')
  }, [color])
  return <div ref={ref} className="plugin-icon">{letter}</div>
}

interface PluginVersion {
  label:       string   // display label e.g. "v7.0.1 — Blender 3.x / 4.x / 5.x"
  version:     string   // short version e.g. "7.0.1"
  downloadUrl: string   // direct download URL for the zip
}

interface Plugin {
  id:               string
  name:             string
  description:      string
  version:          string          // latest version string
  installedVersion?: string         // currently installed version (if any)
  hasUpdate?:        boolean
  icon:             string
  iconColor:        string
  available:        boolean
  versions?:        PluginVersion[] // dropdown options — only for plugins with multiple builds
}

const BLENDER_VERSIONS: PluginVersion[] = [
  {
    label:       'v2.1.3 — Blender 3.x / 4.x / 5.x (Recommended)',
    version:     '2.1.3',
    // NOTE: was previously 'renderfarm_submitter_v7.zip', which does not exist as a
    // release asset (404) — the real asset attached to the latest release is named
    // renderfarm_submitter_v2.1.3.zip. Fixed here; keep this in sync with whatever
    // gets attached to the "latest" GitHub release going forward.
    downloadUrl: 'https://github.com/Silasshaibu/renderfarm-companion/releases/latest/download/renderfarm_submitter_v2.1.3.zip',
  },
]

const MAYA_VERSIONS: PluginVersion[] = [
  {
    label:       'v1.1.0 — Maya 2019–2025 (all platforms)',
    version:     '1.1.0',
    downloadUrl: 'https://github.com/Silasshaibu/renderfarm-companion/releases/latest/download/renderfarm_submitter_maya_v1.1.0.zip',
  },
]

const PLUGINS: Plugin[] = [
  {
    id: 'maya', name: 'Renderfarm for Maya',
    description: 'Maya plugin node to manage submissions to the render farm. Supports Maya versions from 2019 to 2025 on all platforms.',
    version: '1.1.0', icon: 'M', iconColor: '#3b82f6', available: true,
    versions: MAYA_VERSIONS,
  },
  {
    id: 'cinema4d', name: 'Renderfarm for Cinema 4D',
    description: 'A plugin to submit render jobs from Cinema 4D. Tested on versions R21 to R24, Mac and Windows.',
    version: '0.0.0', icon: 'C', iconColor: '#06b6d4', available: false,
  },
  {
    id: 'blender', name: 'Renderfarm for Blender',
    description: 'A plugin to submit render jobs from Blender. Supports Blender 3.x, 4.x and 5.x on all platforms.',
    version: '2.1.3',
    icon: 'B', iconColor: '#f97316', available: true,
    versions: BLENDER_VERSIONS,
  },
  {
    id: 'nuke', name: 'Renderfarm for Nuke',
    description: 'Nuke plugin submitter for the render farm service. Compatible with Nuke 13 and above.',
    version: '0.0.0', icon: 'N', iconColor: '#eab308', available: false,
  },
  {
    id: 'unreal', name: 'Renderfarm for Unreal',
    description: 'Unreal Engine plugin to manage MovieRenderPipeline submissions. Supports Unreal 5.2 and above.',
    version: '0.0.0', icon: 'U', iconColor: '#8b5cf6', available: false,
  },
  {
    id: '3dsmax', name: 'Renderfarm for 3ds Max',
    description: '3ds Max plugin submitter for the render farm cloud rendering service.',
    version: '0.0.0', icon: '3', iconColor: '#3b82f6', available: false,
  },
  {
    id: 'houdini', name: 'Renderfarm for Houdini',
    description: 'A ROP to submit work from Houdini to the render cloud. Compatible with Python3 versions of Houdini for all platforms.',
    version: '0.0.0', icon: 'H', iconColor: '#f97316', available: false,
  },
  {
    id: 'katana', name: 'Renderfarm for Katana',
    description: 'Katana plugin node to manage submissions to the render farm. Supports Katana from 5.0v1 and above.',
    version: '0.0.0', icon: 'K', iconColor: '#22d3ee', available: false,
  },
]

interface PluginsPageProps {
  pluginsPath?: string
  onRefresh?:   () => void
}

export default function PluginsPage({ pluginsPath: _pluginsPath, onRefresh: _onRefresh }: PluginsPageProps = {}) {
  const [installed,    setInstalled]    = useState<Record<string, string>>({})
  const [installing,   setInstalling]   = useState<Set<string>>(new Set())
  const [dropdownOpen, setDropdownOpen] = useState<string | null>(null)
  const [noticeFor,    setNoticeFor]    = useState<string | null>(null)  // plugin id showing "not yet released"
  const dropdownRef = useRef<HTMLDivElement>(null)
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const showNotice = (id: string) => {
    setNoticeFor(id)
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = setTimeout(() => setNoticeFor(null), 3000)
  }

  // Close dropdown when clicking outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!dropdownRef.current?.contains(e.target as Node)) {
        setDropdownOpen(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleInstall = (plugin: Plugin, ver?: PluginVersion) => {
    const targetVer = ver ?? (plugin.versions?.[0])
    setDropdownOpen(null)

    if (targetVer?.downloadUrl) {
      // Open the download URL in the browser — user saves the zip, then installs in Blender
      window.rfApi.shell.open(targetVer.downloadUrl)
      // Mark as installing then installed
      setInstalling(s => new Set([...s, plugin.id]))
      setTimeout(() => {
        setInstalling(s => { const n = new Set(s); n.delete(plugin.id); return n })
        setInstalled(s => ({ ...s, [plugin.id]: targetVer.version }))
      }, 1000)
    } else {
      // Generic install simulation for plugins without a real download yet
      setInstalling(s => new Set([...s, plugin.id]))
      setTimeout(() => {
        setInstalling(s => { const n = new Set(s); n.delete(plugin.id); return n })
        setInstalled(s => ({ ...s, [plugin.id]: plugin.version }))
      }, 1800)
    }
  }

  return (
    <div className="page-content">
      <div className="plugins-grid">
        {PLUGINS.map((p) => {
          const installedVer = installed[p.id]
          const isInstalling = installing.has(p.id)
          const isUpToDate   = installedVer === p.version
          const isOpen       = dropdownOpen === p.id

          return (
            <div key={p.id} className="plugin-card">
              {p.hasUpdate && !isUpToDate && (
                <span className="plugin-update-badge">update</span>
              )}

              <div className="plugin-card-body">
                <PluginIcon letter={p.icon} color={p.iconColor} />
                <div className="plugin-info">
                  <h3 className="plugin-name">{p.name}</h3>
                  <p className="plugin-desc">{p.description}</p>
                </div>
              </div>

              <div className="plugin-card-footer">
                <button type="button" className="plugin-info-btn" title="More info">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="12" y1="8" x2="12" y2="12"/>
                    <line x1="12" y1="16" x2="12.01" y2="16" strokeWidth="3" strokeLinecap="round"/>
                  </svg>
                </button>

                {/* Installed badge */}
                {installedVer && (
                  <span className="plugin-installed-badge">
                    Installed v{installedVer}
                  </span>
                )}

                {/* Install button + version dropdown */}
                <div className="plugin-install-group" ref={isOpen ? dropdownRef : undefined}>

                  {/* "Not yet released" notice — shown for 3s after clicking unavailable plugin */}
                  {noticeFor === p.id && (
                    <span className="plugin-not-released">Not yet released</span>
                  )}

                  <button
                    type="button"
                    className={`plugin-install-btn ${isUpToDate ? 'plugin-install-btn--done' : ''} ${!p.available ? 'plugin-install-btn--unavailable' : ''}`}
                    onClick={() => p.available ? handleInstall(p) : showNotice(p.id)}
                    disabled={isInstalling || isUpToDate}
                    title={!p.available ? 'Not yet released' : isUpToDate ? `v${p.version} is already installed` : `Install v${p.version}`}
                  >
                    {isInstalling
                      ? 'Downloading…'
                      : isUpToDate
                      ? `✓ v${p.version}`
                      : `INSTALL v${p.version}`}
                  </button>

                  {/* Version selector arrow — only shown for plugins with multiple builds */}
                  {p.versions && !isUpToDate && (
                    <div className="plugin-version-wrap">
                      <button
                        type="button"
                        className={`plugin-install-arrow ${isOpen ? 'plugin-install-arrow--open' : ''}`}
                        title="Select version"
                        onClick={() => setDropdownOpen(isOpen ? null : p.id)}
                      >
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none"
                          stroke="currentColor" strokeWidth="2.5">
                          <polyline points="6 9 12 15 18 9"/>
                        </svg>
                      </button>

                      {isOpen && (
                        <div className="plugin-version-menu" role="menu">
                          <div className="plugin-version-menu-title">
                            Select version
                          </div>
                          {p.versions.map(v => (
                            <button
                              key={v.version}
                              type="button"
                              role="menuitem"
                              className="plugin-version-item"
                              onClick={() => handleInstall(p, v)}
                            >
                              <span className="plugin-version-label">{v.label}</span>
                              {v.version === p.version && (
                                <span className="plugin-version-latest">latest</span>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
