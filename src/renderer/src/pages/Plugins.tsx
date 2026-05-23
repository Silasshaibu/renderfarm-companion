import { useState } from 'react'

interface Plugin {
  id: string
  name: string
  description: string
  version: string
  installedVersion?: string
  hasUpdate?: boolean
  icon: string
  iconColor: string
  available: boolean
}

const PLUGINS: Plugin[] = [
  {
    id: 'maya', name: 'Renderfarm for Maya',
    description: 'Maya plugin node to manage submissions to the render farm. Supports Maya versions from 2019 to 2025 on all platforms.',
    version: '1.1.2', icon: 'M', iconColor: '#3b82f6', available: true,
  },
  {
    id: 'cinema4d', name: 'Renderfarm for Cinema 4D',
    description: 'A plugin to submit render jobs from Cinema 4D. Tested on versions R21 to R24, Mac and Windows.',
    version: '1.2.2', icon: 'C', iconColor: '#06b6d4', available: true,
  },
  {
    id: 'blender', name: 'Renderfarm for Blender',
    description: 'A plugin to submit render jobs from Blender. Supports Blender 3.x and 4.x on all platforms.',
    version: '0.5.0', installedVersion: '0.3.12', hasUpdate: true, icon: 'B', iconColor: '#f97316', available: true,
  },
  {
    id: 'nuke', name: 'Renderfarm for Nuke',
    description: 'Nuke plugin submitter for the render farm service. Compatible with Nuke 13 and above.',
    version: '0.8.2', icon: 'N', iconColor: '#eab308', available: true,
  },
  {
    id: 'unreal', name: 'Renderfarm for Unreal',
    description: 'Unreal Engine plugin to manage MovieRenderPipeline submissions. Supports Unreal 5.2 and above.',
    version: '1.0.0a7', icon: 'U', iconColor: '#8b5cf6', available: true,
  },
  {
    id: '3dsmax', name: 'Renderfarm for 3ds Max',
    description: '3ds Max plugin submitter for the render farm cloud rendering service.',
    version: '0.7.0', icon: '3', iconColor: '#3b82f6', available: true,
  },
  {
    id: 'houdini', name: 'Renderfarm for Houdini',
    description: 'A ROP to submit work from Houdini to the render cloud. Compatible with Python3 versions of Houdini for all platforms.',
    version: '2.1.0', icon: 'H', iconColor: '#f97316', available: true,
  },
  {
    id: 'katana', name: 'Renderfarm for Katana',
    description: 'Katana plugin node to manage submissions to the render farm. Supports Katana from 5.0v1 and above.',
    version: '1.0.3', icon: 'K', iconColor: '#22d3ee', available: true,
  },
]

export default function PluginsPage() {
  const [installed, setInstalled] = useState<Set<string>>(new Set())
  const [installing, setInstalling] = useState<Set<string>>(new Set())

  const handleInstall = (id: string) => {
    setInstalling((s) => new Set([...s, id]))
    setTimeout(() => {
      setInstalling((s) => { const n = new Set(s); n.delete(id); return n })
      setInstalled((s) => new Set([...s, id]))
    }, 1800)
  }

  return (
    <div className="page-content">
      <div className="plugins-grid">
        {PLUGINS.map((p) => {
          const isInstalled  = installed.has(p.id)
          const isInstalling = installing.has(p.id)
          return (
            <div key={p.id} className="plugin-card">
              {p.hasUpdate && <span className="plugin-update-badge">update</span>}
              <div className="plugin-card-body">
                <div className="plugin-icon" style={{ background: p.iconColor + '22', color: p.iconColor }}>
                  {p.icon}
                </div>
                <div className="plugin-info">
                  <h3 className="plugin-name">{p.name}</h3>
                  <p className="plugin-desc">{p.description}</p>
                </div>
              </div>
              <div className="plugin-card-footer">
                <button className="plugin-info-btn" title="More info">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/>
                    <line x1="12" y1="16" x2="12.01" y2="16" strokeWidth="3" strokeLinecap="round"/>
                  </svg>
                </button>
                {p.installedVersion && !isInstalled && (
                  <span className="plugin-installed-badge">Installed v{p.installedVersion}</span>
                )}
                <div className="plugin-install-group">
                  <button
                    className={`plugin-install-btn ${isInstalled ? 'plugin-install-btn--done' : ''}`}
                    onClick={() => handleInstall(p.id)}
                    disabled={isInstalling || isInstalled}
                  >
                    {isInstalling ? 'Installing…' : isInstalled ? '✓ Installed' : `INSTALL v${p.version}`}
                  </button>
                  {!isInstalled && (
                    <button className="plugin-install-arrow" title="More versions">
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                        <polyline points="6 9 12 15 18 9"/>
                      </svg>
                    </button>
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
