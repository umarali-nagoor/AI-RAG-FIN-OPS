import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { RepositoryInfo, AskResponse, Source } from '../types'
import { Spinner, ErrorBox, Sources } from '../components/Shared'

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  provider?: string
  timestamp: string
}

function now() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function Ask() {
  const [repos, setRepos] = useState<RepositoryInfo[]>([])
  const [repo, setRepo] = useState('')
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => { api.repositories().then(setRepos).catch(() => {}) }, [])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  function send() {
    const q = question.trim()
    if (!q || loading) return
    setQuestion('')
    setError(null)
    const userMsg: Message = { role: 'user', content: q, timestamp: now() }
    setMessages(prev => [...prev, userMsg])
    setLoading(true)
    api.ask(q, repo || null)
      .then((r: AskResponse) => {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: r.answer,
          sources: r.sources,
          provider: r.llmProvider,
          timestamp: now(),
        }])
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  function handleKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  return (
    <>
      <h2 className="page-title">Ask</h2>

      <div className="card" style={{ marginBottom: '0.5rem' }}>
        <div className="section-label">Repository context (optional)</div>
        <select value={repo} onChange={e => setRepo(e.target.value)}>
          <option value="">— all repositories —</option>
          {repos.filter(r => r.available).map(r => (
            <option key={r.name} value={r.name}>{r.name}</option>
          ))}
        </select>
      </div>

      <div className="chat-history">
        {messages.length === 0 && (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.875rem', alignSelf: 'center', margin: 'auto' }}>
            Ask anything about logging costs, findings, or optimization strategies.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg msg-${m.role}`}>
            <div className="msg-bubble">{m.content}</div>
            <div className="msg-meta">
              {m.role === 'assistant' && m.provider && `${m.provider} · `}
              {m.timestamp}
            </div>
            {m.role === 'assistant' && m.sources && m.sources.length > 0 && (
              <div style={{ fontSize: '0.75rem' }}>
                <Sources sources={m.sources} />
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="msg msg-assistant">
            <div className="msg-bubble"><Spinner /></div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && <ErrorBox message={error} />}

      <div className="chat-form">
        <input
          type="text"
          placeholder="Ask a question… (Enter to send)"
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={handleKey}
          disabled={loading}
          aria-label="Question"
        />
        <button className="btn btn-primary" onClick={send} disabled={!question.trim() || loading}>
          Send
        </button>
      </div>
    </>
  )
}
