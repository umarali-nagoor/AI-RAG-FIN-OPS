// Shared type definitions mirroring backend models.py

export type Impact = 'HIGH' | 'MEDIUM' | 'LOW'
export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW'
export type Decision = 'PASS' | 'WARN' | 'BLOCK'

export interface RepositoryInfo {
  name: string
  description: string
  languages: string[]
  available: boolean
  indexed: boolean
  commit: string | null
}

export interface Source {
  id: string
  type: string
  title: string
  path: string | null
  score: number | null
  snippet: string
}

export interface ReasonOut {
  reason: string
  impact: Impact
}

export interface AnalyzeResponse {
  repository: string
  riskLevel: Impact
  topReasons: ReasonOut[]
  estimatedOptimizationPotential: string
  optimizationRange: string
  platformCostShare: string | null
  costBasis: string
  estimatedGbPerDay: number
  estimatedUsdPerMonth: number
  filesIdentified: string[]
  rootCause: string
  sources: Source[]
  llmProvider: string
}

export interface RecommendationOut {
  issue: string
  location: string
  recommendation: string
  estimatedSavings: string
  impact: Impact
  confidence: Confidence
  rule: number
  currentCode: string | null
  suggestedCode: string | null
  estimatedUsdPerMonthSaved: number
}

export interface OptimizeResponse {
  repository: string
  recommendations: RecommendationOut[]
  estimatedOptimizationPotential: string
  guardrails: string[]
  summary: string
  sources: Source[]
  llmProvider: string
}

export interface PRFindingOut {
  type: string
  reason: string
  location: string
  level: string
  impact: Impact
  estimatedGbPerDay: number
  recommendation: string
  suggestedCode: string | null
}

export interface ReviewPRResponse {
  repository: string
  risk: Impact
  decision: Decision
  estimatedCostImpact: string
  estimatedUsdPerMonth: number
  findings: PRFindingOut[]
  recommendation: string
  summary: string
  newLogStatements: number
  removedLogStatements: number
  sources: Source[]
  llmProvider: string
}

export interface AskResponse {
  answer: string
  intent: string
  repository: string | null
  data: Record<string, unknown> | null
  sources: Source[]
  llmProvider: string
  grounded: boolean
}

export interface PRSummaryOut {
  number: number
  title: string
  author: string
  branch: string
  base: string
  state: string
  url: string
  createdAt: string
  updatedAt: string
}

export interface AnalyticsRepo {
  repository: string
  riskLevel: Impact
  estimatedGbPerDay: number
  estimatedUsdPerMonth: number
  estimatedOptimizationPotential: string
  costBasis: string
  filesIdentified: number
  languages: string[]
}
