import { useEffect, useState } from 'react'
import { api } from '../api'
import type { PricingData } from '../types'
import { Spinner, ErrorBox } from '../components/Shared'

// Pipeline → severity levels that route through it
const PIPELINES = [
  {
    name: 'Priority Insights',
    levels: ['CRITICAL', 'ERROR'],
    color: '#750e13',
    bg: '#ffd7d9',
    priceKey: 'priority_insights' as const,
    description: 'Highest-cost pipeline. Logs stored with full search + alerting + retention tiers.',
  },
  {
    name: 'Analyze & Alert',
    levels: ['INFO', 'DEBUG'],
    color: '#8e4b00',
    bg: '#fef0d0',
    priceKey: 'analyze_and_alert' as const,
    description: 'Mid-cost pipeline. Logs ingested for search and alert evaluation.',
  },
  {
    name: 'Store & Search',
    levels: ['WARNING', 'VERBOSE'],
    color: '#044317',
    bg: '#defbe6',
    priceKey: 'store_and_search' as const,
    description: 'Lowest-cost pipeline. Logs stored for search only, no alerting.',
  },
]

const RETENTION_DAYS = [7, 14, 30, 60, 90]

function fmt(n: number) {
  return `$${n.toFixed(4)}`
}

export default function Pricing() {
  const [data, setData] = useState<PricingData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.pricing()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <>
      <h2 className="page-title">IBM Cloud Logs Pricing</h2>

      {loading && <Spinner />}
      {error && <ErrorBox message={error} />}

      {data && (
        <>
          {/* Source badge */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.75rem',
            marginBottom: '1.25rem', flexWrap: 'wrap',
          }}>
            <span style={{
              fontSize: '0.75rem', padding: '0.25rem 0.625rem',
              background: '#defbe6', color: '#044317',
              border: '1px solid #a7f0ba', borderRadius: 3, fontWeight: 600,
            }}>
              LIVE
            </span>
            <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
              Source: <strong style={{ color: 'var(--text)' }}>{data.source}</strong>
            </span>
            <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
              Effective from: <strong style={{ color: 'var(--text)' }}>{data.effective_from}</strong>
            </span>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
              Prices refresh every hour · USD per GB ingested
            </span>
          </div>

          {/* Pipeline cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1rem', marginBottom: '1.5rem' }}>
            {PIPELINES.map(p => {
              const price = p.priceKey === 'priority_insights'
                ? data.priority_insights_usd_gb['7'] ?? data.priority_insights_usd_gb[Object.keys(data.priority_insights_usd_gb)[0]]
                : p.priceKey === 'analyze_and_alert'
                  ? data.analyze_and_alert_usd_gb
                  : data.store_and_search_usd_gb
              return (
                <div key={p.name} className="card" style={{ borderTop: `3px solid ${p.color}` }}>
                  <div style={{ marginBottom: '0.5rem', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
                    <strong style={{ fontSize: '0.9375rem' }}>{p.name}</strong>
                    <span style={{ fontSize: '1.25rem', fontWeight: 700, color: p.color, fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(price)}<span style={{ fontSize: '0.6875rem', fontWeight: 400, color: 'var(--text-muted)' }}>/GB</span>
                    </span>
                  </div>
                  <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>{p.description}</p>
                  <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
                    {p.levels.map(l => (
                      <span key={l} style={{
                        fontSize: '0.6875rem', fontWeight: 600, padding: '1px 6px',
                        background: p.bg, color: p.color, borderRadius: 2,
                      }}>{l}</span>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>

          {/* Priority Insights retention table */}
          <div className="card">
            <div className="section-label" style={{ marginBottom: '0.75rem' }}>
              Priority Insights — price by retention period
            </div>
            <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.875rem' }}>
              CRITICAL and ERROR logs route here. Longer retention = higher price per GB.
            </p>
            <div className="tbl-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Retention</th>
                    <th>USD / GB</th>
                    <th>USD / GB / month (100 GB/day)</th>
                    <th>vs 7-day</th>
                  </tr>
                </thead>
                <tbody>
                  {RETENTION_DAYS.map(days => {
                    const price = data.priority_insights_usd_gb[String(days)] ?? null
                    const base = data.priority_insights_usd_gb['7'] ?? 1
                    const monthly = price != null ? price * 100 * 30 : null
                    const mult = price != null ? (price / base) : null
                    return (
                      <tr key={days}>
                        <td><strong>{days} days</strong></td>
                        <td className="mono">{price != null ? fmt(price) : '—'}</td>
                        <td className="mono">{monthly != null ? `$${monthly.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}</td>
                        <td style={{ color: days === 7 ? 'var(--text-muted)' : '#750e13', fontWeight: days === 7 ? 400 : 600 }}>
                          {days === 7 ? 'baseline' : mult != null ? `${mult.toFixed(2)}×` : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* How this affects cost estimates */}
          <div className="card" style={{ marginTop: '1rem' }}>
            <div className="section-label" style={{ marginBottom: '0.5rem' }}>How these prices drive cost estimates</div>
            <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', lineHeight: 1.7 }}>
              <p>Every log statement extracted from a repository is assigned a severity level. The severity determines which pipeline it routes to, and the pipeline price × estimated GB/day gives the monthly cost estimate shown in Analyze, Optimize and PR Review.</p>
              <p style={{ marginTop: '0.5rem' }}>Prices are fetched live from the <strong>IBM Global Catalog API</strong> at server startup and refreshed every hour. If the catalog is unreachable, the last known values are used as a fallback.</p>
            </div>
          </div>
        </>
      )}
    </>
  )
}
