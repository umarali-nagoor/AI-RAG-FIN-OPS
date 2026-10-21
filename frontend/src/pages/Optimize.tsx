import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import type { RepositoryInfo, OptimizeResponse, RecommendationOut } from '../types'
import { Spinner, ErrorBox, Tag, Sources, ProviderBadge } from '../components/Shared'

// ── export helpers ──────────────────────────────────────────────────────────

/**
 * Markdown export — designed to be dropped directly into an AI coding tool
 * (GitHub Copilot, Cursor, Claude, etc.).  Each finding includes:
 *   - file path and rule number so the agent can locate the code
 *   - the problematic current code in a fenced block
 *   - the exact suggested replacement in a fenced block
 *   - cost impact so the agent can prioritise
 */
function buildMarkdown(repo: string, result: OptimizeResponse): string {
  const ts = new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
  const total = result.recommendations
    .reduce((s, r) => s + (r.estimatedUsdPerMonthSaved ?? 0), 0)

  const lines: string[] = [
    `# FinOps Cost-Reduction Recommendations`,
    ``,
    `**Repository:** \`${repo}\`  `,
    `**Generated:** ${ts}  `,
    `**Total optimisation potential:** ${result.estimatedOptimizationPotential}${total > 0 ? ` (~$${total.toFixed(2)}/mo)` : ''}  `,
    ``,
    `## Context`,
    ``,
    result.summary,
    ``,
    `## How to use this file`,
    ``,
    `Each finding below contains:`,
    `- The **file and line** to change (Location field)`,
    `- The **current code** that causes excess IBM Cloud Logs spend`,
    `- The **suggested replacement** that fixes the issue`,
    `- The **estimated monthly saving** for that change`,
    ``,
    `Paste this file into your AI coding assistant and ask:`,
    `> "Apply all the suggested code changes in this document to reduce IBM Cloud Logs costs."`,
    ``,
    `---`,
    ``,
  ]

  const sorted = [...result.recommendations].sort((a, b) => {
    const order: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 }
    return (order[a.impact] ?? 3) - (order[b.impact] ?? 3)
  })

  sorted.forEach((r, idx) => {
    const saving = r.estimatedUsdPerMonthSaved > 0
      ? ` · saves ~$${r.estimatedUsdPerMonthSaved.toFixed(2)}/mo`
      : r.estimatedSavings ? ` · ${r.estimatedSavings}` : ''

    lines.push(`## Finding ${idx + 1} — ${r.issue}`)
    lines.push(``)
    lines.push(`| Field | Value |`)
    lines.push(`|---|---|`)
    lines.push(`| **Impact** | ${r.impact} |`)
    lines.push(`| **Confidence** | ${r.confidence} |`)
    lines.push(`| **Rule** | #${r.rule} |`)
    lines.push(`| **Location** | \`${r.location}\` |`)
    lines.push(`| **Est. saving** | ${r.estimatedSavings}${saving} |`)
    lines.push(``)
    lines.push(`**Problem:** ${r.recommendation}`)
    lines.push(``)

    if (r.currentCode) {
      const lang = detectLang(r.location)
      lines.push(`### Current code (to replace)`)
      lines.push(``)
      lines.push(`\`\`\`${lang}`)
      lines.push(r.currentCode.trim())
      lines.push(`\`\`\``)
      lines.push(``)
    }

    if (r.suggestedCode) {
      const lang = detectLang(r.location)
      lines.push(`### Suggested replacement`)
      lines.push(``)
      lines.push(`\`\`\`${lang}`)
      lines.push(r.suggestedCode.trim())
      lines.push(`\`\`\``)
      lines.push(``)
    }

    lines.push(`---`)
    lines.push(``)
  })

  if (result.guardrails.length > 0) {
    lines.push(`## Guardrails`)
    lines.push(``)
    lines.push(`Do NOT make the following changes (they would break observability or SLAs):`)
    lines.push(``)
    result.guardrails.forEach(g => lines.push(`- ${g}`))
    lines.push(``)
  }

  return lines.join('\n')
}

function detectLang(location: string): string {
  if (location.endsWith('.go')) return 'go'
  if (location.endsWith('.java')) return 'java'
  if (location.endsWith('.py')) return 'python'
  if (location.endsWith('.ts') || location.endsWith('.tsx')) return 'typescript'
  if (location.endsWith('.js') || location.endsWith('.jsx')) return 'javascript'
  return ''
}

function buildJson(repo: string, result: OptimizeResponse): string {
  return JSON.stringify({
    schema_version: '1.0',
    repository: repo,
    exported_at: new Date().toISOString(),
    estimated_optimization_potential: result.estimatedOptimizationPotential,
    summary: result.summary,
    guardrails: result.guardrails,
    recommendations: result.recommendations.map(r => ({
      rule: r.rule,
      impact: r.impact,
      confidence: r.confidence,
      issue: r.issue,
      location: r.location,
      recommendation: r.recommendation,
      estimated_savings: r.estimatedSavings,
      estimated_usd_per_month_saved: r.estimatedUsdPerMonthSaved,
      current_code: r.currentCode ?? null,
      suggested_code: r.suggestedCode ?? null,
    })),
  }, null, 2)
}

function download(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function CodeBlock({ code, label }: { code: string; label: string }) {
  return (
    <details style={{ marginTop: '0.5rem' }}>
      <summary style={{ cursor: 'pointer', fontSize: '0.75rem', color: 'var(--text-muted)' }}>{label}</summary>
      <pre className="mono" style={{
        background: '#1e1e1e', color: '#d4d4d4', padding: '0.75rem',
        overflowX: 'auto', fontSize: '0.75rem', lineHeight: 1.5, marginTop: '0.375rem',
      }}>{code}</pre>
    </details>
  )
}

function RecRow({ r }: { r: RecommendationOut }) {
  return (
    <tr>
      <td><Tag value={r.impact} /></td>
      <td><Tag value={r.confidence} /></td>
      <td>
        <strong style={{ fontSize: '0.8125rem' }}>{r.issue}</strong>
        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 2 }}>{r.location}</div>
        {r.currentCode && <CodeBlock code={r.currentCode} label="current code" />}
        {r.suggestedCode && <CodeBlock code={r.suggestedCode} label="suggested code" />}
      </td>
      <td style={{ maxWidth: 320 }}>{r.recommendation}</td>
      <td className="mono" style={{ whiteSpace: 'nowrap' }}>
        {r.estimatedSavings}
        {r.estimatedUsdPerMonthSaved > 0 && (
          <div style={{ fontSize: '0.6875rem', color: 'var(--text-muted)' }}>
            ${r.estimatedUsdPerMonthSaved.toFixed(2)}/mo
          </div>
        )}
      </td>
      <td>{r.rule}</td>
    </tr>
  )
}

export default function Optimize() {
  const [searchParams] = useSearchParams()
  const [repos, setRepos] = useState<RepositoryInfo[]>([])
  const [repo, setRepo] = useState(searchParams.get('repo') ?? '')
  const [result, setResult] = useState<OptimizeResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const exportRef = useRef<HTMLDivElement>(null)

  useEffect(() => { api.repositories().then(setRepos).catch(() => {}) }, [])

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) {
        setExportOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  function run() {
    if (!repo) return
    setLoading(true); setError(null); setResult(null)
    api.optimize(repo)
      .then(setResult)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  function exportAs(fmt: 'markdown' | 'json') {
    if (!result) return
    const ts = new Date().toISOString().slice(0, 10)
    const slug = repo.replace(/[^a-z0-9-]/gi, '-').toLowerCase()
    if (fmt === 'markdown') {
      download(buildMarkdown(repo, result), `finops-recommendations-${slug}-${ts}.md`, 'text/markdown')
    } else {
      download(buildJson(repo, result), `finops-recommendations-${slug}-${ts}.json`, 'application/json')
    }
    setExportOpen(false)
  }

  return (
    <>
      <h2 className="page-title">Optimization Table</h2>

      <div className="card">
        <div className="section-label">Repository</div>
        <select value={repo} onChange={e => setRepo(e.target.value)} style={{ marginBottom: '1rem' }}>
          <option value="">— select repository —</option>
          {repos.filter(r => r.available).map(r => (
            <option key={r.name} value={r.name}>{r.name}</option>
          ))}
        </select>
        <button className="btn btn-primary" onClick={run} disabled={!repo || loading}>
          {loading ? <><Spinner />&nbsp;Optimizing…</> : 'Get Recommendations'}
        </button>
      </div>

      {error && <ErrorBox message={error} />}

      {result && (
        <>
          <div className="card" style={{ display: 'flex', gap: '1.5rem', alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span>
              <strong>Potential:</strong> {result.estimatedOptimizationPotential}
              <ProviderBadge provider={result.llmProvider} />
            </span>
            <span style={{ fontSize: '0.875rem', color: 'var(--text-muted)', flex: 1 }}>{result.summary}</span>

            {/* Export dropdown */}
            <div ref={exportRef} style={{ position: 'relative', marginLeft: 'auto' }}>
              <button
                className="btn btn-secondary"
                onClick={() => setExportOpen(o => !o)}
                title="Download recommendations"
                style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}
              >
                ↓ Export
              </button>
              {exportOpen && (
                <div style={{
                  position: 'absolute', right: 0, top: 'calc(100% + 4px)', zIndex: 20,
                  background: 'var(--surface)', border: '1px solid var(--border)',
                  borderRadius: 6, boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
                  minWidth: 160, overflow: 'hidden',
                }}>
                  <button
                    onClick={() => exportAs('markdown')}
                    style={{
                      display: 'block', width: '100%', textAlign: 'left',
                      padding: '0.6rem 1rem', background: 'none', border: 'none',
                      cursor: 'pointer', fontSize: '0.875rem',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--border)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                  >
                    ✦ AI Prompt (.md)
                  </button>
                  <button
                    onClick={() => exportAs('json')}
                    style={{
                      display: 'block', width: '100%', textAlign: 'left',
                      padding: '0.6rem 1rem', background: 'none', border: 'none',
                      cursor: 'pointer', fontSize: '0.875rem',
                      borderTop: '1px solid var(--border)',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--border)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                  >
                    {'{ }'} Machine-readable (.json)
                  </button>
                </div>
              )}
            </div>
          </div>

          {result.guardrails.length > 0 && (
            <div className="card" style={{ background: '#fef0d0', border: '1px solid #f1c21b' }}>
              <div className="section-label" style={{ color: '#8e4b00' }}>Guardrails</div>
              <ul style={{ paddingLeft: '1.25rem', fontSize: '0.8125rem', lineHeight: 2 }}>
                {result.guardrails.map((g, i) => <li key={i}>{g}</li>)}
              </ul>
            </div>
          )}

          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>Impact</th>
                  <th>Confidence</th>
                  <th>Issue / Location</th>
                  <th>Recommendation</th>
                  <th>Est. Savings</th>
                  <th>Rule</th>
                </tr>
              </thead>
              <tbody>
                {result.recommendations.map((r, i) => <RecRow key={i} r={r} />)}
              </tbody>
            </table>
          </div>

          <Sources sources={result.sources} />
        </>
      )}
    </>
  )
}
