import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import type { RepositoryInfo } from '../types'
import { Spinner, ErrorBox, Tag } from '../components/Shared'

export default function Repositories() {
  const [repos, setRepos] = useState<RepositoryInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.repositories()
      .then(setRepos)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <>
      <h2 className="page-title">Repository Picker</h2>
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
                      className="btn btn-primary"
                      disabled={!r.available}
                      onClick={() => navigate(`/analyze?repo=${r.name}`)}
                    >
                      Analyze
                    </button>
                    <button
                      className="btn btn-ghost"
                      disabled={!r.available}
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
