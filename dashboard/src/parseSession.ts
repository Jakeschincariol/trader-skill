import type {
  AnalysisJson,
  BacktestResult,
  NewsItem,
  Portfolio,
  SkillRow,
} from './types'

export type DisplayBacktest = {
  strategy: string
  engine?: string
  symbols?: string[]
  from?: string | null
  to?: string | null
  pnl?: number
  pnl_pct?: number
  sharpe?: number
  max_drawdown_pct?: number
  trades?: { total?: number; won?: number; lost?: number }
  note?: string
  source?: string
}

function asNum(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

function asStr(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() ? v : undefined
}

type MetricBlob = {
  total_return_pct?: number
  max_drawdown_pct?: number
  sharpe?: number
  end_equity?: number
  start_equity?: number
  from?: string
  to?: string
}

function fromMetrics(
  strategy: string,
  m: MetricBlob | undefined,
  extra?: Partial<DisplayBacktest>,
): DisplayBacktest | null {
  if (!m) return null
  const start = asNum(m.start_equity)
  const end = asNum(m.end_equity)
  const pnl =
    start != null && end != null ? end - start : undefined
  return {
    strategy,
    pnl,
    pnl_pct: asNum(m.total_return_pct),
    sharpe: asNum(m.sharpe),
    max_drawdown_pct: asNum(m.max_drawdown_pct),
    from: asStr(m.from) ?? null,
    to: asStr(m.to) ?? null,
    ...extra,
  }
}

/** Normalize backtest.json / winner.json / latest.json into a short display row. */
export function normalizeBacktest(raw: BacktestResult): DisplayBacktest | null {
  const file = asStr(raw._file) ?? 'backtest.json'

  if (typeof raw.pnl === 'number' || asStr(raw.strategy)) {
    return {
      strategy: asStr(raw.strategy) ?? file,
      engine: asStr(raw.engine),
      symbols: Array.isArray(raw.symbols)
        ? (raw.symbols as unknown[]).map(String)
        : undefined,
      from: (raw.from as string | null | undefined) ?? null,
      to: (raw.to as string | null | undefined) ?? null,
      pnl: asNum(raw.pnl),
      pnl_pct: asNum(raw.pnl_pct),
      sharpe: asNum(raw.sharpe),
      max_drawdown_pct: asNum(raw.max_drawdown_pct),
      trades: raw.trades,
      note: asStr(raw.note) ?? asStr(raw.verdict),
      source: file,
    }
  }

  // winner.json
  if (raw.test_metrics && typeof raw.test_metrics === 'object') {
    const family = asStr(raw.family)
    const id = asStr(raw.strategy_id)
    const strategy = [family, id].filter(Boolean).join(':') || file
    return fromMetrics(strategy, raw.test_metrics as MetricBlob, {
      symbols: Array.isArray(raw.universe)
        ? (raw.universe as unknown[]).map(String)
        : undefined,
      note: asStr(raw.verdict),
      source: file,
    })
  }

  // latest.json — take selection.winner.test
  const selection = raw.selection as
    | {
        verdict?: string
        winner?: {
          id?: string
          family?: string
          test?: MetricBlob
        }
      }
    | undefined
  if (selection?.winner?.test) {
    const w = selection.winner
    const strategy =
      [asStr(w.family), asStr(w.id)].filter(Boolean).join(':') ||
      `grid winner (${asStr(raw.run_id) ?? file})`
    const nSpecs = asNum(raw.n_specs)
    return fromMetrics(strategy, w.test, {
      engine: asStr(raw.engine),
      symbols: Array.isArray(raw.symbols)
        ? (raw.symbols as unknown[]).map(String)
        : undefined,
      note: [
        asStr(selection.verdict),
        nSpecs != null ? `${nSpecs} specs` : null,
        asStr(raw.note),
      ]
        .filter(Boolean)
        .join(' · '),
      source: file,
    })
  }

  return null
}

/** Extract ## section bodies from markdown. */
export function mdSections(md: string): Record<string, string> {
  const out: Record<string, string> = {}
  const parts = md.split(/^##\s+/m)
  for (let i = 1; i < parts.length; i++) {
    const chunk = parts[i]
    const nl = chunk.indexOf('\n')
    const title = (nl === -1 ? chunk : chunk.slice(0, nl)).trim()
    const body = nl === -1 ? '' : chunk.slice(nl + 1).trim()
    out[title] = body
  }
  return out
}

function parseMdTable(block: string): string[][] {
  const lines = block
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('|'))
  if (lines.length < 2) return []
  const rows: string[][] = []
  for (const line of lines) {
    const cells = line
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((c) => c.trim())
    if (cells.every((c) => /^:?-+:?$/.test(c))) continue
    rows.push(cells)
  }
  return rows
}

export function extractSkills(report: string | null): SkillRow[] {
  if (!report) return []
  const sections = mdSections(report)
  const key =
    Object.keys(sections).find((k) => /skill/i.test(k) || /сделал/i.test(k)) ??
    null
  if (!key) return []
  const rows = parseMdTable(sections[key])
  if (rows.length < 2) return []
  const [, ...data] = rows
  return data
    .filter((r) => r.length >= 2)
    .map(([name, result]) => ({
      name: name.replace(/\*\*/g, '').trim(),
      result: result.replace(/\*\*/g, '').trim(),
    }))
}

export function extractNewsFromReport(report: string | null): NewsItem[] {
  if (!report) return []
  const skills = extractSkills(report)
  const newsSkill = skills.find((s) => /news/i.test(s.name))
  if (!newsSkill) return []

  // Split skill blurb into readable bullets when possible.
  const text = newsSkill.result
  const chunks = text
    .split(/;\s+/)
    .map((c) => c.trim())
    .filter(Boolean)

  if (chunks.length <= 1) {
    return [
      {
        title: 'Мониторинг новостей',
        summary: text,
        source: 'trader-news',
      },
    ]
  }

  return chunks.map((chunk, i) => ({
    title: chunk.length > 90 ? `${chunk.slice(0, 87)}…` : chunk,
    summary: chunk,
    source: 'trader-news',
    at: i === 0 ? undefined : undefined,
  }))
}

export function clip(text: string | undefined | null, max = 160): string {
  if (!text) return ''
  const t = text.replace(/\s+/g, ' ').trim()
  if (t.length <= max) return t
  return `${t.slice(0, max - 1).trimEnd()}…`
}

export function extractAnalysis(
  portfolio: Portfolio | null,
  report: string | null,
  analysisJson?: AnalysisJson | null,
): {
  thesis?: string
  rationale?: string
  screener?: string
  analyst?: string
  risks: string[]
  fills?: number
  ordersPassed?: number
  signals: Array<{ ticker: string; sources: string[]; reasons: string[] }>
} {
  const skills = extractSkills(report)
  const screener = skills.find((s) => /screener/i.test(s.name))?.result
  const analyst = skills.find((s) => /analyst/i.test(s.name))?.result
  const risks: string[] = []

  if (Array.isArray(analysisJson?.risks)) {
    for (const r of analysisJson.risks) {
      if (typeof r === 'string' && r.trim()) risks.push(r.trim())
    }
  }

  if (report && risks.length === 0) {
    const sections = mdSections(report)
    const riskBody =
      sections['Риски'] ??
      sections['Risks'] ??
      Object.entries(sections).find(([k]) => /риск/i.test(k))?.[1]
    if (riskBody) {
      for (const line of riskBody.split('\n')) {
        const m = line.match(/^-\s+(.+)/)
        if (m) risks.push(m[1].trim())
      }
    }
  }

  // Short default risks from session shape when none written yet.
  if (risks.length === 0 && (portfolio || analysisJson)) {
    risks.push('Бумажная симуляция: исполнения идеальные, без проскальзывания.')
    risks.push('Котировки могут быть с задержкой — не для живой торговли.')
  }

  const rawSignals =
    analysisJson?.signals ||
    analysisJson?.thesis?.signals ||
    (portfolio?.thesis as { signals?: AnalysisJson['signals'] } | undefined)?.signals ||
    []
  const signals = (Array.isArray(rawSignals) ? rawSignals : [])
    .filter((s) => s && s.ticker)
    .map((s) => ({
      ticker: String(s.ticker),
      sources: Array.isArray(s.sources) ? s.sources.map(String) : [],
      reasons: Array.isArray(s.reasons) ? s.reasons.map(String) : [],
    }))

  return {
    thesis: analysisJson?.thesis?.name ?? portfolio?.thesis?.name,
    rationale:
      analysisJson?.thesis?.rationale_ru ?? portfolio?.thesis?.rationale_ru,
    screener,
    analyst,
    risks: risks.slice(0, 5),
    fills:
      typeof analysisJson?.fills === 'number'
        ? analysisJson.fills
        : portfolio?.fills?.length,
    ordersPassed:
      typeof analysisJson?.orders_passed === 'number'
        ? analysisJson.orders_passed
        : undefined,
    signals,
  }
}

export function formatMoney(n: number | undefined | null, digits = 2): string {
  if (n == null || Number.isNaN(n)) return '—'
  const sign = n < 0 ? '−' : ''
  return `${sign}$${Math.abs(n).toFixed(digits)}`
}

export function formatPct(n: number | undefined | null, digits = 2): string {
  if (n == null || Number.isNaN(n)) return '—'
  const sign = n > 0 ? '+' : n < 0 ? '−' : ''
  return `${sign}${Math.abs(n).toFixed(digits)}%`
}

export function sideLabel(side: string): string {
  const s = side.toLowerCase()
  if (s === 'buy') return 'покупка'
  if (s === 'sell') return 'продажа'
  return side
}

export function modeLabel(mode?: string | null): string {
  if (!mode) return ''
  const m = mode.toLowerCase()
  if (m === 'paper') return 'симуляция'
  if (m === 'live') return 'живой (запрещён)'
  return mode
}

export const DISCLAIMER_RU =
  'Только обучение и tooling. Бумажная симуляция. Не финансовый совет.'

export function eventKindLabel(kind?: string | null): string {
  if (!kind) return 'событие'
  const map: Record<string, string> = {
    session_start: 'старт сессии',
    session_end: 'конец сессии',
    portfolio_reset: 'сброс портфеля',
    skill_start: 'skill старт',
    skill_end: 'skill конец',
    fill: 'сделка',
    fill_skip: 'пропуск сделки',
    mark_to_market: 'переоценка',
    rebalance: 'ребаланс',
    auto_exit: 'авто-выход',
    reopen_search: 'поиск входа',
    reopen: 'новый вход',
    runner_start: 'воркер старт',
    runner_stop: 'воркер стоп',
    runner_error: 'ошибка воркера',
    event: 'событие',
  }
  return map[kind] ?? kind
}

export function fileList(files?: Record<string, { mtime: string; size: number }>) {
  if (!files) return []
  return Object.entries(files)
    .map(([name, meta]) => ({ name, ...meta }))
    .sort((a, b) => a.name.localeCompare(b.name))
}
