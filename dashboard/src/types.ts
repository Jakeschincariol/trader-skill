export type Position = {
  ticker: string
  shares: number
  cost_basis: number
  avg_cost_per_share: number
  mark: number
  market_value: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
}

export type Fill = {
  id: string
  symbol: string
  side: string
  notional: number
  qty: number
  price: number
  status: string
}

export type Portfolio = {
  session?: string
  mode?: string
  currency?: string
  started_at?: string
  as_of?: string
  price_source?: string
  price_note?: string
  thesis?: {
    name?: string
    source?: string
    as_of_holdings?: string
    filed?: string
    rationale_ru?: string
  }
  starting_cash?: number
  cash?: number
  invested_cost?: number
  positions_market_value?: number
  equity?: number
  unrealized_pnl?: number
  total_pnl_vs_start?: number
  total_pnl_pct?: number
  realized_pnl?: number
  cash_buffer_target?: number
  positions?: Position[]
  fills?: Fill[]
  config_snapshot?: Record<string, unknown>
  disclaimer?: string
}

export type NewsItem = {
  title: string
  source?: string
  tickers?: string[]
  summary?: string
  at?: string
}

export type SkillRow = {
  name: string
  result: string
  status?: string
  group?: string
}

export type SkillsCatalog = {
  core?: string[]
  vendor?: string[]
  all?: string[]
  completed?: string[]
  last_run?: SkillRow[]
  heavy?: string[]
  heavy_every?: number
}

export type PlatformEvent = {
  ts?: string
  kind?: string
  summary?: string
  skill?: string
  data?: Record<string, unknown>
}

export type BacktestResult = {
  ok?: boolean
  engine?: string
  strategy?: string
  symbols?: string[]
  from?: string | null
  to?: string | null
  start_cash?: number
  end_value?: number
  pnl?: number
  pnl_pct?: number
  sharpe?: number
  max_drawdown_pct?: number
  trades?: { total?: number; won?: number; lost?: number }
  note?: string
  _file?: string
  [key: string]: unknown
}

export type AnalysisJson = {
  thesis?: {
    name?: string
    source?: string
    rationale_ru?: string
    as_of_holdings?: string
    filed?: string
  }
  orders_proposed?: number
  orders_passed?: number
  fills?: number
  equity?: number
  pnl?: number
  risks?: string[]
  [key: string]: unknown
}

export type SessionBundle = {
  ok: boolean
  poll_ms?: number
  now?: string
  dir?: string
  files?: Record<string, { mtime: string; size: number }>
  portfolio: Portfolio | null
  ledger: string | null
  report: string | null
  news: unknown
  analysis: AnalysisJson | unknown
  skills: unknown
  skills_catalog?: SkillsCatalog | null
  events?: PlatformEvent[]
  backtest?: BacktestResult | null
  backtests?: BacktestResult[]
  meta?: Record<string, unknown> | null
  extras?: Record<string, unknown>
}

export type AutoExitConfig = {
  enabled?: boolean
  take_profit_pct?: number
  stop_loss_pct?: number
  portfolio_take_profit_pct?: number
  max_drawdown_pct?: number
  min_position_pnl_usd?: number
  exit_rebuy_cooldown_cycles?: number
}

export type RunnerState = {
  running: boolean
  started_at?: string | null
  pid?: number | null
  goal?: string
  last_action?: string | null
  message?: string
  stopped_at?: string | null
  updated_at?: string
  interval_s?: number
  auto_exit?: AutoExitConfig
  auto_exit_enabled?: boolean
}
