import type { Source } from '../types'

export function Tag({ value }: { value: string }) {
  return <span className={`tag tag-${value}`}>{value}</span>
}

export function Spinner() {
  return <span className="spinner" role="status" aria-label="Loading" />
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="error-box">⚠ {message}</div>
}

export function Sources({ sources }: { sources: Source[] }) {
  if (!sources.length) return null
  return (
    <details className="sources">
      <summary>{sources.length} source{sources.length !== 1 ? 's' : ''}</summary>
      <div style={{ marginTop: '0.5rem' }}>
        {sources.map(s => (
          <div key={s.id} className="source-item">
            <span className="mono" style={{ color: 'var(--text-muted)' }}>{s.type}</span>
            <span style={{ flex: 1 }}>{s.title}{s.path ? ` — ${s.path}` : ''}</span>
            {s.score != null && (
              <span style={{ color: 'var(--text-muted)' }}>{(s.score * 100).toFixed(0)}%</span>
            )}
          </div>
        ))}
      </div>
    </details>
  )
}

export function ProviderBadge({ provider }: { provider: string }) {
  return (
    <span style={{ fontSize: '0.6875rem', color: 'var(--text-muted)', marginLeft: '0.5rem' }}>
      via {provider}
    </span>
  )
}
