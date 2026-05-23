export default function HelpPage() {
  const open = (url: string) => window.rfApi.shell.open(url)

  return (
    <div className="hp-page">

      {/* Welcome heading */}
      <h2 className="hp-welcome">
        Welcome to Renderfarm Companion{' '}
        <span className="hp-version">version: 1.0.1</span>
      </h2>

      {/* ── Two-column cards ─────────────────────────────────────────────── */}
      <div className="hp-cards">

        {/* Watch intro video */}
        <button
          type="button"
          className="hp-card"
          onClick={() => open('http://localhost:3000/intro')}
        >
          <div className="hp-card-thumb hp-card-thumb--video">
            <div className="hp-play-btn">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <polygon points="5 3 19 12 5 21 5 3"/>
              </svg>
            </div>
          </div>
          <span className="hp-card-label">Watch the introduction video</span>
        </button>

        {/* Visit documentation */}
        <button
          type="button"
          className="hp-card"
          onClick={() => open('http://localhost:3000/docs')}
        >
          <div className="hp-card-thumb hp-card-thumb--docs">
            <div className="hp-docs-mock">
              <div className="hp-docs-bar" />
              <div className="hp-docs-line hp-docs-line--title" />
              <div className="hp-docs-line" />
              <div className="hp-docs-line hp-docs-line--short" />
              <div className="hp-docs-line" />
            </div>
          </div>
          <span className="hp-card-label">Visit the documentation</span>
        </button>

      </div>

      {/* ── Features ─────────────────────────────────────────────────────── */}
      <div className="hp-section">
        <h3 className="hp-section-title">Features</h3>
        <ul className="hp-list">
          <li>
            Install submitters from the{' '}
            <button
              type="button"
              className="hp-inline-link"
              onClick={() => open('http://localhost:3000/plugins')}
            >
              Plugins Page
            </button>
          </li>
          <li>Download finished renders in the <strong>Download Manager</strong></li>
          <li>Experiment with <strong>Submission Kit</strong></li>
        </ul>
      </div>

      {/* ── Release Notes ────────────────────────────────────────────────── */}
      <div className="hp-section">
        <h3 className="hp-section-title">
          Release Notes <span className="hp-rn-version">1.0.1</span>
        </h3>
        <ul className="hp-list">
          <li>
            General improvements.
            <ul className="hp-sublist">
              <li>
                The Companion app now comes bundled with its own embedded version of the
                Renderfarm core libraries. This ensures that the commandline tools are
                always available.
              </li>
              <li>
                Additionally, the Submission Kit uses those core libraries, which means
                you no longer need to manually install them.
              </li>
            </ul>
          </li>
          <li>
            Plugins page improvements.
            <ul className="hp-sublist">
              <li>You can now set a custom plugin install location from the app bar.</li>
              <li>
                Controls have been added to show beta plugins and plugins available for
                all platforms.
              </li>
            </ul>
          </li>
          <li>
            Downloader improvements.
            <ul className="hp-sublist">
              <li>Downloader performance has been improved for large jobs.</li>
              <li>The downloader page displays the last 10 jobs by default.</li>
              <li>
                New natural language job filters have been added. You can now choose jobs
                to display by a range of job IDs, by time ranges, or by the most recent
                jobs.
              </li>
            </ul>
          </li>
          <li>
            Submission Kit improvements.
            <ul className="hp-sublist">
              <li>
                The Submission Kit now understands which cloud you are on and adjusts its
                controls appropriately. For example, a cloud with no concept of Spot
                instances will have that option removed automatically.
              </li>
              <li>
                The list of instance types, projects, software, and available target
                operating system are now correctly updated when you switch accounts.
              </li>
            </ul>
          </li>
        </ul>
      </div>

    </div>
  )
}
