import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AnalyticsRepo, Impact } from '../types'
import { Spinner, ErrorBox, Tag } from '../components/Shared'

function RiskBar({ pct }: { pct: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <div className="rank-bar-bg">
        <div className="rank-bar-fill" style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', minWidth: 36 }}>{pct.toFixed(0)}%</span>
    </div>
  )
}

const RISK_ORDER: Record<Impact, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 }

export default function Analytics() {
  const [data, setData] = useState<AnalyticsRepo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState<'usd' | 'gb' | 'risk'>('usd')

  useEffect(() => {
    api.analyticsRepositories()
      .then(r => setData(r.repositories))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const sorted = [...data].sort((a, b) => {
    if (sortBy === 'usd') return b.estimatedUsdPerMonth - a.estimatedUsdPerMonth
    if (sortBy === 'gb') return b.estimatedGbPerDay - a.estimatedGbPerDay
    return RISK_ORDER[a.riskLevel] - RISK_ORDER[b.riskLevel]
  })

  const maxUsd = Math.max(...data.map(r => r.estimatedUsdPerMonth), 1)

  return (
    <>
      <h2 className="page-title">Platform Ranking</h2>

      {loading && <Spinner />}
      {error && <ErrorBox message={error} />}

      {!loading && !error && (
        <>
          <div style={{ marginBottom: '1rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <span style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>Sort by:</span>
            {(['usd', 'gb', 'risk'] as const).map(s => (
              <button
                key={s}
                className={`btn ${sortBy === s ? 'btn-primary' : 'btn-ghost'}`}
                style={{ padding: '0.25rem 0.75rem', fontSize: '0.8125rem' }}
                onClick={() => setSortBy(s)}
              >
                {s === 'usd' ? '$/month' : s === 'gb' ? 'GB/day' : 'Risk'}
              </button>
            ))}
          </div>

          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Repository</th>
                  <th>Risk</th>
                  <th>Cost / Month</th>
                  <th>GB / Day</th>
                  <th>Potential</th>
                  <th>Basis</th>
                  <th>Languages</th>
                  <th>Cost Share</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={r.repository}>
                    <td style={{ color: 'var(--text-muted)', textAlign: 'right' }}>{i + 1}</td>
                    <td><strong>{r.repository}</strong></td>
                    <td><Tag value={r.riskLevel} /></td>
                    <td className="mono">${r.estimatedUsdPerMonth.toFixed(2)}</td>
                    <td className="mono">{r.estimatedGbPerDay.toFixed(2)}</td>
                    <td>{r.estimatedOptimizationPotential}</td>
                    <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{r.costBasis}</td>
                    <td style={{ fontSize: '0.75rem' }}>{r.languages?.join(', ') || '—'}</td>
                    <td style={{ minWidth: 120 }}>
                      <RiskBar pct={(r.estimatedUsdPerMonth / maxUsd) * 100} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}
