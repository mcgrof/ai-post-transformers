// AI Post Transformers Admin Dashboard
// Cloudflare Worker with inline HTML

import { ADMIN_RELEASE_TAG } from './release.js';
import {
  getAdminIdentity,
  generateSystemdService,
  generateSystemdTimer,
  generateEnvFile,
  generateInstallCommands,
} from './systemd.js';

const GITHUB_REPO = 'mcgrof/ai-post-transformers';
const PODCAST_DOMAIN = 'https://podcast.do-not-panic.com';
const PUBLISH_JOB_PROGRESS_STEPS = ['publish', 'viz', 'cover', 'site', 'verify'];

// ============================================================================
// STYLES - Dark theme matching dash.do-not-panic.com
// ============================================================================
const styles = `
:root {
  --bg-primary: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #21262d;
  --bg-hover: #30363d;
  --border-color: #30363d;
  --text-primary: #e6edf3;
  --text-secondary: #8b949e;
  --text-muted: #6e7681;
  --accent: #58a6ff;
  --accent-hover: #79b8ff;
  --success: #3fb950;
  --warning: #d29922;
  --danger: #f85149;
  --purple: #a371f7;
  --gradient-start: #58a6ff;
  --gradient-end: #a371f7;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.6;
  min-height: 100vh;
}

a {
  color: var(--accent);
  text-decoration: none;
  transition: color 0.2s;
}

a:hover {
  color: var(--accent-hover);
}

.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 1.5rem;
}

/* Header */
header {
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  padding: 1rem 0;
  position: sticky;
  top: 0;
  z-index: 100;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
}

.brand-group {
  display: flex;
  align-items: center;
  gap: 0.9rem;
  flex-wrap: wrap;
}

.logo {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-weight: 600;
  font-size: 1.25rem;
}

.logo-icon {
  width: 36px;
  height: 36px;
  background: linear-gradient(135deg, var(--gradient-start), var(--gradient-end));
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.25rem;
}

.release-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.22rem 0.55rem;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: pointer;
  user-select: none;
  transition: border-color 0.2s, color 0.2s, background 0.2s;
}

button.release-chip {
  appearance: none;
  -webkit-appearance: none;
  font: inherit;
}

.release-chip:hover {
  border-color: var(--accent);
  color: var(--text-primary);
}

nav {
  display: flex;
  gap: 0.25rem;
  flex-wrap: wrap;
}

nav a {
  padding: 0.5rem 1rem;
  border-radius: 6px;
  color: var(--text-secondary);
  font-size: 0.875rem;
  font-weight: 500;
  transition: all 0.2s;
}

nav a:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

nav a.active {
  background: var(--bg-tertiary);
  color: var(--accent);
}

/* Main content */
main {
  padding: 2rem 0;
  min-height: calc(100vh - 140px);
}

h1 {
  font-size: 1.75rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
}

h2 {
  font-size: 1.25rem;
  font-weight: 600;
  margin-bottom: 1rem;
  color: var(--text-primary);
}

.page-header {
  margin-bottom: 2rem;
}

.page-header p {
  color: var(--text-secondary);
}

/* Cards */
.card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1rem;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1rem;
  gap: 1rem;
}

.card-title {
  font-size: 1.125rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

.card-subtitle {
  color: var(--text-secondary);
  font-size: 0.875rem;
}

/* Stats grid */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}

.stat-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 1.5rem;
  position: relative;
  overflow: hidden;
}

.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--gradient-start), var(--gradient-end));
}

.stat-value {
  font-size: 2.5rem;
  font-weight: 700;
  line-height: 1;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, var(--text-primary), var(--text-secondary));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.stat-label {
  color: var(--text-secondary);
  font-size: 0.875rem;
  font-weight: 500;
}

.stat-icon {
  position: absolute;
  right: 1rem;
  top: 50%;
  transform: translateY(-50%);
  font-size: 3rem;
  opacity: 0.1;
}

/* Quick links */
.quick-links {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1rem;
}

.quick-link {
  display: flex;
  align-items: center;
  gap: 1rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 1.25rem;
  transition: all 0.2s;
}

.quick-link:hover {
  border-color: var(--accent);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(88, 166, 255, 0.15);
}

.quick-link-icon {
  width: 48px;
  height: 48px;
  background: var(--bg-tertiary);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.5rem;
}

.quick-link-text h3 {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

.quick-link-text p {
  color: var(--text-secondary);
  font-size: 0.813rem;
}

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 1rem;
  border-radius: 6px;
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  border: none;
  transition: all 0.2s;
}

.btn-primary {
  background: var(--accent);
  color: white;
}

.btn-primary:hover {
  background: var(--accent-hover);
}

.btn-success {
  background: var(--success);
  color: white;
}

.btn-success:hover {
  background: #46c35f;
}

.btn-danger {
  background: transparent;
  color: var(--danger);
  border: 1px solid var(--danger);
}

.btn-danger:hover {
  background: var(--danger);
  color: white;
}

.btn-secondary {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
}

.btn-secondary:hover {
  background: var(--bg-hover);
}

.btn-sm {
  padding: 0.375rem 0.75rem;
  font-size: 0.75rem;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Forms */
.form-group {
  margin-bottom: 1.25rem;
}

label {
  display: block;
  margin-bottom: 0.5rem;
  font-weight: 500;
  font-size: 0.875rem;
}

input[type="text"],
input[type="url"],
textarea,
select {
  width: 100%;
  padding: 0.75rem 1rem;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 0.875rem;
  transition: border-color 0.2s;
}

input:focus,
textarea:focus,
select:focus {
  outline: none;
  border-color: var(--accent);
}

textarea {
  resize: vertical;
  min-height: 120px;
  font-family: inherit;
}

/* Draft list */
.draft-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.draft-item {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 1.5rem;
  transition: border-color 0.2s;
}

.draft-item:hover {
  border-color: var(--bg-hover);
}

.draft-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1rem;
  flex-wrap: wrap;
  gap: 1rem;
}

.draft-title {
  font-size: 1.125rem;
  font-weight: 600;
}

.draft-meta {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  margin-bottom: 0.75rem;
}

.draft-meta span {
  color: var(--text-secondary);
  font-size: 0.813rem;
  display: flex;
  align-items: center;
  gap: 0.375rem;
}

.draft-description {
  color: var(--text-secondary);
  font-size: 0.875rem;
  margin-bottom: 1rem;
  line-height: 1.5;
}

.draft-description .card-sources,
.draft-desc-wrap .card-sources {
  margin-top: 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border-color);
  font-size: 0.82rem;
  color: var(--text-secondary);
  line-height: 1.75;
}

.draft-description .card-sources a,
.draft-desc-wrap .card-sources a {
  color: var(--accent);
  word-break: break-all;
  text-decoration: none;
}

.draft-description .card-sources a:hover,
.draft-desc-wrap .card-sources a:hover {
  text-decoration: underline;
}

.draft-actions {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

/* Audio player */
.audio-player {
  width: 100%;
  margin: 1rem 0;
  border-radius: 8px;
  background: var(--bg-tertiary);
}

audio {
  width: 100%;
  height: 40px;
}

audio::-webkit-media-controls-panel {
  background: var(--bg-tertiary);
}

/* Queue items */
.queue-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.queue-item {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  overflow: hidden;
  transition: border-color 0.2s;
}

.queue-item:hover {
  border-color: var(--bg-hover);
}

.queue-item-header {
  padding: 1.25rem;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
}

.queue-item-main {
  flex: 1;
  min-width: 0;
}

.queue-item-title {
  font-weight: 600;
  margin-bottom: 0.375rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.queue-item-source {
  color: var(--text-secondary);
  font-size: 0.813rem;
}

.queue-item-score {
  background: linear-gradient(135deg, var(--gradient-start), var(--gradient-end));
  color: white;
  padding: 0.375rem 0.75rem;
  border-radius: 20px;
  font-weight: 600;
  font-size: 0.875rem;
  white-space: nowrap;
}

.queue-item-body {
  padding: 0 1.25rem 1.25rem;
  display: none;
  border-top: 1px solid var(--border-color);
}

.queue-item.expanded .queue-item-body {
  display: block;
  padding-top: 1.25rem;
}

.queue-item-abstract {
  color: var(--text-secondary);
  font-size: 0.875rem;
  line-height: 1.6;
  margin-bottom: 1rem;
}

.score-breakdown {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1rem;
}

.score-item {
  background: var(--bg-tertiary);
  padding: 0.75rem;
  border-radius: 8px;
  font-size: 0.813rem;
}

.score-item-label {
  color: var(--text-secondary);
  margin-bottom: 0.25rem;
}

.score-item-value {
  font-weight: 600;
}

/* Badges */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.25rem 0.625rem;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 500;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.badge-success {
  background: rgba(63, 185, 80, 0.15);
  color: var(--success);
}

.badge-warning {
  background: rgba(210, 153, 34, 0.15);
  color: var(--warning);
}

.badge-danger {
  background: rgba(248, 81, 73, 0.15);
  color: var(--danger);
}

.badge-purple {
  background: rgba(163, 113, 247, 0.15);
  color: var(--purple);
}

.badge-blue {
  background: rgba(88, 166, 255, 0.15);
  color: var(--accent);
}

.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}

/* Submissions */
.submission-item {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 1rem 1.25rem;
  margin-bottom: 0.75rem;
}

.submission-url {
  word-break: break-all;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.813rem;
  color: var(--accent);
  margin-bottom: 0.5rem;
}

.submission-time {
  color: var(--text-muted);
  font-size: 0.75rem;
}

/* Issues */
.issue-item {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 1.25rem;
  margin-bottom: 0.75rem;
  transition: border-color 0.2s;
}

.issue-item:hover {
  border-color: var(--bg-hover);
}

.issue-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  margin-bottom: 0.75rem;
}

.issue-title {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.issue-title a {
  color: var(--text-primary);
}

.issue-title a:hover {
  color: var(--accent);
}

.issue-number {
  color: var(--text-muted);
  font-weight: normal;
}

.issue-labels {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.issue-label {
  padding: 0.125rem 0.5rem;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 500;
}

.issue-meta {
  color: var(--text-secondary);
  font-size: 0.813rem;
}

.issue-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

/* Empty state */
.empty-state {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--text-secondary);
}

.empty-state-icon {
  font-size: 3rem;
  margin-bottom: 1rem;
  opacity: 0.5;
}

.empty-state h3 {
  margin-bottom: 0.5rem;
  color: var(--text-primary);
}

/* Loading */
.loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 3rem;
  color: var(--text-secondary);
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--border-color);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: 0.75rem;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Toast notifications */
.toast-container {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.toast {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 1rem 1.25rem;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  display: flex;
  align-items: center;
  gap: 0.75rem;
  animation: slideIn 0.3s ease;
  max-width: 350px;
}

.toast-success {
  border-left: 3px solid var(--success);
}

.toast-error {
  border-left: 3px solid var(--danger);
}

@keyframes slideIn {
  from {
    transform: translateX(100%);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 1rem;
}

.modal {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 1.5rem;
  max-width: 500px;
  width: 100%;
  max-height: 90vh;
  overflow-y: auto;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.25rem;
}

.modal-title {
  font-size: 1.125rem;
  font-weight: 600;
}

.modal-close {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1.5rem;
  cursor: pointer;
  padding: 0;
  line-height: 1;
}

.modal-close:hover {
  color: var(--text-primary);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  margin-top: 1.5rem;
}

/* Responsive */
@media (max-width: 768px) {
  .header-content {
    flex-direction: column;
    align-items: flex-start;
  }

  nav {
    width: 100%;
    overflow-x: auto;
  }

  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .stat-value {
    font-size: 2rem;
  }

  .draft-header {
    flex-direction: column;
  }

  .draft-actions {
    width: 100%;
  }

  .draft-actions .btn {
    flex: 1;
    justify-content: center;
  }

  h1 {
    font-size: 1.5rem;
  }
}

@media (max-width: 480px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }

  .container {
    padding: 0 1rem;
  }

  main {
    padding: 1.5rem 0;
  }
}

/* Footer */
footer {
  background: var(--bg-secondary);
  border-top: 1px solid var(--border-color);
  padding: 1.5rem 0;
  margin-top: auto;
}

.footer-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
  font-size: 0.813rem;
  color: var(--text-secondary);
}

.footer-links {
  display: flex;
  gap: 1.5rem;
}

/* Expand icon */
.expand-icon {
  transition: transform 0.2s;
  color: var(--text-muted);
}

.queue-item.expanded .expand-icon {
  transform: rotate(180deg);
}
`;

// ============================================================================
// HTML TEMPLATES
// ============================================================================
function baseHTML(title, content, activePage) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${title} - AI Post Transformers Admin (${ADMIN_RELEASE_TAG})</title>
  <style>${styles}
    .conference-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 1rem; }
    .conference-card { display: block; padding: 1.25rem; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; text-decoration: none; color: inherit; transition: border-color 0.2s; }
    .conference-card:hover { border-color: var(--accent); }
    .conf-badge { display: inline-block; background: #c9302c; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.5px; margin-bottom: 8px; }
    .conference-card h3 { margin: 4px 0; font-size: 1.1rem; }
    .conference-card p { color: var(--text-secondary); font-size: 0.813rem; margin: 4px 0; }
    .conf-count { color: var(--accent); font-size: 0.813rem; font-weight: 600; }
</style>
</head>
<body>
  <header>
    <div class="container">
      <div class="header-content">
        <div class="brand-group">
          <a href="/" class="logo">
            <div class="logo-icon">🎙️</div>
            <span>Admin Dashboard</span>
          </a>
          <button type="button" class="release-chip" data-release="${ADMIN_RELEASE_TAG}" onclick="copyReleaseTag(event)" title="Copy release tag to clipboard" aria-label="Copy release tag to clipboard"><span aria-hidden="true">📋</span><span>${ADMIN_RELEASE_TAG}</span></button>
        </div>
        <nav>
          <a href="/"${activePage === 'dashboard' ? ' class="active"' : ''}>Dashboard</a>
          <a href="/drafts"${activePage === 'drafts' ? ' class="active"' : ''}>Drafts</a>
          <a href="/queue"${activePage === 'queue' ? ' class="active"' : ''}>Queue</a>
          <a href="/conferences"${activePage === 'conferences' ? ' class="active"' : ''}>Conferences</a>
          <a href="/submit"${activePage === 'submit' ? ' class="active"' : ''}>Submit</a>
          <a href="/issues"${activePage === 'issues' ? ' class="active"' : ''}>Issues</a>
          <a href="/automation"${activePage === 'automation' ? ' class="active"' : ''}>Automation</a>
        </nav>
      </div>
    </div>
  </header>
  <main>
    <div class="container">
      ${content}
    </div>
  </main>
  <footer>
    <div class="container">
      <div class="footer-content">
        <span>AI Post Transformers Admin · ${ADMIN_RELEASE_TAG}</span>
        <div class="footer-links">
          <a href="https://podcast.do-not-panic.com" target="_blank">Main Site</a>
          <a href="https://github.com/${GITHUB_REPO}" target="_blank">GitHub</a>
        </div>
      </div>
    </div>
  </footer>
  <div id="toast-container" class="toast-container"></div>
  <script>${clientScript}</script>
</body>
</html>`;
}

function dashboardPage(stats) {
  return `
    <div class="page-header">
      <h1>Dashboard</h1>
      <p>Overview of your podcast administration</p>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-value">${stats.pendingDrafts}</div>
        <div class="stat-label">Pending Drafts</div>
        <div class="stat-icon">📋</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${stats.queueSize}</div>
        <div class="stat-label">Queue Size</div>
        <div class="stat-icon">📚</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${stats.submissions}</div>
        <div class="stat-label">Submissions</div>
        <div class="stat-icon">📨</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${stats.openIssues}</div>
        <div class="stat-label">Open Issues</div>
        <div class="stat-icon">💬</div>
      </div>
    </div>

    <h2>Quick Actions</h2>
    <div class="quick-links">
      <a href="/drafts" class="quick-link">
        <div class="quick-link-icon">🎧</div>
        <div class="quick-link-text">
          <h3>Review Drafts</h3>
          <p>Listen and approve pending episodes</p>
        </div>
      </a>
      <a href="/queue" class="quick-link">
        <div class="quick-link-icon">📰</div>
        <div class="quick-link-text">
          <h3>Editorial Queue</h3>
          <p>Browse papers ranked by relevance</p>
        </div>
      </a>
      <a href="/submit" class="quick-link">
        <div class="quick-link-icon">🔗</div>
        <div class="quick-link-text">
          <h3>Submit Papers</h3>
          <p>Add new papers for processing</p>
        </div>
      </a>
      <a href="/issues" class="quick-link">
        <div class="quick-link-icon">💡</div>
        <div class="quick-link-text">
          <h3>Community Issues</h3>
          <p>Review feedback and requests</p>
        </div>
      </a>
    </div>
  `;
}


function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── Display-title fallback for queue cards ────────────────────
// Opaque identifiers like "ep102" or bare draft stems should never
// be the primary label shown to the operator.  This helper walks a
// fallback chain until it finds something human-readable.
//
// Order:  enriched paper title → explicit title (if not opaque) →
//         URL-derived fallback → human-readable slug from key → generic label.
const OPAQUE_ID_RE = /^ep\d+$/i;
const DRAFT_STEM_RE = /^(\d{4}-\d{2}-\d{2}-)?(.*?)(-[a-f0-9]{4,8})?$/;

function humanizeSlug(slug) {
  if (!slug) return '';
  const m = slug.match(DRAFT_STEM_RE);
  const core = m ? m[2] : slug;
  if (!core) return '';
  return core.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function titleFromUrl(item) {
  const url = (item.urls && item.urls[0]) || item.url || '';
  if (!url) return '';
  const arxiv = url.match(/(\d{4}\.\d{4,5})(?:v\d+)?/);
  if (arxiv) return `arXiv ${arxiv[1]}`;
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, '');
    const path = u.pathname && u.pathname !== '/' ? u.pathname : '';
    return `${host}${path}`;
  } catch {
    return url;
  }
}

function genericTitle(item) {
  if ((item.urls && item.urls.length) || item.url) return 'Submitted paper';
  if (item.draft_stem || (item.key || '').includes('draft')) return 'Draft episode';
  return 'Untitled';
}

function displayTitle(item) {
  // 1. Enriched paper title (from metadata enrichment on first URL)
  if (item.metadata) {
    const urls = item.urls || [];
    for (const u of urls) {
      const m = item.metadata[u];
      if (m && m.enrichment_status === 'done' && m.title) return m.title;
    }
  }
  // 2. Explicit title if it's not an opaque ID
  if (item.title && !OPAQUE_ID_RE.test(item.title)) return item.title;
  // 3. URL-derived human fallback (better than opaque internal ids)
  const urlTitle = titleFromUrl(item);
  if (urlTitle) return urlTitle;
  // 4. Human-readable slug derived from key or draft_stem, but not opaque ids
  const stem = item.draft_stem || item.key || '';
  const base = stem.split('/').pop().replace(/\.\w+$/, '');
  if (base && !OPAQUE_ID_RE.test(base)) {
    const friendly = humanizeSlug(base);
    if (friendly && !OPAQUE_ID_RE.test(friendly.replace(/\s+/g, ''))) return friendly;
  }
  // 5. Generic human fallback only — never expose opaque ids as main label
  return genericTitle(item);
}

function splitDraftSources(text) {
  const raw = String(text || '');
  const match = raw.match(/(?:\n\s*Sources:\s*\n?|Sources:\s*)/i);
  if (!match) {
    return { body: raw.trim(), sources: '' };
  }
  const idx = match.index || 0;
  return {
    body: raw.slice(0, idx).trim(),
    sources: raw.slice(idx + match[0].length).trim(),
  };
}

function formatDraftSourcesHtml(sourcesRaw, preview = false) {
  if (!sourcesRaw) return '';
  let body = String(sourcesRaw || '');
  body = body.replace(/(\d+\.\s)/g, '\n$1');
  body = body.replace(/(https?:\/\/)/g, '\n$1');
  body = body.replace(/[ 	]+/g, ' ');
  body = body.replace(/\n\s*/g, '\n').trim();

  const lines = body.split(/\n+/).map(line => line.trim()).filter(Boolean);
  const rendered = [];
  let pendingTitle = null;
  let sourceCount = 0;

  function flushTitle() {
    if (pendingTitle) {
      rendered.push(escapeHtml(pendingTitle.trim()));
      pendingTitle = null;
      sourceCount += 1;
    }
  }

  for (const line of lines) {
    if (/^https?:\/\//i.test(line)) {
      flushTitle();
      const urls = line.match(/https?:\/\/[^\s,)]+/g) || [];
      for (const url of urls) {
        const esc = escapeHtml(url);
        rendered.push('<a href="' + esc + '" target="_blank" rel="noopener noreferrer">' + esc + '</a>');
      }
      if (urls.length > 1) sourceCount += urls.length - 1;
      continue;
    }
    if (/^\d+\.\s/.test(line)) {
      flushTitle();
      pendingTitle = line;
      continue;
    }
    if (pendingTitle) pendingTitle += ' ' + line;
    else {
      rendered.push(escapeHtml(line));
      sourceCount += 1;
    }
  }
  flushTitle();

  const visible = preview ? rendered.slice(0, 4) : rendered;
  const label = preview && sourceCount > 0 ? 'Sources (' + sourceCount + ')' : 'Sources:';
  return '<div class="card-sources"><strong>' + label + '</strong><br><br>' + visible.join('<br>') + '</div>';
}

function formatDraftDescription(desc, preview = false) {
  const text = String(desc || 'No description available');
  const parts = splitDraftSources(text);
  const bodyText = preview && parts.body.length > 220 ? parts.body.slice(0, 220).trimEnd() + '...' : parts.body;

  let html = escapeHtml(bodyText).replace(/\n/g, '<br>');
  html = html.replace(/(<br>\s*){3,}/g, '<br><br>');
  if (parts.sources) {
    html += formatDraftSourcesHtml(parts.sources, preview);
  }
  return html;
}

function toggleDraftDescription(btn) {
  const wrap = btn.closest('.draft-description, .draft-desc-wrap');
  if (!wrap) return;
  const preview = wrap.querySelector('.desc-preview');
  const full = wrap.querySelector('.desc-full');
  const showingFull = full && full.style.display !== 'none';
  if (preview) preview.style.display = showingFull ? 'block' : 'none';
  if (full) full.style.display = showingFull ? 'none' : 'block';
  btn.textContent = showingFull ? 'Show more' : 'Show less';
}


function publishJobBadgeClass(state) {
  switch (state) {
    case 'approved_for_publish':
    case 'publish_completed':
      return 'badge-success';
    case 'publish_claimed':
    case 'publish_running':
      return 'badge-blue';
    case 'publish_failed':
      return 'badge-danger';
    case 'publish_released':
      return 'badge-warning';
    default:
      return 'badge-pending';
  }
}

function publishJobStateLabel(job) {
  if (!job || !job.state) return 'Pending Review';
  switch (job.state) {
    case 'approved_for_publish': return 'Approved for Publish';
    case 'publish_claimed': return 'Claimed for Publish';
    case 'publish_running': return 'Publishing';
    case 'publish_completed': return 'Published';
    case 'publish_failed': return 'Publish Failed';
    case 'publish_released': return 'Ready to Reclaim';
    default: return job.state.replace(/_/g, ' ');
  }
}

function renderPublishJobSummary(job) {
  if (!job) return '';
  const parts = [];
  parts.push('<div style="margin-top:0.5rem;font-size:0.77rem;color:var(--text-secondary)">Publish state: <strong style="color:var(--text-primary)">' + escapeHtml(publishJobStateLabel(job)) + '</strong></div>');
  if (job.claimed_by && job.claimed_by.name) {
    parts.push('<div style="font-size:0.74rem;color:var(--text-secondary)">Claimed by: ' + escapeHtml(job.claimed_by.name) + '</div>');
  }
  if (job.error && job.error.step) {
    const msg = job.error.message || 'Publish step failed';
    parts.push('<div style="font-size:0.74rem;color:var(--danger)">Last error: ' + escapeHtml(job.error.step + ' — ' + msg) + '</div>');
  }
  return parts.join('');
}

function renderDraftActionButtons(draft) {
  const job = draft.publish_job || null;
  const state = job && job.state ? job.state : null;
  const locked = ['approved_for_publish', 'publish_claimed', 'publish_running', 'publish_completed'].includes(state);
  const approveLabel = locked ? '✓ Approved' : '✓ Approve';
  const approveAttrs = locked ? ' disabled style="opacity:0.65;cursor:not-allowed"' : '';
  return '<button class="btn btn-success" onclick="approveDraft(\'' + draft.key + '\')"' + approveAttrs + '>' + approveLabel + '</button>'
    + '<button class="btn btn-danger" onclick="openRejectModal(\'' + draft.key + '\')">✕ Reject</button>';
}

function draftsPageWithData(data) {
  const drafts = data.drafts || [];
  if (drafts.length === 0) {
    return `<div class="page-header"><h1>Draft Review</h1><p>Review and approve pending episodes</p></div>
    <div class="empty-state"><div class="empty-state-icon">🎧</div><h3>No pending drafts</h3><p>All caught up!</p></div>`;
  }

  const cards = drafts.map(d => {
    const desc = d.description || '';
    const previewHtml = formatDraftDescription(desc, true);
    const fullHtml = formatDraftDescription(desc, false);
    const showToggle = desc.length > 220;
    return `
    <div class="card" style="margin-bottom:1.5rem">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <h3 style="margin:0 0 4px">${escapeHtml(displayTitle(d))}${d.revision && d.revision > 1 ? ` <span style="font-size:0.75rem;color:var(--text-secondary);font-weight:normal">(v${d.revision})</span>` : ''}</h3>
          <div style="color:var(--text-secondary);font-size:0.813rem">
            📅 ${d.date || 'Unknown'} · ⏱️ ${d.duration || '~25 min'}
          </div>
        </div>
        <span class="badge ${publishJobBadgeClass(d.publish_job && d.publish_job.state)}">${publishJobStateLabel(d.publish_job)}</span>
      </div>
      ${renderPublishJobSummary(d.publish_job)}
      <div class="draft-desc-wrap" style="color:var(--text-secondary);font-size:0.875rem;margin:0.75rem 0;line-height:1.55">
        <div class="desc-preview">${previewHtml}</div>
        ${showToggle ? `<div class="desc-full" style="display:none">${fullHtml}</div><button type="button" onclick="toggleDraftDescription(this)" style="background:none;border:none;color:var(--accent);cursor:pointer;padding:0;font-size:0.8rem;margin-top:0.5rem">Show more</button>` : ''}
      </div>
      <div style="margin:0.75rem 0;display:flex;align-items:center;gap:4px">
        <button onclick="seekAudio(this,-60)" style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:6px;padding:6px 8px;cursor:pointer;color:var(--text-primary);font-size:0.75rem" title="Rewind 1 min">⏪1m</button>
        <button onclick="seekAudio(this,-15)" style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:6px;padding:6px 8px;cursor:pointer;color:var(--text-primary);font-size:0.75rem" title="Rewind 15s">⏪15s</button>
        <audio controls preload="metadata" style="flex:1;height:40px">
          <source src="${d.audioUrl}" type="audio/mpeg">
        </audio>
        <button onclick="seekAudio(this,15)" style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:6px;padding:6px 8px;cursor:pointer;color:var(--text-primary);font-size:0.75rem" title="Forward 15s">15s⏩</button>
        <button onclick="seekAudio(this,60)" style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:6px;padding:6px 8px;cursor:pointer;color:var(--text-primary);font-size:0.75rem" title="Forward 1 min">1m⏩</button>
      </div>
      <div style="display:flex;gap:8px;margin-top:0.75rem">
        ${renderDraftActionButtons(d)}
      </div>
    </div>
  `}).join('');

  return `<div class="page-header"><h1>Draft Review</h1><p>${drafts.length} episodes pending review</p></div>${cards}`;
}

function draftsPage() {
  return `
    <div class="page-header">
      <h1>Draft Episodes</h1>
      <p>Review and approve podcast drafts</p>
    </div>

    <div id="drafts-container">
      <div class="loading">
        <div class="spinner"></div>
        Loading drafts...
      </div>
    </div>

    <div id="reject-modal" class="modal-overlay" style="display: none;">
      <div class="modal">
        <div class="modal-header">
          <h3 class="modal-title">Reject Draft</h3>
          <button class="modal-close" onclick="closeRejectModal()">&times;</button>
        </div>
        <div class="form-group">
          <label for="reject-reason">Reason for rejection</label>
          <textarea id="reject-reason" placeholder="Enter the reason for rejecting this draft..."></textarea>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" onclick="closeRejectModal()">Cancel</button>
          <button class="btn btn-danger" onclick="confirmReject()">Reject Draft</button>
        </div>
      </div>
    </div>

    <script>
      loadDrafts();
    </script>
  `;
}




function submissionStatusBadge(status) {
  const map = {
    submitted:            { label: 'Submitted',        color: 'var(--accent)' },
    pending:              { label: 'Submitted',        color: 'var(--accent)' },
    generation_claimed:   { label: 'Claimed',          color: 'var(--warning)' },
    generation_running:   { label: 'Generating Draft', color: 'var(--warning)' },
    draft_generated:      { label: 'Draft Ready',      color: 'var(--success)' },
    generation_failed:    { label: 'Failed',           color: 'var(--danger)' },
    approved_for_publish: { label: 'Approved',         color: 'var(--purple)' },
    published:            { label: 'Published',        color: 'var(--success)' },
    rejected:             { label: 'Rejected',         color: 'var(--danger)' },
  };
  const info = map[status] || { label: status, color: 'var(--text-secondary)' };
  return `<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:600;background:${info.color}22;color:${info.color}">${info.label}</span>`;
}

function renderSubmissionMetadata(sub) {
  const meta = sub.metadata || {};
  const urls = sub.urls || [];
  let html = '';
  for (const u of urls) {
    const m = meta[u];
    const short = u.length > 80 ? u.substring(0, 77) + '...' : u;
    const link = `<a href="${escapeHtml(u)}" target="_blank" style="color:var(--accent);text-decoration:none;font-size:0.813rem">`;

    if (m && m.enrichment_status === 'done' && m.title) {
      // Enriched: show title as link, date + summary snippet below
      html += `${link}<strong>${escapeHtml(m.title)}</strong></a>`;
      const parts = [];
      if (m.published) {
        parts.push(m.published.substring(0, 10));
      }
      if (m.arxiv_id) {
        parts.push(`arXiv:${m.arxiv_id}`);
      }
      if (parts.length > 0) {
        html += `<div style="font-size:0.7rem;color:var(--text-muted);margin-top:1px">${escapeHtml(parts.join(' · '))}</div>`;
      }
      if (m.summary) {
        const snippet = m.summary.length > 180 ? m.summary.substring(0, 177) + '...' : m.summary;
        html += `<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:2px">${escapeHtml(snippet)}</div>`;
      }
    } else if (m && m.enrichment_status === 'pending') {
      // Pending enrichment
      html += `${link}${escapeHtml(short)}</a>`;
      html += `<div style="font-size:0.7rem;color:var(--text-muted);margin-top:1px;font-style:italic">⏳ Enriching metadata…</div>`;
    } else if (m && m.enrichment_status === 'failed') {
      // Failed enrichment — show URL with note
      html += `${link}${escapeHtml(short)}</a>`;
      html += `<div style="font-size:0.7rem;color:var(--text-muted);margin-top:1px">Metadata unavailable</div>`;
    } else {
      // No metadata at all or unsupported source
      html += `${link}${escapeHtml(short)}</a>`;
    }
    html += '<div style="margin-bottom:6px"></div>';
  }
  return html;
}

function renderSubmissionRow(sub) {
  const title = displayTitle(sub);
  const metaHtml = renderSubmissionMetadata(sub);
  const timeStr = new Date(sub.timestamp).toLocaleString();
  const badge = submissionStatusBadge(sub.status);
  const errorLine = sub.error
    ? `<div style="color:var(--danger);font-size:0.75rem;margin-top:4px">Error: ${escapeHtml(sub.error)}</div>`
    : '';
  const draftLine = sub.draft_stem
    ? `<div style="font-size:0.75rem;margin-top:4px;color:var(--text-secondary)">Draft: ${escapeHtml(sub.draft_stem)}</div>`
    : '';
  const retryBtn = sub.status === 'generation_failed' && sub.key
    ? `<button class="btn btn-secondary" onclick="retrySubmission('${escapeHtml(sub.key)}')" style="font-size:0.7rem;padding:3px 8px;margin-top:6px">🔄 Retry</button>`
    : '';
  const instrLine = sub.instructions
    ? `<div style="font-size:0.75rem;margin-top:4px;color:var(--text-secondary);font-style:italic">${escapeHtml(sub.instructions.substring(0, 120))}${sub.instructions.length > 120 ? '...' : ''}</div>`
    : '';
  const isPending = sub.status === 'submitted' || sub.status === 'pending';
  const claimedLine = sub.claimed_by
    ? `<div style="font-size:0.75rem;margin-top:4px;color:var(--text-secondary)">Assigned to: <strong style="color:var(--text-primary)">${escapeHtml(sub.claimed_by)}</strong></div>`
    : isPending
      ? `<div style="font-size:0.75rem;margin-top:4px;color:var(--text-muted)">Open — any generation worker can claim</div>`
      : '';

  return `
    <div style="padding:12px 0;border-bottom:1px solid var(--border)">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
        <div style="flex:1">
          <div style="font-weight:600;font-size:0.95rem;margin-bottom:6px">${escapeHtml(title)}</div>
          ${metaHtml}
          ${instrLine}
          ${claimedLine}
          ${errorLine}
          ${draftLine}
          ${retryBtn}
          <div style="color:var(--text-muted);font-size:0.75rem;margin-top:4px">${timeStr}</div>
        </div>
        <div style="text-align:right">
          ${badge}
        </div>
      </div>
    </div>`;
}

function queuePageWithData(data, subsData) {
  // Use sections (keyed by section name) rather than the flat papers array
  const papers = data.sections || {};
  const submissions = (subsData && subsData.submissions) || [];
  const sections = [
    {key: 'bridge', label: '🌉 Bridge Papers', desc: 'Papers connecting memory/storage with broader AI interest'},
    {key: 'public', label: '🌍 Public Interest', desc: 'High-impact papers for general AI audience'},
    {key: 'memory', label: '💾 Memory / Storage', desc: 'Papers directly relevant to memory and storage systems'},
    {key: 'monitor', label: '👀 Monitor', desc: 'Worth tracking, not yet podcast-ready'},
    {key: 'deferred', label: '⏸️ Deferred', desc: 'Pushed to next cycle'},
  ];

  let totalPapers = 0;
  sections.forEach(s => { totalPapers += (papers[s.key] || []).length; });

  // Partition submissions into generation-focused groups
  const pendingSubs = submissions.filter(s =>
    s.status === 'submitted' || s.status === 'pending'
  );
  const generatingSubs = submissions.filter(s =>
    ['generation_claimed', 'generation_running'].includes(s.status)
  );
  const failedSubs = submissions.filter(s =>
    s.status === 'generation_failed'
  );
  const draftReadySubs = submissions.filter(s =>
    s.status === 'draft_generated'
  );
  const generationPipelineCount = pendingSubs.length + generatingSubs.length + failedSubs.length + draftReadySubs.length;

  let html = `
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
      <div>
        <h1>Editorial Queue</h1>
        <p>${totalPapers} editorial papers · ${generationPipelineCount} in generation pipeline</p>
      </div>
      <div style="display:flex;gap:8px">
        <a href="https://podcast.do-not-panic.com/queue.html" target="_blank" class="btn btn-primary">📊 Full Queue ↗</a>
        <a href="/submit" class="btn btn-secondary">+ Submit</a>
      </div>
    </div>

    <div class="card">
      <label style="color:var(--text-secondary);font-size:0.813rem;display:block;margin-bottom:6px">Quick generate from URL:</label>
      <div style="display:flex;gap:8px">
        <input id="quick-gen-url" placeholder="https://arxiv.org/pdf/2401.12345" style="flex:1;padding:10px 14px;background:var(--background);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:0.875rem">
        <button class="btn btn-success" onclick="quickGenerate()" style="white-space:nowrap">🎙️ Generate</button>
      </div>
    </div>`;

  // --- Pending generation section (submitted, not yet picked up) ---
  if (pendingSubs.length > 0) {
    html += `<div class="card" style="margin-top:1.5rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div>
          <h2 style="margin:0">⏳ Pending Generation <span style="color:var(--text-secondary);font-size:0.875rem;font-weight:400">(${pendingSubs.length})</span></h2>
          <p style="color:var(--text-secondary);font-size:0.813rem;margin:2px 0 0">Awaiting pickup by a generation worker — anyone with systemd timer can claim these</p>
        </div>
      </div>`;
    pendingSubs.forEach(sub => { html += renderSubmissionRow(sub); });
    html += '</div>';
  }

  // --- Active generation (claimed or running) ---
  if (generatingSubs.length > 0) {
    html += `<div class="card" style="margin-top:1.5rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div>
          <h2 style="margin:0">🔄 Generating <span style="color:var(--text-secondary);font-size:0.875rem;font-weight:400">(${generatingSubs.length})</span></h2>
          <p style="color:var(--text-secondary);font-size:0.813rem;margin:2px 0 0">A worker is actively generating these episodes</p>
        </div>
      </div>`;
    generatingSubs.forEach(sub => { html += renderSubmissionRow(sub); });
    html += '</div>';
  }

  // --- Draft ready (generated but not yet reviewed) ---
  if (draftReadySubs.length > 0) {
    html += `<div class="card" style="margin-top:1.5rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div>
          <h2 style="margin:0">✅ Draft Ready <span style="color:var(--text-secondary);font-size:0.875rem;font-weight:400">(${draftReadySubs.length})</span></h2>
          <p style="color:var(--text-secondary);font-size:0.813rem;margin:2px 0 0">Generation complete — head to <a href="/drafts" style="color:var(--accent)">Drafts</a> to review and approve</p>
        </div>
      </div>`;
    draftReadySubs.forEach(sub => { html += renderSubmissionRow(sub); });
    html += '</div>';
  }

  // --- Failed generation ---
  if (failedSubs.length > 0) {
    html += `<div class="card" style="margin-top:1.5rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div>
          <h2 style="margin:0">❌ Generation Failed <span style="color:var(--text-secondary);font-size:0.875rem;font-weight:400">(${failedSubs.length})</span></h2>
          <p style="color:var(--text-secondary);font-size:0.813rem;margin:2px 0 0">These submissions hit errors during generation</p>
        </div>
      </div>`;
    failedSubs.forEach(sub => { html += renderSubmissionRow(sub); });
    html += '</div>';
  }

  // Collect arXiv IDs from submissions that are past the editorial stage
  // so we can suppress them from the editorial queue (they're already handled).
  // This includes papers in any active generation state, not just published —
  // a paper being generated or with a ready draft doesn't need editorial action.
  const handledStatuses = [
    'generation_claimed', 'generation_running', 'draft_generated',
    'approved_for_publish', 'published',
  ];
  const handledArxivIds = new Set();
  submissions.filter(s => handledStatuses.includes(s.status)).forEach(s => {
    (s.urls || []).forEach(u => {
      const m = u.match(/(\d{4}\.\d{4,5})/);
      if (m) handledArxivIds.add(m[1]);
    });
  });

  sections.forEach(s => {
    const allItems = papers[s.key] || [];
    // Filter out papers that already have a published or approved submission
    const items = allItems.filter(p =>
      !p.arxiv_id || !handledArxivIds.has(p.arxiv_id)
    );
    if (items.length === 0) return;

    html += `<div class="card" style="margin-top:1.5rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
        <div>
          <h2 style="margin:0">${s.label} <span style="color:var(--text-secondary);font-size:0.875rem;font-weight:400">(${items.length})</span></h2>
          <p style="color:var(--text-secondary);font-size:0.813rem;margin:2px 0 0">${s.desc}</p>
        </div>
      </div>`;

    items.forEach((p, i) => {
      const title = displayTitle(p);
      const abstract = (p.abstract || '').substring(0, 200) + ((p.abstract || '').length > 200 ? '...' : '');
      const arxivUrl = p.arxiv_id ? 'https://arxiv.org/abs/' + p.arxiv_id : '#';
      const pdfUrl = p.arxiv_id ? 'https://arxiv.org/pdf/' + p.arxiv_id : '#';

      html += `
        <div style="padding:12px 0;border-bottom:1px solid var(--border)">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
            <div style="flex:1">
              <a href="${arxivUrl}" target="_blank" style="color:var(--accent);text-decoration:none;font-weight:600;font-size:0.95rem">${escapeHtml(title)}</a>
              <p style="color:var(--text-secondary);font-size:0.813rem;margin:4px 0 0">${abstract}</p>
            </div>
            <button class="btn btn-success" onclick="quickGenerateUrl('${pdfUrl}')" style="font-size:0.75rem;padding:4px 10px;white-space:nowrap">🎙️</button>
          </div>
        </div>`;
    });

    html += '</div>';
  });

  html += `<script>
    async function quickGenerate() {
      const url = document.getElementById('quick-gen-url').value.trim();
      if (!url) { showToast('Enter a URL', 'error'); return; }
      quickGenerateUrl(url);
      document.getElementById('quick-gen-url').value = '';
    }
    async function quickGenerateUrl(url) {
      try {
        const res = await fetch('/api/generate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({urls: [url], action: 'generate'}),
          credentials: 'same-origin'
        });
        showToast('Queued for generation!');
      } catch(e) { showToast('Failed: ' + e.message, 'error'); }
    }
    async function retrySubmission(key) {
      try {
        const res = await fetch('/api/submissions/status', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ key, status: 'submitted' }),
          credentials: 'same-origin'
        });
        const data = await res.json();
        if (data.success) {
          showToast('Reset to submitted — awaiting generation pickup');
          setTimeout(() => location.reload(), 800);
        } else {
          showToast(data.error || 'Retry failed', 'error');
        }
      } catch(e) { showToast('Retry failed: ' + e.message, 'error'); }
    }
  </script>`;

  return html;
}


function queuePage() {
  return `
    <div class="page-header">
      <h1>Editorial Queue</h1>
      <p>Papers ranked by relevance score</p>
    </div>

    <div id="queue-container">
      <div class="loading">
        <div class="spinner"></div>
        Loading queue...
      </div>
    </div>

    <script>
      loadQueue();
    </script>
  `;
}


// Conference data - centralized for easy updates
const CONFERENCES = {
  'neurips2025': {
    id: 'neurips2025',
    name: 'NeurIPS 2025',
    fullName: 'Conference on Neural Information Processing Systems 2025',
    color: '#a371f7',
    badgeText: 'NeurIPS',
    episodes: [
      { id: 37, title: 'CARTRIDGE: Keys as Routers in KV Caches', date: '2025-01-15' },
      { id: 39, title: 'Tokenization Bias in Language Models', date: '2025-01-22' }
    ]
  },
  'icml2024': {
    id: 'icml2024',
    name: 'ICML 2024',
    fullName: 'International Conference on Machine Learning 2024',
    color: '#58a6ff',
    badgeText: 'ICML',
    episodes: [
      { id: 34, title: 'Structured State Space Duality', date: '2024-12-10' }
    ]
  },
  'iclr2026': {
    id: 'iclr2026',
    name: 'ICLR 2026',
    fullName: 'International Conference on Learning Representations 2026',
    color: '#3fb950',
    badgeText: 'ICLR',
    episodes: [
      { id: 46, title: 'Gradient Descent at Inference Time', date: '2026-02-28' }
    ]
  },
  'fast26': {
    id: 'fast26',
    name: "FAST '26",
    fullName: 'USENIX Conference on File and Storage Technologies 2026',
    color: '#c9302c',
    badgeText: 'USENIX',
    episodes: [],
    status: 'generating',
    papers: [
      { name: 'fast26-liu-yang', authors: 'Liu et al.', url: 'https://www.usenix.org/system/files/fast26-liu-yang.pdf' },
      { name: 'fast26-hu-shipeng', authors: 'Hu et al.', url: 'https://www.usenix.org/system/files/fast26-hu-shipeng.pdf' },
      { name: 'fast26-zheng', authors: 'Zheng et al.', url: 'https://www.usenix.org/system/files/fast26-zheng.pdf' },
      { name: 'fast26-liu-qingyuan', authors: 'Liu et al.', url: 'https://www.usenix.org/system/files/fast26-liu-qingyuan.pdf' },
      { name: 'fast26-an', authors: 'An et al.', url: 'https://www.usenix.org/system/files/fast26-an.pdf' },
      { name: 'fast26-liu-yubo', authors: 'Liu et al.', url: 'https://www.usenix.org/system/files/fast26-liu-yubo.pdf' }
    ]
  }
};

function conferencesPage() {
  const conferenceCards = Object.values(CONFERENCES).map(conf => `
    <a href="/conference/${conf.id}" class="conference-card" style="border-left: 4px solid ${conf.color};">
      <div class="conf-badge" style="background: ${conf.color};">${conf.badgeText}</div>
      <h3>${conf.name}</h3>
      <p>${conf.fullName}</p>
      <span class="conf-count" style="color: ${conf.color};">
        ${conf.episodes.length} episode${conf.episodes.length !== 1 ? 's' : ''}
        ${conf.status === 'generating' ? ' (generating)' : ''}
      </span>
    </a>
  `).join('');

  return `
    <div class="page-header">
      <h1>Conferences</h1>
      <p>Episodes organized by academic conference</p>
    </div>

    <div class="stats-grid" style="margin-bottom: 2rem;">
      <div class="stat-card">
        <div class="stat-value">${Object.keys(CONFERENCES).length}</div>
        <div class="stat-label">Tracked Conferences</div>
        <div class="stat-icon">🎓</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${Object.values(CONFERENCES).reduce((sum, c) => sum + c.episodes.length, 0)}</div>
        <div class="stat-label">Total Episodes</div>
        <div class="stat-icon">🎙️</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${Object.values(CONFERENCES).filter(c => c.status === 'generating').length}</div>
        <div class="stat-label">In Progress</div>
        <div class="stat-icon">⏳</div>
      </div>
    </div>

    <h2>All Conferences</h2>
    <div class="conference-grid">
      ${conferenceCards}
    </div>
  `;
}

function conferenceDetailPage(confId) {
  const conf = CONFERENCES[confId];
  if (!conf) {
    return `
      <div class="empty-state">
        <div class="empty-state-icon">🔍</div>
        <h3>Conference not found</h3>
        <p><a href="/conferences">Back to conferences</a></p>
      </div>
    `;
  }

  const episodeCards = conf.episodes.length > 0 ? conf.episodes.map(ep => `
    <div class="card" style="border-left: 4px solid ${conf.color};">
      <div class="card-header">
        <div>
          <div class="card-title">${ep.title}</div>
          <div class="card-subtitle">Episode ${ep.id} • ${ep.date}</div>
        </div>
      </div>
      <div class="draft-actions">
        <a href="${PODCAST_DOMAIN}/episodes/${ep.id}.html" target="_blank" class="btn btn-primary">
          ▶ Play Episode
        </a>
        <a href="${PODCAST_DOMAIN}/episodes/${ep.id}.mp3" target="_blank" class="btn btn-secondary">
          ⬇ Download MP3
        </a>
      </div>
    </div>
  `).join('') : `
    <div class="empty-state">
      <div class="empty-state-icon">📄</div>
      <h3>Episodes generating...</h3>
      <p>Conference paper podcasts are being generated. Check back soon.</p>
    </div>
  `;

  const papersSection = conf.papers ? `
    <div class="card" style="margin-top: 1.5rem;">
      <h2>Submitted Papers</h2>
      <table style="width:100%;font-size:0.875rem">
        <thead>
          <tr style="text-align:left;color:var(--text-secondary)">
            <th style="padding:8px 0">Paper</th>
            <th>Authors</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          ${conf.papers.map(p => `
            <tr>
              <td style="padding:6px 0"><a href="${p.url}" target="_blank">${p.name}</a></td>
              <td>${p.authors}</td>
              <td><span class="badge badge-warning">Generating</span></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  ` : '';

  return `
    <div class="page-header">
      <h1 style="display:flex;align-items:center;gap:12px">
        <span style="background:${conf.color};color:white;padding:4px 12px;border-radius:6px;font-size:0.7em;font-weight:700;letter-spacing:1px">${conf.badgeText}</span>
        ${conf.name}
      </h1>
      <p>${conf.fullName}</p>
    </div>

    <div class="card">
      <div style="display:flex;gap:2rem;flex-wrap:wrap;margin-bottom:1.5rem">
        <div>
          <div style="font-size:2rem;font-weight:700;color:${conf.color}">${conf.episodes.length}</div>
          <div style="color:var(--text-secondary);font-size:0.813rem">Episodes</div>
        </div>
        ${conf.papers ? `
        <div>
          <div style="font-size:2rem;font-weight:700;color:var(--warning)">${conf.papers.length}</div>
          <div style="color:var(--text-secondary);font-size:0.813rem">Papers Submitted</div>
        </div>
        ` : ''}
      </div>
      <a href="/conferences" class="btn btn-secondary">← Back to Conferences</a>
    </div>

    <div class="card" style="margin-top:1.5rem">
      <h2>Conference Episodes</h2>
      ${episodeCards}
    </div>

    ${papersSection}
  `;
}

function conferenceFAST26Page() {
  return conferenceDetailPage('fast26');
}

function submitPage(subsData) {
  const submissions = (subsData && subsData.submissions) || [];

  let recentHtml = '';
  if (submissions.length === 0) {
    recentHtml = '<div class="empty-state" style="padding:2rem 0"><p style="color:var(--text-secondary)">No submissions yet</p></div>';
  } else {
    recentHtml = submissions.map(sub => renderSubmissionRow(sub)).join('');
  }

  return `
    <div class="page-header">
      <h1>Submit Papers</h1>
      <p>Add paper URLs for podcast generation — include special instructions for how episodes should be produced</p>
    </div>

    <div class="card">
      <h2>New Submission</h2>
      <form id="submit-form" onsubmit="handleSubmit(event)">
        <div class="form-group">
          <label for="urls">Paper URLs (one per line)</label>
          <textarea id="urls" placeholder="https://arxiv.org/abs/2401.12345
https://www.usenix.org/system/files/fast26-example.pdf
https://example.com/paper.pdf" rows="6"></textarea>
        </div>
        <div class="form-group" style="margin-top: 1rem;">
          <label for="instructions">Special Instructions (optional)</label>
          <textarea id="instructions" placeholder="Example: These papers are from USENIX FAST'26. Use the prefix 'FAST26:' in the episode title. Cover them serially so later episodes can reference earlier ones by title. Create a conference page to track all FAST26 episodes together.

Other ideas:
• Group related papers into a series
• Request specific analysis angles
• Ask for cross-references to prior episodes
• Specify a conference or workshop theme" rows="6" style="font-size: 0.875rem;"></textarea>
        </div>
        <p style="color: var(--text-secondary); font-size: 0.813rem; margin-bottom: 1rem;">
          <strong>Accepts:</strong> arXiv PDFs, arXiv abstract pages, USENIX/ACM/IEEE paper PDFs, direct PDF URLs, HTML paper pages.<br>
          <strong>Instructions:</strong> Add context like conference names, series ordering, title prefixes, or analysis focus. Papers from the same submission are processed together.
        </p>
        <button type="submit" class="btn btn-primary">Submit Papers</button>
      </form>
    </div>

    <div class="card" style="margin-top: 1.5rem;">
      <h2>Conference Pages</h2>
      <p style="color: var(--text-secondary); margin-bottom: 1rem;">Track all episodes from a conference or event in one place.</p>
      <div class="conference-grid">
        <a href="/conference/fast26" class="conference-card">
          <div class="conf-badge">USENIX</div>
          <h3>FAST '26</h3>
          <p>File and Storage Technologies</p>
          <span class="conf-count" id="fast26-count">0 episodes</span>
        </a>
      </div>
    </div>

    <div class="card" style="margin-top: 1.5rem;">
      <h2>Recent Submissions</h2>
      <div id="submissions-container">${recentHtml}</div>
    </div>
  `;
}


function issuesPageWithData(data) {
  const issues = data.issues || [];
  if (issues.length === 0) {
    return `<div class="page-header"><h1>Community Issues</h1><p>Issues from <a href="https://github.com/mcgrof/ai-post-transformers/issues" target="_blank">GitHub</a></p></div>
    <div class="card"><div class="empty-state"><div class="empty-state-icon">📝</div><h3>No open issues</h3><p><a href="https://github.com/mcgrof/ai-post-transformers/issues/new" target="_blank">Create one on GitHub</a></p></div></div>`;
  }

  const cards = issues.map(i => `
    <div class="card" style="margin-bottom:1rem">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <h3 style="margin:0 0 4px"><a href="${i.html_url}" target="_blank" style="color:var(--accent);text-decoration:none">#${i.number} ${i.title}</a></h3>
          <div style="color:var(--text-secondary);font-size:0.813rem">
            by ${i.user?.login || 'unknown'} · ${new Date(i.created_at).toLocaleDateString()}
            ${i.comments > 0 ? ' · 💬 ' + i.comments : ''}
          </div>
        </div>
        <div style="display:flex;gap:4px">${(i.labels || []).map(l => '<span class="badge" style="background:' + (l.color ? '#' + l.color + '33' : 'var(--surface)') + ';color:' + (l.color ? '#' + l.color : 'var(--text)') + ';font-size:0.7rem;padding:2px 6px;border-radius:4px">' + l.name + '</span>').join('')}</div>
      </div>
      ${i.body ? '<p style="color:var(--text-secondary);font-size:0.875rem;margin:0.5rem 0 0">' + (i.body.substring(0, 200) + (i.body.length > 200 ? '...' : '')) + '</p>' : ''}
    </div>
  `).join('');

  return `<div class="page-header"><h1>Community Issues</h1><p>${issues.length} open issues from <a href="https://github.com/mcgrof/ai-post-transformers/issues" target="_blank">GitHub</a></p></div>${cards}`;
}

function issuesPage() {
  return `
    <div class="page-header">
      <h1>Community Issues</h1>
      <p>GitHub issues and feature requests</p>
    </div>

    <div id="issues-container">
      <div class="loading">
        <div class="spinner"></div>
        Loading issues...
      </div>
    </div>

    <script>
      loadIssues();
    </script>
  `;
}

function automationPage(data) {
  const { adminEmail, adminId, service, timer, envFile, installCommands } = data;
  const esc = escapeHtml;
  return `
    <div class="page-header">
      <h1>Automation / Systemd</h1>
      <p>Podcast worker systemd units for <strong>${esc(adminEmail || adminId)}</strong></p>
      <p style="color:var(--text-secondary);font-size:0.813rem;margin-top:4px">
        The worker runs two phases each tick: (1) pick up pending submissions and generate drafts, (2) claim and process approved publish jobs.
      </p>
    </div>

    <div class="card" style="margin-bottom:1rem">
      <h3 style="margin:0 0 0.5rem">Service Unit</h3>
      <p style="color:var(--text-secondary);font-size:0.813rem;margin-bottom:0.5rem">
        Install as <code>~/.config/systemd/user/podcast-worker.service</code>
      </p>
      <pre style="background:var(--bg-tertiary);padding:1rem;border-radius:6px;overflow-x:auto;font-size:0.813rem;line-height:1.5"><code>${esc(service)}</code></pre>
    </div>

    <div class="card" style="margin-bottom:1rem">
      <h3 style="margin:0 0 0.5rem">Timer Unit</h3>
      <p style="color:var(--text-secondary);font-size:0.813rem;margin-bottom:0.5rem">
        Install as <code>~/.config/systemd/user/podcast-worker.timer</code>
      </p>
      <pre style="background:var(--bg-tertiary);padding:1rem;border-radius:6px;overflow-x:auto;font-size:0.813rem;line-height:1.5"><code>${esc(timer)}</code></pre>
    </div>

    <div class="card" style="margin-bottom:1rem">
      <h3 style="margin:0 0 0.5rem">Environment File</h3>
      <p style="color:var(--text-secondary);font-size:0.813rem;margin-bottom:0.5rem">
        Install as <code>~/.config/podcast-worker/env</code> — fill in credentials
      </p>
      <pre style="background:var(--bg-tertiary);padding:1rem;border-radius:6px;overflow-x:auto;font-size:0.813rem;line-height:1.5"><code>${esc(envFile)}</code></pre>
    </div>

    <div class="card" style="margin-bottom:1rem">
      <h3 style="margin:0 0 0.5rem">Install Commands</h3>
      <pre style="background:var(--bg-tertiary);padding:1rem;border-radius:6px;overflow-x:auto;font-size:0.813rem;line-height:1.5"><code>${esc(installCommands)}</code></pre>
    </div>
  `;
}

// ============================================================================
// CLIENT-SIDE JAVASCRIPT
// ============================================================================
const clientScript = `
let rejectingDraftKey = null;

// Audio seek helper
function seekAudio(btn, delta) {
  var a = btn.closest('.card, .draft-item').querySelector('audio');
  if (!a) return;
  var t = a.currentTime + delta;
  if (t < 0) t = 0;
  if (a.duration && isFinite(a.duration) && t > a.duration) t = a.duration;
  a.currentTime = t;
}

// Toast notifications
function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  toast.innerHTML = '<span>' + (type === 'success' ? '✓' : '✕') + '</span><span>' + message + '</span>';
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

async function copyReleaseTag(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const button = event && event.currentTarget ? event.currentTarget : null;
  const tag = button && button.dataset ? button.dataset.release : '${ADMIN_RELEASE_TAG}';

  async function tryNavigatorClipboard() {
    if (!navigator.clipboard || !navigator.clipboard.writeText) return false;
    await navigator.clipboard.writeText(tag);
    return true;
  }

  function tryExecCommand() {
    const textarea = document.createElement('textarea');
    textarea.value = tag;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '-1000px';
    textarea.style.left = '-1000px';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch (err) {
      ok = false;
    }
    textarea.remove();
    return ok;
  }

  function flashCopied() {
    if (!button) return;
    const previous = button.innerHTML;
    button.innerHTML = '<span aria-hidden="true">✅</span><span>Copied!</span>';
    setTimeout(() => {
      button.innerHTML = previous;
    }, 1200);
  }

  try {
    let copied = false;
    try {
      copied = await tryNavigatorClipboard();
    } catch (err) {
      copied = false;
    }
    if (!copied) {
      copied = tryExecCommand();
    }
    if (copied) {
      flashCopied();
      showToast('Copied release tag: ' + tag);
      return;
    }

    window.prompt('Copy release tag:', tag);
    showToast('Clipboard blocked — release tag shown for manual copy', 'error');
  } catch (err) {
    window.prompt('Copy release tag:', tag);
    showToast('Clipboard blocked — release tag shown for manual copy', 'error');
  }
}

// Drafts
function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function splitDraftSources(text) {
  const raw = String(text || '');
  const match = raw.match(/(?:\\n\\s*Sources:\\s*\\n?|Sources:\\s*)/i);
  if (!match) {
    return { body: raw.trim(), sources: '' };
  }
  const idx = match.index || 0;
  return {
    body: raw.slice(0, idx).trim(),
    sources: raw.slice(idx + match[0].length).trim(),
  };
}

function formatDraftSourcesHtml(sourcesRaw, preview = false) {
  if (!sourcesRaw) return '';
  let body = String(sourcesRaw || '');
  body = body.replace(/(\d+\.\s)/g, '\\n$1');
  body = body.replace(/(https?:\\/\\/)/g, '\\n$1');
  body = body.replace(/[ 	]+/g, ' ');
  body = body.replace(/\\n\\s*/g, '\\n').trim();

  const lines = body.split(/\\n+/).map(line => line.trim()).filter(Boolean);
  const rendered = [];
  let pendingTitle = null;
  let sourceCount = 0;

  function flushTitle() {
    if (pendingTitle) {
      rendered.push(escapeHtml(pendingTitle.trim()));
      pendingTitle = null;
      sourceCount += 1;
    }
  }

  for (const line of lines) {
    if (/^https?:\\/\\//i.test(line)) {
      flushTitle();
      const urls = line.match(/https?:\\/\\/[^\\s,)]+/g) || [];
      for (const url of urls) {
        const esc = escapeHtml(url);
        rendered.push('<a href="' + esc + '" target="_blank" rel="noopener noreferrer">' + esc + '</a>');
      }
      if (urls.length > 1) sourceCount += urls.length - 1;
      continue;
    }
    if (/^\d+\.\s/.test(line)) {
      flushTitle();
      pendingTitle = line;
      continue;
    }
    if (pendingTitle) pendingTitle += ' ' + line;
    else {
      rendered.push(escapeHtml(line));
      sourceCount += 1;
    }
  }
  flushTitle();

  const visible = preview ? rendered.slice(0, 4) : rendered;
  const label = preview && sourceCount > 0 ? 'Sources (' + sourceCount + ')' : 'Sources:';
  return '<div class="card-sources"><strong>' + label + '</strong><br><br>' + visible.join('<br>') + '</div>';
}

function formatDraftDescription(desc, preview = false) {
  const text = String(desc || 'No description available');
  const parts = splitDraftSources(text);
  const bodyText = preview && parts.body.length > 220 ? parts.body.slice(0, 220).trimEnd() + '...' : parts.body;

  let html = escapeHtml(bodyText).replace(/\\n/g, '<br>');
  html = html.replace(/(<br>\\s*){3,}/g, '<br><br>');
  if (parts.sources) {
    html += formatDraftSourcesHtml(parts.sources, preview);
  }
  return html;
}

function toggleDraftDescription(btn) {
  const wrap = btn.closest('.draft-description, .draft-desc-wrap');
  if (!wrap) return;
  const preview = wrap.querySelector('.desc-preview');
  const full = wrap.querySelector('.desc-full');
  const showingFull = full && full.style.display !== 'none';
  if (preview) preview.style.display = showingFull ? 'block' : 'none';
  if (full) full.style.display = showingFull ? 'none' : 'block';
  btn.textContent = showingFull ? 'Show more' : 'Show less';
}

async function loadDrafts() {
  // Skip if server-rendered content already present
  const dc = document.getElementById("drafts-container");
  if (!dc || !dc.querySelector(".loading")) return;

  const container = document.getElementById('drafts-container');
  try {
    const res = await fetch('/api/drafts', {credentials: 'same-origin'});
    const data = await res.json();

    if (!data.drafts || data.drafts.length === 0) {
      container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🎧</div><h3>No pending drafts</h3><p>All caught up! No drafts waiting for review.</p></div>';
      return;
    }

    container.innerHTML = '<div class="draft-list">' + data.drafts.map(function(draft) {
      return '<div class="draft-item" data-key="' + draft.key + '">'
        + '<div class="draft-header"><div><div class="draft-title">' + (draft.title || 'Untitled') + '</div>'
        + '<div class="draft-meta"><span>📅 ' + (draft.date || 'Unknown date') + '</span><span>⏱️ ' + (draft.duration || 'Unknown duration') + '</span></div>'
        + '</div></div>'
        + '<div class="draft-description" style="line-height:1.55">'
        + '<div class="desc-preview">' + formatDraftDescription(draft.description || 'No description available', true) + '</div>'
        + (((draft.description || 'No description available').length > 220)
            ? ('<div class="desc-full" style="display:none">' + formatDraftDescription(draft.description || 'No description available', false) + '</div>'
               + '<button type="button" onclick="toggleDraftDescription(this)" style="background:none;border:none;color:var(--accent);cursor:pointer;padding:0;font-size:0.8rem;margin-top:0.5rem">Show more</button>')
            : '')
        + '</div>'
        + '<div style="display:flex;align-items:center;gap:4px;margin:0.75rem 0">'
        + '<button onclick="seekAudio(this,-60)" style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:6px;padding:6px 8px;cursor:pointer;color:var(--text-primary);font-size:0.75rem" title="Rewind 1 min">⏪1m</button>'
        + '<button onclick="seekAudio(this,-15)" style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:6px;padding:6px 8px;cursor:pointer;color:var(--text-primary);font-size:0.75rem" title="Rewind 15s">⏪15s</button>'
        + '<audio controls preload="none" style="flex:1;height:40px"><source src="' + draft.audioUrl + '" type="audio/mpeg"></audio>'
        + '<button onclick="seekAudio(this,15)" style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:6px;padding:6px 8px;cursor:pointer;color:var(--text-primary);font-size:0.75rem" title="Forward 15s">15s⏩</button>'
        + '<button onclick="seekAudio(this,60)" style="background:var(--bg-tertiary);border:1px solid var(--border-color);border-radius:6px;padding:6px 8px;cursor:pointer;color:var(--text-primary);font-size:0.75rem" title="Forward 1 min">1m⏩</button>'
        + '</div>'
        + '<div class="draft-actions">'
        + renderDraftActionButtons(draft)
        + '</div>'
        + '</div>';
    }).join('') + '</div>';
  } catch (err) {
    container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">⚠️</div><h3>Failed to load drafts</h3><p>' + err.message + '</p></div>';
  }
}

async function adminApiFetch(path, options) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    redirect: 'manual',
    ...options,
  });
  if (res.type === 'opaqueredirect' || res.status === 0 || res.status === 302) {
    throw new Error('ACCESS_SESSION_EXPIRED');
  }
  const ct = (res.headers.get('content-type') || '');
  if (!ct.includes('application/json')) {
    throw new Error('ACCESS_SESSION_EXPIRED');
  }
  return res;
}

async function approveDraft(key) {
  try {
    const res = await adminApiFetch('/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, action: 'approve' })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Draft approved successfully');
      window.location.reload();
    } else {
      showToast(data.error || 'Failed to approve draft', 'error');
    }
  } catch (err) {
    if (err.message === 'ACCESS_SESSION_EXPIRED') {
      showToast('Session expired — reloading to re-authenticate...', 'error');
      setTimeout(function() { window.location.reload(); }, 1500);
      return;
    }
    showToast('Failed to approve draft: ' + err.message, 'error');
  }
}

function openRejectModal(key) {
  rejectingDraftKey = key;
  document.getElementById('reject-modal').style.display = 'flex';
  document.getElementById('reject-reason').value = '';
}

function closeRejectModal() {
  rejectingDraftKey = null;
  document.getElementById('reject-modal').style.display = 'none';
}

async function confirmReject() {
  const reason = document.getElementById('reject-reason').value.trim();
  if (!reason) {
    showToast('Please provide a reason for rejection', 'error');
    return;
  }
  try {
    const res = await adminApiFetch('/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: rejectingDraftKey, action: 'reject', reason })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Draft rejected');
      closeRejectModal();
      window.location.reload();
    } else {
      showToast(data.error || 'Failed to reject draft', 'error');
    }
  } catch (err) {
    if (err.message === 'ACCESS_SESSION_EXPIRED') {
      showToast('Session expired — reloading to re-authenticate...', 'error');
      setTimeout(function() { window.location.reload(); }, 1500);
      return;
    }
    showToast('Failed to reject draft: ' + err.message, 'error');
  }
}

// Queue
async function loadQueue() {
  // Skip if server-rendered content already present
  const qc = document.getElementById("queue-container");
  if (!qc || !qc.querySelector(".loading")) return;

  const container = document.getElementById('queue-container');
  try {
    const res = await fetch('/api/queue', {credentials: 'same-origin'});
    const data = await res.json();

    if (!data.papers || data.papers.length === 0) {
      container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📚</div><h3>Queue is empty</h3><p>No papers in the editorial queue.</p></div>';
      return;
    }

    container.innerHTML = '<div class="queue-list">' + data.papers.map((paper, i) => \`
      <div class="queue-item" onclick="toggleQueueItem(this)">
        <div class="queue-item-header">
          <div class="queue-item-main">
            <div class="queue-item-title">
              <span>\${i + 1}.</span>
              \${paper.title}
            </div>
            <div class="badges">
              \${(paper.taxonomy || []).map(t => '<span class="badge badge-purple">' + t + '</span>').join('')}
              \${(paper.badges || []).map(b => '<span class="badge badge-blue">' + b + '</span>').join('')}
            </div>
            <div class="queue-item-source">\${paper.source || 'Unknown source'}</div>
          </div>
          <div class="queue-item-score">\${paper.score || 0}</div>
          <span class="expand-icon">▼</span>
        </div>
        <div class="queue-item-body">
          <p class="queue-item-abstract">\${paper.abstract || 'No abstract available.'}</p>
          \${paper.scoring ? \`
            <div class="score-breakdown">
              \${Object.entries(paper.scoring).map(([k, v]) => \`
                <div class="score-item">
                  <div class="score-item-label">\${k}</div>
                  <div class="score-item-value">\${v}</div>
                </div>
              \`).join('')}
            </div>
          \` : ''}
          <button class="btn btn-primary" onclick="event.stopPropagation(); generatePodcast('\${paper.id}')">
            🎙️ Generate Podcast
          </button>
        </div>
      </div>
    \`).join('') + '</div>';
  } catch (err) {
    container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">⚠️</div><h3>Failed to load queue</h3><p>' + err.message + '</p></div>';
  }
}

function toggleQueueItem(el) {
  el.classList.toggle('expanded');
}

async function generatePodcast(paperId) {
  try {
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paperId })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Podcast generation queued');
    } else {
      showToast(data.error || 'Failed to queue generation', 'error');
    }
  } catch (err) {
    showToast('Failed to queue generation: ' + err.message, 'error');
  }
}

// Submissions
async function loadSubmissions() {
  const container = document.getElementById('submissions-container');
  if (!container) return;
  try {
    const res = await fetch('/api/submissions');
    const data = await res.json();

    if (!data.submissions || data.submissions.length === 0) {
      container.innerHTML = '<div class="empty-state" style="padding: 2rem 0;"><p style="color: var(--text-secondary);">No submissions yet</p></div>';
      return;
    }

    container.innerHTML = data.submissions.map(sub => {
      const statusColors = {submitted:'#58a6ff',pending:'#58a6ff',generation_claimed:'#d29922',generation_running:'#d29922',draft_generated:'#3fb950',generation_failed:'#f85149',approved_for_publish:'#a371f7',published:'#3fb950',rejected:'#f85149'};
      const statusLabels = {submitted:'Submitted',pending:'Submitted',generation_claimed:'Claimed',generation_running:'Generating',draft_generated:'Draft Ready',generation_failed:'Failed',approved_for_publish:'Approved',published:'Published',rejected:'Rejected'};
      const st = sub.status || 'submitted';
      const c = statusColors[st] || '#8b949e';
      const l = statusLabels[st] || st;
      const meta = sub.metadata || {};
      let urlsHtml = '';
      (sub.urls || [sub.url]).forEach(u => {
        const m = meta[u];
        const short = u.length > 80 ? u.substring(0, 77) + '...' : u;
        if (m && m.enrichment_status === 'done' && m.title) {
          urlsHtml += '<div style="margin-bottom:4px"><a href="' + escapeHtml(u) + '" target="_blank" style="color:var(--accent,#58a6ff);text-decoration:none;font-size:0.875rem"><strong>' + escapeHtml(m.title) + '</strong></a>';
          const parts = [];
          if (m.published) parts.push(m.published.substring(0, 10));
          if (m.arxiv_id) parts.push('arXiv:' + m.arxiv_id);
          if (parts.length) urlsHtml += '<div style="font-size:0.7rem;color:var(--text-muted,#8b949e)">' + escapeHtml(parts.join(' \\u00b7 ')) + '</div>';
          if (m.summary) {
            const snip = m.summary.length > 180 ? m.summary.substring(0, 177) + '...' : m.summary;
            urlsHtml += '<div style="font-size:0.75rem;color:var(--text-secondary,#8b949e);margin-top:1px">' + escapeHtml(snip) + '</div>';
          }
          urlsHtml += '</div>';
        } else if (m && m.enrichment_status === 'pending') {
          urlsHtml += '<div style="margin-bottom:4px"><div style="font-size:0.875rem">' + escapeHtml(short) + '</div><div style="font-size:0.7rem;color:var(--text-muted,#8b949e);font-style:italic">\\u23f3 Enriching metadata\\u2026</div></div>';
        } else {
          urlsHtml += '<div style="font-size:0.875rem;margin-bottom:4px">' + escapeHtml(short) + '</div>';
        }
      });
      const claimedLine = sub.claimed_by
        ? '<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:2px">Assigned to: ' + escapeHtml(sub.claimed_by) + '</div>'
        : '';
      const instrLine = sub.instructions
        ? '<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:2px;font-style:italic">' + escapeHtml(sub.instructions.substring(0, 120)) + (sub.instructions.length > 120 ? '...' : '') + '</div>'
        : '';
      const errorLine = sub.error
        ? '<div style="font-size:0.75rem;color:#f85149;margin-top:2px">Error: ' + escapeHtml(sub.error) + '</div>'
        : '';
      return \`<div class="submission-item" style="display:flex;justify-content:space-between;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--border-color,#30363d)">
        <div style="flex:1">\${urlsHtml}\${instrLine}\${claimedLine}\${errorLine}<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:2px">\${new Date(sub.timestamp).toLocaleString()}</div></div>
        <span style="padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:600;background:\${c}22;color:\${c};white-space:nowrap;margin-left:12px">\${l}</span>
      </div>\`;
    }).join('');
  } catch (err) {
    // Don't wipe server-rendered content on fetch failure (e.g. CF Access redirect).
    // Show a toast so the operator knows the refresh failed, but keep stale content visible.
    if (typeof showToast === 'function') {
      showToast('Could not refresh submissions list', 'error');
    }
  }
}

async function handleSubmit(e) {
  e.preventDefault();
  const urlsField = document.getElementById('urls');
  const instrField = document.getElementById('instructions');
  const submitBtn = e.target.querySelector('button[type="submit"]');
  const urls = urlsField.value.trim().split('\\n').filter(u => u.trim());

  if (urls.length === 0) {
    showToast('Please enter at least one URL', 'error');
    return;
  }

  // Basic URL validation
  const validUrlPattern = /^https?:\\/\\/.+/;
  const invalidUrls = urls.filter(u => !validUrlPattern.test(u.trim()));
  if (invalidUrls.length > 0) {
    showToast('Invalid URL format detected', 'error');
    return;
  }

  // Disable button and show progress
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting...';
  }

  try {
    const cleanUrls = urls.map(u => u.trim());
    const instrText = instrField.value.trim() || null;
    const res = await fetch('/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: cleanUrls, instructions: instrText })
    });
    if (!res.ok) {
      let msg = 'Server error (' + res.status + ')';
      try { const d = await res.json(); if (d.error) msg = d.error; } catch (_) {}
      showToast(msg, 'error');
      return;
    }
    const data = await res.json();
    if (data.success) {
      showToast('Submitted ' + data.count + ' paper' + (data.count !== 1 ? 's' : '') + ' — pending generation pickup');
      urlsField.value = '';
      instrField.value = '';
      // Optimistically render the new submission instead of re-fetching.
      // This avoids the failure mode where loadSubmissions() hits a CF Access
      // redirect and replaces the list with an error.
      const container = document.getElementById('submissions-container');
      if (container) {
        const urlsHtml = cleanUrls.map(u => '<div style="margin-bottom:4px"><div style="font-size:0.875rem">' + escapeHtml(u) + '</div><div style="font-size:0.7rem;color:var(--text-muted,#8b949e);font-style:italic">\\u23f3 Enriching metadata\\u2026</div></div>').join('');
        const instrHtml = instrText
          ? '<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:2px;font-style:italic">' + escapeHtml(instrText.substring(0, 120)) + (instrText.length > 120 ? '...' : '') + '</div>'
          : '';
        const now = new Date().toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
        const newRow = '<div style="padding:12px 0;border-bottom:1px solid var(--border-color,#30363d)">'
          + '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">'
          + '<div style="flex:1">' + urlsHtml + instrHtml
          + '<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:2px">' + now + '</div></div>'
          + '<span style="padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:600;background:#58a6ff22;color:#58a6ff;white-space:nowrap;margin-left:12px">Submitted</span>'
          + '</div></div>';
        // Remove empty-state placeholder if present
        const empty = container.querySelector('.empty-state');
        if (empty) empty.remove();
        container.insertAdjacentHTML('afterbegin', newRow);
      }
    } else {
      showToast(data.error || 'Failed to submit papers', 'error');
    }
  } catch (err) {
    showToast('Failed to submit: ' + err.message, 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit Papers';
    }
  }
}

// Issues
async function loadIssues() {
  // Skip if server-rendered content already present
  const ic = document.getElementById("issues-container");
  if (!ic || !ic.querySelector(".loading")) return;

  const container = document.getElementById('issues-container');
  try {
    const res = await fetch('/api/issues', {credentials: 'same-origin'});
    const data = await res.json();

    if (!data.issues || data.issues.length === 0) {
      container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">💬</div><h3>No open issues</h3><p>All community issues have been addressed.</p></div>';
      return;
    }

    container.innerHTML = data.issues.map(issue => \`
      <div class="issue-item">
        <div class="issue-header">
          <div class="issue-title">
            <a href="\${issue.html_url}" target="_blank">\${issue.title}</a>
            <span class="issue-number">#\${issue.number}</span>
          </div>
        </div>
        <div class="issue-labels">
          \${(issue.labels || []).map(label => \`
            <span class="issue-label" style="background: #\${label.color}22; color: #\${label.color};">\${label.name}</span>
          \`).join('')}
        </div>
        <div class="issue-meta">
          Opened by \${issue.user?.login || 'unknown'} • \${new Date(issue.created_at).toLocaleDateString()}
        </div>
      </div>
    \`).join('');
  } catch (err) {
    container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">⚠️</div><h3>Failed to load issues</h3><p>' + err.message + '</p></div>';
  }
}
`;

// ============================================================================
// WORKER HANDLER
// ============================================================================
export { displayTitle, humanizeSlug, OPAQUE_ID_RE };

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS headers for API
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      // API Routes
      if (path.startsWith('/api/')) {
        const apiResponse = await handleAPI(path, request, env, ctx);
        return new Response(JSON.stringify(apiResponse), {
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        });
      }

      // Page Routes
      let html;

      // Handle dynamic conference routes
      const confMatch = path.match(/^\/conference\/([a-z0-9]+)$/);

      switch (path) {
        case '/':
          const stats = await getDashboardStats(env);
          html = baseHTML('Dashboard', dashboardPage(stats), 'dashboard');
          break;
        case '/drafts':
          const draftsData = await getDrafts(env);
          html = baseHTML('Drafts', draftsPageWithData(draftsData), 'drafts');
          break;
        case '/queue':
          const queueData = await getQueue(env);
          const queueSubs = await getSubmissions(env);
          html = baseHTML('Queue', queuePageWithData(queueData, queueSubs), 'queue');
          break;
        case '/conferences':
          html = baseHTML('Conferences', conferencesPage(), 'conferences');
          break;
        case '/submit':
          const submitSubs = await getSubmissions(env);
          html = baseHTML('Submit', submitPage(submitSubs), 'submit');
          break;
        case '/issues':
          const issuesData = await getIssues();
          html = baseHTML('Issues', issuesPageWithData(issuesData), 'issues');
          break;
        case '/automation':
          const identity = getAdminIdentity(request);
          const systemdData = {
            adminEmail: identity.email,
            adminId: identity.id,
            service: generateSystemdService(identity.id),
            timer: generateSystemdTimer(),
            envFile: generateEnvFile(),
            installCommands: generateInstallCommands(),
          };
          html = baseHTML('Automation', automationPage(systemdData), 'automation');
          break;
        default:
          if (confMatch) {
            const confId = confMatch[1];
            const conf = CONFERENCES[confId];
            html = baseHTML(conf ? conf.name : 'Conference', conferenceDetailPage(confId), 'conferences');
          } else {
            html = baseHTML('Not Found', '<div class="empty-state"><div class="empty-state-icon">404</div><h3>Page not found</h3><p><a href="/">Return to dashboard</a></p></div>', '');
          }
      }

      return new Response(html, {
        headers: { 'Content-Type': 'text/html' }
      });

    } catch (error) {
      console.error('Worker error:', error);
      return new Response(JSON.stringify({ error: error.message }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' }
      });
    }
  }
};

// ============================================================================
// API HANDLERS
// ============================================================================
async function handleAPI(path, request, env, ctx) {
  switch (path) {
    case '/api/drafts':
      return await getDrafts(env);

    case '/api/queue':
      return await getQueue(env);

    case '/api/delegation':
      return await getDelegationExport(env);

    case '/api/version':
      return { release: ADMIN_RELEASE_TAG };

    case '/api/submissions':
      return await getSubmissions(env);

    case '/api/issues':
      return await getIssues();

    case '/api/submit':
      if (request.method !== 'POST') return { error: 'Method not allowed' };
      return await submitPapers(request, env, ctx);

    case '/api/submissions/enrich':
      if (request.method !== 'POST') return { error: 'Method not allowed' };
      return await triggerEnrichment(request, env, ctx);

    case '/api/submissions/status':
      if (request.method !== 'POST') return { error: 'Method not allowed' };
      return await updateSubmissionStatus(request, env);

    case '/api/review':
      if (request.method !== 'POST') return { error: 'Method not allowed' };
      return await reviewDraft(request, env);

    case '/api/publish':
      return await handlePublishAPI(request, env);

    case '/api/generate':
      if (request.method !== 'POST') return { error: 'Method not allowed' };
      return await generatePodcast(request, env);

    case '/api/systemd': {
      const identity = getAdminIdentity(request);
      return {
        admin_email: identity.email,
        admin_id: identity.id,
        service: generateSystemdService(identity.id),
        timer: generateSystemdTimer(),
        env_file: generateEnvFile(),
        install_commands: generateInstallCommands(),
      };
    }

    default:
      return { error: 'Not found' };
  }
}

// Dashboard stats
async function getDashboardStats(env) {
  const stats = {
    pendingDrafts: 0,
    queueSize: 0,
    submissions: 0,
    openIssues: 0
  };

  try {
    // Count drafts
    const drafts = await env.PODCAST_BUCKET.list({ prefix: 'drafts/' });
    stats.pendingDrafts = drafts.objects.filter(o => o.key.endsWith('.mp3')).length;

    // Count queue
    const queueData = await env.ADMIN_BUCKET.get('queue/latest.json');
    if (queueData) {
      const queue = await queueData.json();
      stats.queueSize = normalizeQueuePayload(queue).papers.length;
    }

    // Count submissions
    const submissions = await env.ADMIN_BUCKET.list({ prefix: 'submissions/' });
    stats.submissions = submissions.objects.length;

    // Count open issues (from GitHub)
    try {
      const issuesRes = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/issues?state=open&per_page=100`, {
        headers: { 'User-Agent': 'AI-Post-Transformers-Admin' }
      });
      if (issuesRes.ok) {
        const issues = await issuesRes.json();
        stats.openIssues = issues.length;
      }
    } catch (e) {
      // Ignore GitHub errors for stats
    }
  } catch (e) {
    console.error('Stats error:', e);
  }

  return stats;
}

// Get drafts from manifest.json in ADMIN_BUCKET
async function getDrafts(env) {
  try {
    // Read manifest.json for full episode metadata
    const manifestData = await env.ADMIN_BUCKET.get('manifest.json');
    let manifest = { drafts: [], conferences: {} };
    if (manifestData) {
      manifest = await manifestData.json();
    }

    // Get list of draft MP3s from PODCAST_BUCKET
    const list = await env.PODCAST_BUCKET.list({ prefix: 'drafts/' });
    const publishJobs = await listPublishJobs(env);
    const latestPublishJobByDraft = new Map();
    for (const job of publishJobs) {
      if (!job?.draft_key) continue;
      const current = latestPublishJobByDraft.get(job.draft_key);
      if (!current || (job.created_at || '') > (current.created_at || '')) {
        latestPublishJobByDraft.set(job.draft_key, job);
      }
    }
    const drafts = [];

    for (const obj of list.objects) {
      if (!obj.key.endsWith('.mp3')) continue;

      const filename = obj.key.split('/').pop();
      const baseName = filename.replace('.mp3', '');

      // Look up episode in manifest by matching filename or ID
      let episode = null;
      if (manifest.drafts) {
        episode = manifest.drafts.find(ep => {
          // Match by draft key, filename, or episode ID
          if (ep.draft_key === obj.key) return true;
          if (ep.filename === filename) return true;
          if (ep.basename === baseName) return true;
          if (baseName === `ep${ep.id}`) return true;
          return false;
        });
      }

      const draft = {
        key: obj.key,
        title: episode?.title || null,
        date: episode?.date || obj.uploaded?.toISOString().split('T')[0] || 'Unknown',
        duration: episode?.duration || 'Unknown',
        description: episode?.description || '',
        audioUrl: `${PODCAST_DOMAIN}/${obj.key}`,
        episodeId: episode?.id || null,
        publish_job: summarizePublishJob(latestPublishJobByDraft.get(obj.key)),
        revision: episode?.revision || null,
        revision_state: episode?.revision_state || null,
        episode_key: episode?.episode_key || null,
      };
      // Resolve display title through the fallback chain so opaque
      // identifiers like "ep102" never surface as the primary label.
      draft.title = displayTitle(draft);
      drafts.push(draft);
    }

    // Filter out superseded and rejected revisions from default view.
    // Also filter already-published episodes still lingering as drafts.
    const activeDrafts = drafts.filter(d => {
      const rs = d.revision_state;
      if (rs === 'superseded' || rs === 'rejected') return false;
      if (rs === 'published') return false;
      return true;
    });

    return { drafts: activeDrafts, all_drafts: drafts };
  } catch (error) {
    return { error: error.message, drafts: [] };
  }
}

// Get editorial queue
async function getQueue(env) {
  try {
    const queueData = await env.ADMIN_BUCKET.get('queue/latest.json');
    if (!queueData) {
      return normalizeQueuePayload(null);
    }
    const queue = await queueData.json();
    return normalizeQueuePayload(queue);
  } catch (error) {
    return { error: error.message, papers: [] };
  }
}

async function getDelegationExport(env) {
  try {
    const delegationData = await env.ADMIN_BUCKET.get('delegation/admin/latest.json');
    if (delegationData) {
      return await delegationData.json();
    }

    const queueData = await env.ADMIN_BUCKET.get('queue/latest.json');
    const queue = queueData ? normalizeQueuePayload(await queueData.json())
      : normalizeQueuePayload(null);

    return {
      manifest: {
        version: 0,
        jobs: [],
        volunteers: [],
        metrics: {
          jobs_claimed: 0,
          jobs_released: 0,
          jobs_succeeded: 0,
          jobs_failed: 0,
          by_volunteer: {},
          by_locale: {},
        },
      },
      admin_queue: queue,
      trust_boundaries: {
        trusted_operator: 'authoritative operator control plane',
        trusted_workers: 'authenticated workers claim from live state',
        static_exports: 'semi-trusted copies, never claim from them',
      },
    };
  } catch (error) {
    return {
      error: error.message,
      manifest: { version: 0, jobs: [], volunteers: [], metrics: {} },
      admin_queue: normalizeQueuePayload(null),
    };
  }
}

function normalizeQueuePayload(queue) {
  if (!queue) {
    return { papers: [], sections: {}, counts: {} };
  }

  if (Array.isArray(queue)) {
    return { papers: queue, sections: {}, counts: {} };
  }

  if (Array.isArray(queue.papers)) {
    return {
      papers: queue.papers,
      sections: queue.sections || {},
      counts: queue.counts || {},
      exported_at: queue.exported_at,
    };
  }

  const papers = [];
  const sections = {};
  const counts = {};

  for (const [sectionName, records] of Object.entries(queue)) {
    if (!Array.isArray(records)) {
      continue;
    }
    sections[sectionName] = records;
    counts[sectionName] = records.length;
    for (const record of records) {
      papers.push({
        queue_section: sectionName,
        score: queueScore(record),
        ...record,
      });
    }
  }

  return { papers, sections, counts };
}

function queueScore(record) {
  for (const key of ['max_axis_score', 'public_interest_score', 'memory_score']) {
    const value = record?.[key];
    if (typeof value === 'number') {
      return value;
    }
  }
  return 0;
}

// Get submissions — returns full records with status and R2 key
async function getSubmissions(env) {
  try {
    const list = await env.ADMIN_BUCKET.list({ prefix: 'submissions/' });
    const submissions = [];

    for (const obj of list.objects) {
      try {
        const data = await env.ADMIN_BUCKET.get(obj.key);
        if (data) {
          const sub = await data.json();
          if (sub.urls) {
            submissions.push({
              key: obj.key,
              urls: sub.urls,
              instructions: sub.instructions || null,
              timestamp: sub.timestamp,
              status: sub.status || 'submitted',
              claimed_by: sub.claimed_by || null,
              error: sub.error || null,
              draft_stem: sub.draft_stem || null,
              metadata: sub.metadata || null,
              updated_at: sub.updated_at || sub.timestamp,
            });
          }
        }
      } catch (e) {
        // Skip invalid entries
      }
    }

    // Sort by timestamp descending
    submissions.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    return { submissions: submissions.slice(0, 100) };
  } catch (error) {
    return { error: error.message, submissions: [] };
  }
}

// Get GitHub issues
async function getIssues() {
  try {
    const res = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/issues?state=open&per_page=50`, {
      headers: { 'User-Agent': 'AI-Post-Transformers-Admin' }
    });

    if (!res.ok) {
      return { error: 'Failed to fetch issues', issues: [] };
    }

    const issues = await res.json();
    return { issues };
  } catch (error) {
    return { error: error.message, issues: [] };
  }
}

// ============================================================================
// SUBMISSION METADATA ENRICHMENT
// ============================================================================

// Extract arXiv ID from various URL formats.
// Supports /abs/, /pdf/, and bare IDs like 2401.12345 or 2401.12345v2.
function extractArxivId(url) {
  const m = url.match(/arxiv\.org\/(?:abs|pdf|html)\/(\d{4}\.\d{4,5}(?:v\d+)?)/);
  if (m) return m[1].replace(/v\d+$/, '');
  return null;
}

// Fetch metadata for a single arXiv paper via the Atom API.
// Returns { title, published, summary } or null on failure.
async function fetchArxivMetadata(arxivId) {
  try {
    const res = await fetch(
      `https://export.arxiv.org/api/query?id_list=${arxivId}&max_results=1`,
      { headers: { 'User-Agent': 'AI-Post-Transformers-Admin/1.0' } }
    );
    if (!res.ok) return null;
    const xml = await res.text();

    // Parse title — strip newlines and collapse whitespace
    const titleMatch = xml.match(/<entry>[\s\S]*?<title>([\s\S]*?)<\/title>/);
    const title = titleMatch
      ? titleMatch[1].replace(/\s+/g, ' ').trim()
      : null;

    // Parse published date
    const pubMatch = xml.match(/<entry>[\s\S]*?<published>([\s\S]*?)<\/published>/);
    const published = pubMatch ? pubMatch[1].trim() : null;

    // Parse summary/abstract — strip newlines, collapse whitespace
    const sumMatch = xml.match(/<summary>([\s\S]*?)<\/summary>/);
    const summary = sumMatch
      ? sumMatch[1].replace(/\s+/g, ' ').trim()
      : null;

    if (!title) return null;
    return { title, published, summary };
  } catch (e) {
    return null;
  }
}

// Enrich a single submission record with metadata for each URL.
// Writes the enriched record back to R2. Safe to call multiple times.
async function enrichSubmissionMetadata(key, env) {
  try {
    const obj = await env.ADMIN_BUCKET.get(key);
    if (!obj) return;
    const sub = await obj.json();
    const urls = sub.urls || [];
    if (urls.length === 0) return;

    const metadata = sub.metadata || {};
    let changed = false;

    for (const url of urls) {
      // Skip URLs already enriched (done or unsupported)
      if (metadata[url] && metadata[url].enrichment_status !== 'pending') continue;

      const arxivId = extractArxivId(url);
      if (!arxivId) {
        // Non-arXiv URL — mark as unsupported with URL as fallback title
        metadata[url] = { enrichment_status: 'unsupported' };
        changed = true;
        continue;
      }

      const result = await fetchArxivMetadata(arxivId);
      if (result) {
        metadata[url] = {
          title: result.title,
          published: result.published,
          summary: result.summary,
          arxiv_id: arxivId,
          enrichment_status: 'done',
        };
      } else {
        metadata[url] = { enrichment_status: 'failed', arxiv_id: arxivId };
      }
      changed = true;
    }

    if (changed) {
      sub.metadata = metadata;
      await env.ADMIN_BUCKET.put(key, JSON.stringify(sub));
    }
  } catch (e) {
    // Enrichment is best-effort; don't propagate errors
  }
}

// Submission lifecycle statuses:
//   submitted            — URLs received, awaiting generation pickup
//   generation_claimed   — a worker has claimed this submission
//   generation_running   — gen-podcast.py is actively generating
//   draft_generated      — draft produced, awaiting review
//   generation_failed    — generation attempt failed
//   approved_for_publish — draft approved (maps to publish job)
//   published            — episode is live
//   rejected             — operator rejected the submission

const SUBMISSION_STATUSES = [
  'submitted',
  'generation_claimed',
  'generation_running',
  'draft_generated',
  'generation_failed',
  'approved_for_publish',
  'published',
  'rejected',
];

// Submit papers
async function submitPapers(request, env, ctx) {
  try {
    const { urls, instructions } = await request.json();

    if (!urls || !Array.isArray(urls) || urls.length === 0) {
      return { error: 'No URLs provided' };
    }

    // Validate URLs
    const validUrlPattern = /^https?:\/\/.+/;
    for (const url of urls) {
      if (!validUrlPattern.test(url)) {
        return { error: `Invalid URL: ${url}` };
      }
    }

    const timestamp = new Date().toISOString();
    const key = `submissions/${timestamp.replace(/[:.]/g, '-')}.json`;

    // Initialize metadata with pending status for each URL
    const metadata = {};
    for (const url of urls) {
      metadata[url] = { enrichment_status: 'pending' };
    }

    await env.ADMIN_BUCKET.put(key, JSON.stringify({
      urls,
      instructions: instructions || null,
      timestamp,
      status: 'submitted',
      metadata,
      status_history: [{ status: 'submitted', at: timestamp }],
    }));

    // Fire-and-forget metadata enrichment via arXiv API
    if (ctx && ctx.waitUntil) {
      ctx.waitUntil(enrichSubmissionMetadata(key, env));
    }

    return { success: true, count: urls.length };
  } catch (error) {
    return { error: error.message };
  }
}

// Manual re-enrichment trigger for one or all submissions
async function triggerEnrichment(request, env, ctx) {
  try {
    const body = await request.json();
    const key = body.key; // optional — enrich a specific submission

    if (key) {
      // Enrich a single submission
      if (ctx && ctx.waitUntil) {
        ctx.waitUntil(enrichSubmissionMetadata(key, env));
      } else {
        await enrichSubmissionMetadata(key, env);
      }
      return { success: true, key };
    }

    // Enrich all submissions that have pending metadata
    const list = await env.ADMIN_BUCKET.list({ prefix: 'submissions/' });
    const promises = [];
    for (const obj of list.objects) {
      promises.push(enrichSubmissionMetadata(obj.key, env));
    }
    if (ctx && ctx.waitUntil) {
      ctx.waitUntil(Promise.all(promises));
    } else {
      await Promise.all(promises);
    }
    return { success: true, count: list.objects.length };
  } catch (error) {
    return { error: error.message };
  }
}

// Update submission status (used by generation worker)
async function updateSubmissionStatus(request, env) {
  try {
    const { key, status, claimed_by, error: errorMsg, draft_stem } = await request.json();

    if (!key || !status) {
      return { error: 'Missing key or status' };
    }
    if (!SUBMISSION_STATUSES.includes(status)) {
      return { error: `Invalid status: ${status}` };
    }

    const existing = await env.ADMIN_BUCKET.get(key);
    if (!existing) {
      return { error: `Submission not found: ${key}` };
    }

    const sub = await existing.json();
    const now = new Date().toISOString();

    sub.status = status;
    sub.updated_at = now;
    if (claimed_by) sub.claimed_by = claimed_by;
    if (errorMsg) sub.error = errorMsg;
    if (draft_stem) sub.draft_stem = draft_stem;

    const history = sub.status_history || [];
    history.push({ status, at: now, ...(claimed_by ? { by: claimed_by } : {}) });
    sub.status_history = history;

    await env.ADMIN_BUCKET.put(key, JSON.stringify(sub));
    return { success: true, key, status };
  } catch (error) {
    return { error: error.message };
  }
}

// Review draft (approve/reject)
async function reviewDraft(request, env) {
  try {
    const body = await request.json();
    const {
      key,
      action,
      reason,
      jobId,
      adminId,
      adminName,
      leaseSeconds,
    } = body;
    const ts = Date.now();
    const effectiveAdminId = adminId || 'admin';
    const effectiveAdminName = adminName || effectiveAdminId;
    
    // Write review (append-only, race-free)
    const reviewTarget = key || jobId || 'unknown';
    const reviewKey = `reviews/${reviewTarget.replace(/\//g, '_')}/${ts}.json`;
    await env.ADMIN_BUCKET.put(reviewKey, JSON.stringify({
      key: key || null,
      job_id: jobId || null,
      action,
      reason: reason || '',
      admin_id: effectiveAdminId,
      admin_name: effectiveAdminName,
      timestamp: new Date().toISOString(),
    }));
    
    if (action === 'approve' || action === 'approve_for_publish') {
      if (!key) {
        return { error: 'Missing draft key' };
      }
      const job = await createOrUpdatePublishJob(env, {
        key,
        adminId: effectiveAdminId,
        adminName: effectiveAdminName,
      });
      return {
        success: true,
        action,
        key,
        publish_job: job,
      };
    }

    if (action === 'claim_publish') {
      const job = await updatePublishJobClaim(env, {
        jobId,
        adminId: effectiveAdminId,
        adminName: effectiveAdminName,
        leaseSeconds,
      });
      return { success: true, action, publish_job: job };
    }

    if (action === 'release_publish_claim') {
      const job = await releasePublishJob(env, {
        jobId,
        adminId: effectiveAdminId,
        reason,
      });
      return { success: true, action, publish_job: job };
    }

    if (action === 'retry_publish') {
      const job = await retryPublishJob(env, {
        jobId,
        adminId: effectiveAdminId,
        adminName: effectiveAdminName,
      });
      return { success: true, action, publish_job: job };
    }

    if (action === 'refresh_job_status') {
      const job = await loadPublishJobById(env, jobId);
      return { success: true, action, publish_job: job };
    }

    if (action === 'reject_episode') {
      // Reject all active draft revisions for a logical episode.
      // The episode_key is passed from the client side.
      const episodeKey = body.episode_key;
      if (!episodeKey) {
        return { error: 'Missing episode_key for reject_episode' };
      }
      // Mark the rejection in the review log (already done above).
      // The actual draft state cleanup is handled by the manifest
      // update on the Python/generation side. Record intent here.
      return {
        success: true,
        action,
        episode_key: episodeKey,
        message: `Rejection recorded for all drafts of "${episodeKey}"`,
      };
    }

    return { success: true, action, key };
  } catch (error) {
    return { error: error.message };
  }
}

async function handlePublishAPI(request, env) {
  try {
    if (request.method === 'GET') {
      const url = new URL(request.url);
      const jobId = url.searchParams.get('jobId');
      const draftKey = url.searchParams.get('draftKey');
      const job = jobId
        ? await loadPublishJobById(env, jobId)
        : await findLatestPublishJobForDraft(env, draftKey);
      return {
        success: true,
        action: 'get_publish_status',
        publish_job: summarizePublishJob(job),
      };
    }

    if (request.method !== 'POST') {
      return { error: 'Method not allowed' };
    }

    const body = await request.json();
    const action = body.action || 'get_publish_status';
    const effectiveAdminId = body.adminId || 'admin';
    const effectiveAdminName = body.adminName || effectiveAdminId;

    if (action === 'get_publish_status' || action === 'refresh_job_status') {
      const job = body.jobId
        ? await loadPublishJobById(env, body.jobId)
        : await findLatestPublishJobForDraft(env, body.draftKey);
      return {
        success: true,
        action,
        publish_job: summarizePublishJob(job),
      };
    }

    if (action === 'claim_publish') {
      const job = await updatePublishJobClaim(env, {
        jobId: body.jobId,
        adminId: effectiveAdminId,
        adminName: effectiveAdminName,
        leaseSeconds: body.leaseSeconds,
      });
      return { success: true, action, publish_job: job };
    }

    if (action === 'release_publish_claim') {
      const job = await releasePublishJob(env, {
        jobId: body.jobId,
        adminId: effectiveAdminId,
        reason: body.reason,
      });
      return { success: true, action, publish_job: job };
    }

    if (action === 'retry_publish') {
      const job = await retryPublishJob(env, {
        jobId: body.jobId,
        adminId: effectiveAdminId,
        adminName: effectiveAdminName,
      });
      return { success: true, action, publish_job: job };
    }

    return { error: `Unsupported publish action: ${action}` };
  } catch (error) {
    return { error: error.message };
  }
}

function makePublishJobId(date = new Date()) {
  const pad = (value) => String(value).padStart(2, '0');
  return [
    'pub',
    date.getUTCFullYear(),
    pad(date.getUTCMonth() + 1),
    pad(date.getUTCDate()),
    pad(date.getUTCHours()) + pad(date.getUTCMinutes()) + pad(date.getUTCSeconds()),
  ].join('_');
}

function createPublishJobRecord({
  jobId,
  key,
  title,
  episodeId,
  approvedByAdminId,
  approvedByName,
  createdAt,
}) {
  const draftStem = key.replace(/\.(mp3|txt|json|srt|png)$/i, '');
  const progress = {};
  for (const step of PUBLISH_JOB_PROGRESS_STEPS) {
    progress[step] = 'pending';
  }
  return {
    job_id: jobId,
    episode_id: episodeId || null,
    draft_id: episodeId || null,
    draft_key: key,
    draft_stem: draftStem,
    title: displayTitle({ title, key, draft_stem: draftStem }),
    state: 'approved_for_publish',
    created_at: createdAt,
    updated_at: createdAt,
    approved_by_admin_id: approvedByAdminId,
    approved_by_name: approvedByName,
    claimed_by_admin_id: null,
    claimed_by_name: null,
    claimed_at: null,
    lease_expires_at: null,
    last_heartbeat_at: null,
    released_at: null,
    release_reason: null,
    requirements: {
      viz: true,
      cover: true,
      publish_site: true,
      verify: true,
    },
    progress,
    step_timestamps: {},
    artifacts: {
      audio_url: null,
      srt_url: null,
      page_url: null,
      viz_url: null,
      cover_url: null,
      thumb_url: null,
    },
    error: null,
    history: [{
      timestamp: createdAt,
      action: 'created',
      state: 'approved_for_publish',
    }],
  };
}

function appendPublishJobHistory(job, action, extra = {}) {
  job.history = job.history || [];
  job.history.push({
    timestamp: new Date().toISOString(),
    action,
    ...extra,
  });
}

async function savePublishJob(env, job) {
  const record = publishJobRecordForSave(job);
  record.updated_at = new Date().toISOString();
  const key = `publish-jobs/${record.job_id}.json`;
  await env.ADMIN_BUCKET.put(key, JSON.stringify(record));
  return summarizePublishJob(record);
}

function publishJobRecordForSave(job) {
  if (!job) {
    return job;
  }
  const { claimed_by, lease, ...record } = job;
  return record;
}

async function loadPublishJobById(env, jobId, { summarize = true } = {}) {
  if (!jobId) {
    throw new Error('Missing job ID');
  }
  const obj = await env.ADMIN_BUCKET.get(`publish-jobs/${jobId}.json`);
  if (!obj) {
    throw new Error(`Publish job not found: ${jobId}`);
  }
  const job = await obj.json();
  return summarize ? summarizePublishJob(job) : job;
}

async function listPublishJobs(env) {
  const listed = await env.ADMIN_BUCKET.list({ prefix: 'publish-jobs/' });
  const jobs = [];
  for (const obj of listed.objects) {
    const current = await env.ADMIN_BUCKET.get(obj.key);
    if (!current) continue;
    jobs.push(summarizePublishJob(await current.json()));
  }
  return jobs;
}

async function findLatestPublishJobForDraft(env, draftKey, { summarize = true } = {}) {
  if (!draftKey) {
    return null;
  }
  const listed = await env.ADMIN_BUCKET.list({ prefix: 'publish-jobs/' });
  let latest = null;

  for (const obj of listed.objects) {
    const current = await env.ADMIN_BUCKET.get(obj.key);
    if (!current) continue;
    const job = await current.json();
    if (job.draft_key !== draftKey) continue;
    if (!latest || (job.created_at || '') > (latest.created_at || '')) {
      latest = job;
    }
  }

  return summarize ? summarizePublishJob(latest) : latest;
}

function summarizePublishJob(job) {
  if (!job) {
    return null;
  }
  const claimedByAdminId = job.claimed_by_admin_id || null;
  const claimedByName = job.claimed_by_name || null;
  const leaseExpiresAt = job.lease_expires_at || null;
  const lastHeartbeatAt = job.last_heartbeat_at || null;
  const leaseActive = !!(leaseExpiresAt && new Date(leaseExpiresAt) > new Date());
  return {
    ...job,
    claimed_by: claimedByAdminId ? {
      admin_id: claimedByAdminId,
      name: claimedByName || claimedByAdminId,
    } : null,
    lease: {
      active: leaseActive,
      claimed_at: job.claimed_at || null,
      lease_expires_at: leaseExpiresAt,
      last_heartbeat_at: lastHeartbeatAt,
    },
  };
}

async function findManifestDraft(env, draftKey) {
  const manifestData = await env.ADMIN_BUCKET.get('manifest.json');
  if (!manifestData) {
    return null;
  }
  const manifest = await manifestData.json();
  const filename = draftKey.split('/').pop();
  const basename = filename.replace(/\.mp3$/, '');
  return (manifest.drafts || []).find((draft) => {
    return draft.draft_key === draftKey
      || draft.filename === filename
      || draft.basename === basename
      || basename === `ep${draft.id}`;
  }) || null;
}

async function createOrUpdatePublishJob(env, { key, adminId, adminName }) {
  const now = new Date().toISOString();
  const existing = await findLatestPublishJobForDraft(env, key, { summarize: false });

  if (existing && ['publish_claimed', 'publish_running', 'publish_completed'].includes(existing.state)) {
    return existing;
  }

  if (existing) {
    existing.state = 'approved_for_publish';
    existing.approved_by_admin_id = adminId;
    existing.approved_by_name = adminName;
    existing.claimed_by_admin_id = null;
    existing.claimed_by_name = null;
    existing.claimed_at = null;
    existing.lease_expires_at = null;
    existing.last_heartbeat_at = null;
    existing.released_at = null;
    existing.release_reason = null;
    existing.error = null;
    for (const [step, status] of Object.entries(existing.progress || {})) {
      if (status === 'failed') {
        existing.progress[step] = 'pending';
      }
    }
    appendPublishJobHistory(existing, 'reapproved', {
      admin_id: adminId,
      admin_name: adminName,
    });
    return await savePublishJob(env, existing);
  }

  const draft = await findManifestDraft(env, key);
  const job = createPublishJobRecord({
    jobId: makePublishJobId(),
    key,
    title: draft?.title,
    episodeId: draft?.id || null,
    approvedByAdminId: adminId,
    approvedByName: adminName,
    createdAt: now,
  });
  appendPublishJobHistory(job, 'approved_for_publish', {
    admin_id: adminId,
    admin_name: adminName,
  });
  return await savePublishJob(env, job);
}

async function updatePublishJobClaim(env, { jobId, adminId, adminName, leaseSeconds }) {
  const job = await loadPublishJobById(env, jobId);
  const now = new Date();
  const activeLease = job.lease_expires_at && new Date(job.lease_expires_at) > now;
  if (activeLease && job.claimed_by_admin_id && job.claimed_by_admin_id !== adminId) {
    throw new Error(`Job already claimed by ${job.claimed_by_admin_id}`);
  }
  const seconds = Number(leaseSeconds || 900);
  job.state = 'publish_claimed';
  job.claimed_by_admin_id = adminId;
  job.claimed_by_name = adminName;
  job.claimed_at = now.toISOString();
  job.last_heartbeat_at = now.toISOString();
  job.lease_expires_at = new Date(now.getTime() + seconds * 1000).toISOString();
  appendPublishJobHistory(job, 'claimed', {
    admin_id: adminId,
    admin_name: adminName,
  });
  return await savePublishJob(env, job);
}

async function releasePublishJob(env, { jobId, adminId, reason }) {
  const job = await loadPublishJobById(env, jobId);
  if (job.claimed_by_admin_id && job.claimed_by_admin_id !== adminId) {
    throw new Error(`Job claimed by ${job.claimed_by_admin_id}`);
  }
  job.state = 'publish_released';
  job.claimed_by_admin_id = null;
  job.claimed_by_name = null;
  job.claimed_at = null;
  job.last_heartbeat_at = null;
  job.lease_expires_at = null;
  job.released_at = new Date().toISOString();
  job.release_reason = reason || '';
  appendPublishJobHistory(job, 'released', {
    admin_id: adminId,
    reason: reason || '',
  });
  return await savePublishJob(env, job);
}

async function retryPublishJob(env, { jobId, adminId, adminName }) {
  const job = await loadPublishJobById(env, jobId);
  job.state = 'approved_for_publish';
  job.error = null;
  job.claimed_by_admin_id = null;
  job.claimed_by_name = null;
  job.claimed_at = null;
  job.last_heartbeat_at = null;
  job.lease_expires_at = null;
  for (const step of PUBLISH_JOB_PROGRESS_STEPS) {
    if ((job.progress || {})[step] === 'failed') {
      job.progress[step] = 'pending';
    }
  }
  appendPublishJobHistory(job, 'retry_requested', {
    admin_id: adminId,
    admin_name: adminName,
  });
  return await savePublishJob(env, job);
}

// Queue podcast generation
async function generatePodcast(request, env) {
  try {
    const { paperId } = await request.json();

    if (!paperId) {
      return { error: 'Missing paper ID' };
    }

    const timestamp = new Date().toISOString();
    const actionKey = `actions/generate-${timestamp.replace(/[:.]/g, '-')}.json`;

    await env.ADMIN_BUCKET.put(actionKey, JSON.stringify({
      type: 'generate',
      paperId,
      timestamp,
      status: 'queued'
    }));

    return { success: true, paperId };
  } catch (error) {
    return { error: error.message };
  }
}
