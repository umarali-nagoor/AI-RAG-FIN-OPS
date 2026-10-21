import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import type { RepositoryInfo, AnalyzeResponse } from '../types'
import { Spinner, ErrorBox, Tag, Sources, ProviderBadge } from '../components/Shared'

function RepoSelect({
  repos, value, onChange,
}: { repos: RepositoryInfo[]; value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)} style={{ marginBottom: '1rem' }}>
      <option value="">— select repository —</option>
      {repos.filter(r => r.available).map(r => (
        <option key={r.name} value={r.name}>{r.name}</option>
      ))}
    </select>
  )
}

export default function Analyze() {
  const [searchParams] = useSearchParams()
  const [repos, setRepos] = useState<RepositoryInfo[]>([])
  const [repo, setRepo] = useState(searchParams.get('repo') ?? '')
  const [result, setResult] = useState<AnalyzeResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.repositories().then(setRepos).catch(() => {}) }, [])

  function run() {
    if (!repo) return
    setLoading(true); setError(null); setResult(null)
    api.analyze(repo)
      .then(setResult)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  return (
    <>
      <h2 className="page-title">Root-Cause Analysis</h2>

      <div className="card">
        <div className="section-label">Repository</div>
        <RepoSelect repos={repos} value={repo} onChange={setRepo} />
        <button className="btn btn-primary" onClick={run} disabled={!repo || loading}>
          {loading ? <><Spinner />&nbsp;Analyzing…</> : 'Run Analysis'}
        </button>
      </div>

      {error && <ErrorBox message={error} />}

      {result && (
        <>
          <div className="stat-row">
            <div className="stat-card">
              <div className="stat-label">Risk Level</div>
              <div className="stat-value"><Tag value={result.riskLevel} /></div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Optimization Potential</div>
              <div className="stat-value">{result.estimatedOptimizationPotential}</div>
              <div className="stat-sub">range: {result.optimizationRange}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Est. Cost / Month</div>
              <div className="stat-value">${result.estimatedUsdPerMonth.toFixed(2)}</div>
              <div className="stat-sub">{result.estimatedGbPerDay.toFixed(2)} GB/day · {result.costBasis}</div>
            </div>
            {result.platformCostShare && (
              <div className="stat-card">
                <div className="stat-label">Platform Share</div>
                <div className="stat-value">{result.platformCostShare}</div>
              </div>
            )}
          </div>

          <div className="card">
            <div className="section-label">Root Cause<ProviderBadge provider={result.llmProvider} /></div>
            <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>{result.rootCause}</p>
          </div>

          {result.topReasons.length > 0 && (
            <div className="card">
              <div className="section-label">Top Reasons</div>
              {result.topReasons.map((r, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                  <Tag value={r.impact} />
                  <span>{r.reason}</span>
                </div>
              ))}
            </div>
          )}

          {result.filesIdentified.length > 0 && (
            <div className="card">
              <div className="section-label">Files Identified ({result.filesIdentified.length})</div>
              <ul style={{ paddingLeft: '1.25rem', fontSize: '0.8125rem', lineHeight: 2 }}>
                {result.filesIdentified.map(f => (
                  <li key={f} className="mono">{f}</li>
                ))}
              </ul>
            </div>
          )}

          <Sources sources={result.sources} />
        </>
      )}
    </>
  )
}
