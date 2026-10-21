import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import type { RepositoryInfo } from '../types'
import { Spinner, ErrorBox, Tag } from '../components/Shared'

export default function Repositories() {
  const [repos, setRepos] = useState<RepositoryInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pulling, setPulling] = useState<string | null>(null)   // repo name being pulled, or 'all'
  const [pullMsg, setPullMsg] = useState<string | null>(null)
  const navigate = useNavigate()

  const refreshList = () =>
    api.repositories().then(setRepos).catch(() => {/* keep existing list on refresh failure */})

  useEffect(() => {
    api.repositories()
      .then(setRepos)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const handlePull = async (repoName?: string) => {
    const key = repoName ?? 'all'
    setPulling(key)
    setPullMsg(null)
    try {
      const result = await api.reindex(repoName)
      const count = result.reindexed.length
      setPullMsg(
        count > 0
          ? `✓ Pulled & reindexed: ${result.reindexed.join(', ')}`
          : `No repositories were updated${repoName ? ` (${repoName})` : ''}.`
      )
      await refreshList()
    } catch (e: unknown) {
      setPullMsg(`⚠ Pull failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setPulling(null)
    }
  }

  const anyPulling = pulling !== null

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1rem' }}>
        <h2 className="page-title" style={{ margin: 0 }}>Repository Picker</h2>
        <button
          className="btn btn-warning"
          disabled={anyPulling || loading}
          onClick={() => handlePull()}
          title="Pull latest code from GitHub for all repositories and reindex"
        >
          {pulling === 'all' ? <><span className="spinner" style={{ width: '0.875rem', height: '0.875rem' }} /> Pulling all…</> : '⬇ Pull All Repos'}
        </button>
      </div>

      {pullMsg && (
        <div className={`pull-msg ${pullMsg.startsWith('⚠') ? 'pull-msg-error' : 'pull-msg-ok'}`}>
          {pullMsg}
        </div>
      )}

      {loading && <Spinner />}
      {error && <ErrorBox message={error} />}
      {!loading && !error && (
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Languages</th>
                <th>Status</th>
                <th>Commit</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {repos.map(r => (
                <tr key={r.name}>
                  <td>
                    <strong>{r.name}</strong>
                    {r.description && (
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 2 }}>
                        {r.description}
                      </div>
                    )}
                  </td>
                  <td>{r.languages.join(', ') || '—'}</td>
                  <td>
                    {r.available
                      ? <Tag value={r.indexed ? 'LOW' : 'MEDIUM'} />
                      : <Tag value="HIGH" />
                    }
                    <span style={{ fontSize: '0.75rem', marginLeft: 4 }}>
                      {r.available ? (r.indexed ? 'indexed' : 'not indexed') : 'unavailable'}
                    </span>
                  </td>
                  <td className="mono">{r.commit ? r.commit.slice(0, 8) : '—'}</td>
                  <td style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <button
                      className="btn btn-warning"
                      disabled={anyPulling}
                      onClick={() => handlePull(r.name)}
                      title={`Pull latest code from GitHub for ${r.name}`}
                    >
                      {pulling === r.name
                        ? <><span className="spinner" style={{ width: '0.875rem', height: '0.875rem' }} /> Pulling…</>
                        : '⬇ Pull'
                      }
                    </button>
                    <button
                      className="btn btn-primary"
                      disabled={!r.available || anyPulling}
                      onClick={() => navigate(`/analyze?repo=${r.name}`)}
                    >
                      Analyze
                    </button>
                    <button
                      className="btn btn-ghost"
                      disabled={!r.available || anyPulling}
                      onClick={() => navigate(`/optimize?repo=${r.name}`)}
                    >
                      Optimize
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
