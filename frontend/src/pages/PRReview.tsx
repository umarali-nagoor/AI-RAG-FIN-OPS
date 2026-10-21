import { useEffect, useState } from 'react'
import { api } from '../api'
import type { RepositoryInfo, ReviewPRResponse, PRFindingOut, PRSummaryOut } from '../types'
import { Spinner, ErrorBox, Tag, Sources, ProviderBadge } from '../components/Shared'
import { Light as SyntaxHighlighter } from 'react-syntax-highlighter'
import diffLang from 'react-syntax-highlighter/dist/esm/languages/hljs/diff'
import { githubGist } from 'react-syntax-highlighter/dist/esm/styles/hljs'

SyntaxHighlighter.registerLanguage('diff', diffLang)

// ── Sub-components ────────────────────────────────────────────────────────────

function FindingCard({ f }: { f: PRFindingOut }) {
  return (
    <div className={`finding-card ${f.impact}`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.375rem' }}>
        <Tag value={f.impact} />
        <strong style={{ fontSize: '0.8125rem' }}>{f.type}</strong>
        <span className="mono" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{f.location}</span>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
          level: {f.level} · ~{f.estimatedGbPerDay.toFixed(3)} GB/day
        </span>
      </div>
      <p style={{ fontSize: '0.8125rem', marginBottom: '0.375rem' }}>{f.reason}</p>
      <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>{f.recommendation}</p>
      {f.suggestedCode && (
        <details style={{ marginTop: '0.375rem' }}>
          <summary style={{ cursor: 'pointer', fontSize: '0.75rem', color: 'var(--text-muted)' }}>suggested fix</summary>
          <pre className="mono" style={{
            background: '#1e1e1e', color: '#d4d4d4', padding: '0.75rem',
            overflowX: 'auto', fontSize: '0.75rem', lineHeight: 1.5, marginTop: '0.25rem',
          }}>{f.suggestedCode}</pre>
        </details>
      )}
    </div>
  )
}

function PRList({
  prs, loading, error, onSelect,
}: { prs: PRSummaryOut[]; loading: boolean; error: string | null; onSelect: (pr: PRSummaryOut) => void }) {
  if (loading) return <div style={{ padding: '0.5rem' }}><Spinner /></div>
  if (error) {
    const isNotConfigured = error.includes('501') || error.includes('not configured')
    return (
      <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', padding: '0.5rem' }}>
        {isNotConfigured
          ? <>GitHub not configured — add <code>FINOPS_GITHUB_TOKEN</code> and <code>FINOPS_GITHUB_ORG</code> to your <code>.env</code> file, or paste a diff manually below.</>
          : <ErrorBox message={error} />}
      </div>
    )
  }
  if (!prs.length) return <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', padding: '0.5rem' }}>No open PRs found.</p>
  return (
    <div className="tbl-wrap" style={{ marginBottom: '0.75rem' }}>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Title</th>
            <th>Author</th>
            <th>Branch → Base</th>
            <th>Updated</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {prs.map(pr => (
            <tr key={pr.number}>
              <td className="mono" style={{ color: 'var(--text-muted)' }}>{pr.number}</td>
              <td>
                <a href={pr.url} target="_blank" rel="noopener noreferrer"
                  style={{ color: 'var(--accent)', textDecoration: 'none', fontSize: '0.8125rem' }}>
                  {pr.title}
                </a>
              </td>
              <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{pr.author}</td>
              <td className="mono" style={{ fontSize: '0.6875rem', color: 'var(--text-muted)' }}>
                {pr.branch} → {pr.base}
              </td>
              <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                {new Date(pr.updatedAt).toLocaleDateString()}
              </td>
              <td>
                <button className="btn btn-primary" style={{ padding: '0.25rem 0.75rem', fontSize: '0.75rem' }}
                  onClick={() => onSelect(pr)}>
                  Load diff
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const PLACEHOLDER = `diff --git a/services/workflow.go b/services/workflow.go
--- a/services/workflow.go
+++ b/services/workflow.go
@@ -45,6 +45,10 @@
 func (s *WorkflowService) ReconcileAll(ctx context.Context, workspaces []Workspace) error {
     for _, ws := range workspaces {
+        log.Infof("Reconciling workspace: %+v", ws)
+        log.Infof("Workspace state: %+v", ws.State)
         result, err := s.reconcile(ctx, ws)
         if err != nil {
+            log.Errorf("Reconcile failed for workspace %+v with error: %+v", ws, err)
             return fmt.Errorf("reconcile %s: %w", ws.ID, err)
         }
     }
     return nil
 }
`

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PRReview() {
  const [repos, setRepos] = useState<RepositoryInfo[]>([])
  const [repo, setRepo] = useState('')

  // PR picker state
  const [prs, setPRs] = useState<PRSummaryOut[]>([])
  const [prsLoading, setPRsLoading] = useState(false)
  const [prsError, setPRsError] = useState<string | null>(null)
  const [selectedPR, setSelectedPR] = useState<PRSummaryOut | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)

  // Diff + result state
  const [diffText, setDiffText] = useState('')
  const [result, setResult] = useState<ReviewPRResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.repositories().then(setRepos).catch(() => {}) }, [])

  // When repo changes: reset everything and load PRs
  function handleRepoChange(name: string) {
    setRepo(name)
    setDiffText('')
    setResult(null)
    setError(null)
    setSelectedPR(null)
    setPRs([])
    if (!name) return
    setPRsLoading(true)
    setPRsError(null)
    api.listPRs(name)
      .then(setPRs)
      .catch(e => setPRsError(e.message))
      .finally(() => setPRsLoading(false))
  }

  // Load diff from GitHub when a PR row is clicked
  function handleLoadDiff(pr: PRSummaryOut) {
    setSelectedPR(pr)
    setDiffText('')
    setResult(null)
    setError(null)
    setDiffLoading(true)
    api.getPRDiff(repo, pr.number)
      .then(r => setDiffText(r.diff))
      .catch(e => setError(`Could not fetch diff: ${e.message}`))
      .finally(() => setDiffLoading(false))
  }

  function run() {
    if (!repo || !diffText.trim()) return
    setLoading(true); setError(null); setResult(null)
    api.reviewPR(repo, diffText)
      .then(setResult)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  function clearDiff() {
    setDiffText('')
    setSelectedPR(null)
    setResult(null)
    setError(null)
  }

  return (
    <>
      <h2 className="page-title">PR Diff Review</h2>

      {/* Step 1 — pick repository */}
      <div className="card">
        <div className="section-label">1 · Select Repository</div>
        <select value={repo} onChange={e => handleRepoChange(e.target.value)}>
          <option value="">— select repository —</option>
          {repos.filter(r => r.available).map(r => (
            <option key={r.name} value={r.name}>{r.name}</option>
          ))}
        </select>
      </div>

      {/* Step 2 — pick PR or paste diff */}
      {repo && (
        <div className="card">
          <div className="section-label">2 · Select Open PR</div>
          <PRList prs={prs} loading={prsLoading} error={prsError} onSelect={handleLoadDiff} />

          {/* Diff area */}
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: '0.375rem' }}>
            <div className="section-label" style={{ margin: 0 }}>
              {selectedPR
                ? <>Diff — PR #{selectedPR.number}: {selectedPR.title}</>
                : 'Or paste a unified diff manually'}
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              {!diffText && !diffLoading && (
                <button className="btn btn-ghost" style={{ padding: '0.125rem 0.625rem', fontSize: '0.75rem' }}
                  onClick={() => setDiffText(PLACEHOLDER)}>
                  Load example
                </button>
              )}
              {diffText && (
                <button className="btn btn-ghost" style={{ padding: '0.125rem 0.625rem', fontSize: '0.75rem' }}
                  onClick={clearDiff}>
                  Clear
                </button>
              )}
            </div>
          </div>

          {diffLoading && (
            <div style={{ padding: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-muted)', fontSize: '0.875rem' }}>
              <Spinner /> Fetching diff from GitHub…
            </div>
          )}

          {diffText && !diffLoading && (
            <div className="diff-wrap">
              <SyntaxHighlighter language="diff" style={githubGist} customStyle={{ margin: 0, fontSize: '0.75rem' }}>
                {diffText}
              </SyntaxHighlighter>
            </div>
          )}

          <textarea
            rows={diffText ? 4 : 8}
            placeholder={`Paste a unified diff here (e.g. git diff main...my-branch)…\n\n${PLACEHOLDER}`}
            value={diffText}
            onChange={e => { setDiffText(e.target.value); setSelectedPR(null) }}
            style={{ marginBottom: '0.5rem', fontFamily: 'monospace', fontSize: '0.8125rem' }}
          />

          {!diffText.trim() && !diffLoading && (
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
              ↑ Click <strong>Load diff</strong> on a PR above, or paste a <code>git diff</code> manually.
            </p>
          )}
        </div>
      )}

      {/* Step 3 — review button */}
      {repo && (
        <div style={{ marginBottom: '1rem' }}>
          <button className="btn btn-primary" onClick={run}
            disabled={!repo || !diffText.trim() || loading || diffLoading}>
            {loading ? <><Spinner />&nbsp;Reviewing…</> : '▶  Review PR'}
          </button>
        </div>
      )}

      {error && <ErrorBox message={error} />}

      {/* Results */}
      {result && (
        <>
          <div className="stat-row">
            <div className="stat-card">
              <div className="stat-label">Decision</div>
              <div className="stat-value"><Tag value={result.decision} /></div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Risk</div>
              <div className="stat-value"><Tag value={result.risk} /></div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Cost Impact</div>
              <div className="stat-value">{result.estimatedCostImpact}</div>
              <div className="stat-sub">${result.estimatedUsdPerMonth.toFixed(2)}/mo</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Statements</div>
              <div className="stat-value">+{result.newLogStatements} / -{result.removedLogStatements}</div>
              <div className="stat-sub">added / removed</div>
            </div>
          </div>

          <div className="card">
            <div className="section-label">Summary<ProviderBadge provider={result.llmProvider} /></div>
            <p style={{ marginBottom: '0.5rem', whiteSpace: 'pre-wrap' }}>{result.summary}</p>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{result.recommendation}</p>
          </div>

          {result.findings.length > 0 && (
            <div>
              <div className="section-label" style={{ marginBottom: '0.5rem' }}>
                Findings ({result.findings.length})
              </div>
              {result.findings.map((f, i) => <FindingCard key={i} f={f} />)}
            </div>
          )}

          <Sources sources={result.sources} />
        </>
      )}
    </>
  )
}
