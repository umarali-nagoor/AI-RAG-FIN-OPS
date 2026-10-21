// Thin fetch wrapper — all requests go to /api/*

const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.error) msg = body.error
    } catch {
      // ignore parse errors; keep msg as HTTP status
    }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export const api = {
  repositories: () => request<import('./types').RepositoryInfo[]>('/repositories'),

  analyze: (repository: string) =>
    request<import('./types').AnalyzeResponse>('/analyze-repository', {
      method: 'POST',
      body: JSON.stringify({ repository }),
    }),

  optimize: (repository: string) =>
    request<import('./types').OptimizeResponse>('/optimize-repository', {
      method: 'POST',
      body: JSON.stringify({ repository }),
    }),

  reviewPR: (repository: string, diff: string) =>
    request<import('./types').ReviewPRResponse>('/review-pr', {
      method: 'POST',
      body: JSON.stringify({ repository, diff }),
    }),

  ask: (question: string, repository: string | null, diff?: string | null) =>
    request<import('./types').AskResponse>('/ask', {
      method: 'POST',
      body: JSON.stringify({ question, repository, diff }),
    }),

  analyticsRepositories: () =>
    request<{ repositories: import('./types').AnalyticsRepo[] }>('/analytics/repositories'),

  listPRs: (repository: string, state = 'open') =>
    request<import('./types').PRSummaryOut[]>(`/pull-requests/${encodeURIComponent(repository)}?state=${state}`),

  getPRDiff: (repository: string, prNumber: number) =>
    request<{ repository: string; pr_number: number; diff: string }>(
      `/pull-requests/${encodeURIComponent(repository)}/${prNumber}/diff`
    ),
}
