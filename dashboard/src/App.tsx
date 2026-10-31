import { useEffect, useState } from 'react'
import './App.css'
import {
  clip,
  DISCLAIMER_RU,
  eventKindLabel,
  extractAnalysis,
  extractSkills,
  fileList,
  formatMoney,
  formatPct,
  modeLabel,
  normalizeBacktest,
  sideLabel,
  type DisplayBacktest,
} from './parseSession'
import type { AnalysisJson, BacktestResult, SkillRow, SkillsCatalog } from './types'
import { useSession } from './useSession'

const CORE_FALLBACK = [
  'alerts',
  'copycat',
  'news',
  'pelosi',
  'analyst',
  'screener',
  'budget',
  'risk',
  'broker',
  'tax',
  'rebalance',
]

function skillStatusClass(status?: string): string {
  if (status === 'ok') return 'skill-ok'
  if (status === 'error') return 'skill-err'
  if (status === 'soft' || status === 'skipped') return 'skill-soft'
  return 'skill-pending'
}

function buildSkillBoard(
  skills: SkillRow[],
  catalog: SkillsCatalog | null | undefined,
): { name: string; result: string; status: string; group: string; done: boolean }[] {
  const byName = new Map(skills.map((s) => [s.name, s]))
  const core = catalog?.core?.length ? catalog.core : CORE_FALLBACK
  const vendor = catalog?.vendor ?? []
  const allNames = [
    ...core,
    ...vendor,
    ...skills.map((s) => s.name).filter((n) => !core.includes(n) && !vendor.includes(n)),
  ]
  const seen = new Set<string>()
  const out: { name: string; result: string; status: string; group: string; done: boolean }[] = []
  for (const name of allNames) {
    if (seen.has(name)) continue
    seen.add(name)
    const row = byName.get(name)
    const group = row?.group || (vendor.includes(name) ? 'vendor' : name === 'research' ? 'meta' : 'core')
    out.push({
      name,
      result: row?.result ?? 'ещё не отработал в этом run',
      status: row?.status ?? (row ? 'ok' : 'pending'),
      group,
      done: !!row,
    })
  }
  return out
}

type TabId = 'overview' | 'trades' | 'portfolio' | 'events' | 'analysis' | 'backtests'

const TABS: { id: TabId; label: string }[] = [
  { id: 'overview', label: 'Обзор' },
  { id: 'trades', label: 'Сделки' },
  { id: 'portfolio', label: 'Портфель' },
  { id: 'events', label: 'События' },
  { id: 'analysis', label: 'Анализ' },
  { id: 'backtests', label: 'Бэктесты' },
]

const TAB_STORAGE_KEY = 'nitub.dashboard.tab'

function pnlClass(n: number | undefined | null): string {
  if (n == null || Math.abs(n) < 1e-9) return ''
  return n > 0 ? 'pnl-pos' : 'pnl-neg'
}

function asAnalysisJson(raw: unknown): AnalysisJson | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  return raw as AnalysisJson
}

function collectBacktests(
  single: BacktestResult | null | undefined,
  list: BacktestResult[] | undefined,
): DisplayBacktest[] {
  const raw: BacktestResult[] = []
  if (single && typeof single === 'object') raw.push(single)
  if (Array.isArray(list)) {
    for (const b of list) if (b && typeof b === 'object') raw.push(b)
  }
  const out: DisplayBacktest[] = []
  const seen = new Set<string>()
  for (const b of raw) {
    const n = normalizeBacktest(b)
    if (!n) continue
    const key = `${n.strategy}|${n.pnl_pct ?? n.pnl ?? ''}|${n.sharpe ?? ''}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(n)
  }
  return out
}

function shortTime(ts?: string): string {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleTimeString('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return ts
  }
}

export default function App() {
  const {
    data,
    runner,
    status,
    error,
    updatedAt,
    flashKey,
    hasAny,
    controlBusy,
    startRunner,
    stopRunner,
  } = useSession(4000)
  const [tab, setTab] = useState<TabId>(() => {
    try {
      const saved = localStorage.getItem(TAB_STORAGE_KEY) as TabId | null
      if (saved && TABS.some((t) => t.id === saved)) return saved
    } catch {
      /* ignore */
    }
    return 'overview'
  })

  useEffect(() => {
    try {
      localStorage.setItem(TAB_STORAGE_KEY, tab)
    } catch {
      /* ignore */
    }
  }, [tab])

  const portfolio = data.portfolio
  const skillsFromJson = Array.isArray(data.skills)
    ? (data.skills as { name?: string; result?: string; status?: string; group?: string }[])
        .filter((s) => s.name && s.result)
        .map((s) => ({
          name: String(s.name),
          result: String(s.result),
          status: s.status ? String(s.status) : undefined,
          group: s.group ? String(s.group) : undefined,
        }))
    : []
  const skills = skillsFromJson.length > 0 ? skillsFromJson : extractSkills(data.report)
  const catalog =
    (data.skills_catalog as SkillsCatalog | null | undefined) ||
    ((data.extras?.['skills_catalog.json'] as SkillsCatalog | undefined) ?? null)
  const skillBoard = buildSkillBoard(skills, catalog)
  const skillsDone = skillBoard.filter((s) => s.done).length
  const analysis = extractAnalysis(
    portfolio,
    data.report,
    asAnalysisJson(data.analysis),
  )
  const files = fileList(data.files)
  const events = Array.isArray(data.events) ? [...data.events].reverse().slice(0, 60) : []
  const backtests = collectBacktests(data.backtest ?? null, data.backtests)
  const sourceDir = data.dir ?? 'nitub/state'
  const pnl = portfolio?.total_pnl_vs_start ?? portfolio?.unrealized_pnl ?? null
  const pnlPct = portfolio?.total_pnl_pct
  const fills = portfolio?.fills ?? []
  const positions = portfolio?.positions ?? []
  const running = !!runner.running
  const runnerLabel = running
    ? runner.message?.startsWith('Работает')
      ? runner.message
      : 'Работает…'
    : runner.message || 'Остановлен'

  return (
    <div className="page">
      <header className="top">
        <div className="brand-block">
          <h1 className="brand">NITUB</h1>
          <p className="tagline">Бумажная сессия · только симуляция</p>
        </div>

        <div className="status-row">
          <span>
            <span
              className={`dot ${
                status === 'live' ? 'live' : status === 'error' ? 'error' : 'loading'
              }`}
            />
            {status === 'live'
              ? 'онлайн · ~4 с'
              : status === 'error'
                ? `ошибка: ${error}`
                : 'ожидание…'}
          </span>
          {updatedAt ? (
            <span>{new Date(updatedAt).toLocaleTimeString('ru-RU')}</span>
          ) : null}
          {portfolio?.session ? <span>{portfolio.session}</span> : null}
        </div>
      </header>

      <nav className="tabs" role="tablist" aria-label="Разделы дашборда">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`tab ${tab === t.id ? 'is-active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.id === 'backtests' && backtests.length > 0 ? (
              <span className="tab-count">{backtests.length}</span>
            ) : null}
            {t.id === 'trades' && fills.length > 0 ? (
              <span className="tab-count">{fills.length}</span>
            ) : null}
            {t.id === 'events' && events.length > 0 ? (
              <span className="tab-count">{Math.min(events.length, 99)}</span>
            ) : null}
          </button>
        ))}
      </nav>

      <main className="panel-wrap" key={tab}>
        {tab === 'overview' ? (
          <section className="tab-panel" aria-label="Обзор">
            <div className="control-bar">
              <div className="control-status">
                <span className={`run-dot ${running ? 'on' : 'off'}`} />
                <span className="control-label">{runnerLabel}</span>
                {runner.auto_exit_enabled !== false &&
                (runner.auto_exit?.enabled !== false || runner.auto_exit == null) ? (
                  <span className="control-goal muted" title="Пока Старт работает; Стоп только гасит воркер">
                    авто-выход · TP +
                    {runner.auto_exit?.take_profit_pct ?? 4}% / SL{' '}
                    {runner.auto_exit?.stop_loss_pct ?? -2.5}%
                  </span>
                ) : (
                  <span className="control-goal muted">авто-выход выкл.</span>
                )}
              </div>
              <div className="control-actions">
                <button
                  type="button"
                  className="btn btn-start"
                  disabled={controlBusy || running}
                  onClick={() => void startRunner()}
                >
                  Старт
                </button>
                <button
                  type="button"
                  className="btn btn-stop"
                  disabled={controlBusy || !running}
                  onClick={() => void stopRunner()}
                >
                  Стоп
                </button>
              </div>
            </div>

            {!hasAny ? (
              <div className="empty">
                Пока пусто в <code>nitub/state/</code>. Нажмите <strong>Старт</strong> или
                запустите <code>nitub run paper</code>.
              </div>
            ) : (
              <>
                <div className="hero-metrics">
                  <div className="metric metric-lead">
                    <span className="metric-k">Прибыль / убыток</span>
                    <span
                      key={flashKey}
                      className={`metric-v ${pnlClass(pnl)} ${flashKey > 0 ? 'flash' : ''}`}
                    >
                      {formatMoney(pnl)}
                      {pnlPct != null ? (
                        <span className="metric-sub">{formatPct(pnlPct)}</span>
                      ) : null}
                    </span>
                  </div>
                  <div className="metric">
                    <span className="metric-k">Капитал</span>
                    <span className="metric-v">{formatMoney(portfolio?.equity)}</span>
                  </div>
                  <div className="metric">
                    <span className="metric-k">Наличные</span>
                    <span className="metric-v">{formatMoney(portfolio?.cash)}</span>
                  </div>
                  <div className="metric">
                    <span className="metric-k">Позиции</span>
                    <span className="metric-v">{positions.length}</span>
                  </div>
                  <div className="metric">
                    <span className="metric-k">Сделки</span>
                    <span className="metric-v">{fills.length}</span>
                  </div>
                </div>
                <p className="one-liner">
                  {clip(analysis.thesis, 90) || 'Тезис ещё не задан'}
                  {portfolio?.mode ? ` · ${modeLabel(portfolio.mode)}` : ''}
                </p>

                <div className="skills-board">
                  <div className="skills-board-head">
                    <h2 className="brief-label">Skills pipeline</h2>
                    <span className="muted">
                      {skillsDone}/{skillBoard.length} в текущем run
                    </span>
                  </div>
                  <ul className="skills-grid">
                    {skillBoard.map((s) => (
                      <li key={s.name} className={`skill-chip ${skillStatusClass(s.status)}`}>
                        <span className="skill-chip-name">{s.name}</span>
                        <span className="skill-chip-meta">
                          {s.done ? (s.status === 'ok' ? 'ok' : s.status || 'done') : 'ожидает'}
                          {s.group === 'vendor' ? ' · vendor' : ''}
                        </span>
                        <span className="skill-chip-result">{clip(s.result, 72)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            )}
          </section>
        ) : null}

        {tab === 'trades' && hasAny ? (
          <section className="tab-panel" aria-label="Сделки">
            {!fills.length ? (
              <div className="empty">Сделок пока нет.</div>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Сторона</th>
                      <th>Тикер</th>
                      <th className="num">Кол-во</th>
                      <th className="num">Цена</th>
                      <th className="num">Сумма</th>
                      <th>ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fills.map((f) => (
                      <tr key={f.id}>
                        <td>
                          <span
                            className={
                              f.side.toLowerCase() === 'buy' ? 'side-buy' : 'side-sell'
                            }
                          >
                            {sideLabel(f.side)}
                          </span>
                        </td>
                        <td>{f.symbol}</td>
                        <td className="num">{f.qty}</td>
                        <td className="num">{formatMoney(f.price)}</td>
                        <td className="num">{formatMoney(f.notional)}</td>
                        <td className="muted">{f.id}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        ) : null}

        {tab === 'trades' && !hasAny ? (
          <div className="empty">
            Пока пусто в <code>nitub/state/</code>. Нажмите <strong>Старт</strong> на вкладке
            Обзор.
          </div>
        ) : null}

        {tab === 'portfolio' && hasAny ? (
          <section className="tab-panel" aria-label="Портфель">
            {!portfolio ? (
              <div className="empty">
                Ждём <code>portfolio.json</code>.
              </div>
            ) : (
              <>
                <div className="mini-row">
                  <span>
                    Нереализ.{' '}
                    <strong className={pnlClass(portfolio.unrealized_pnl)}>
                      {formatMoney(portfolio.unrealized_pnl)}
                    </strong>
                  </span>
                  <span>
                    Реализ.{' '}
                    <strong className={pnlClass(portfolio.realized_pnl)}>
                      {formatMoney(portfolio.realized_pnl)}
                    </strong>
                  </span>
                  {portfolio.as_of ? (
                    <span className="muted">
                      оценка {new Date(portfolio.as_of).toLocaleString('ru-RU')}
                    </span>
                  ) : null}
                </div>
                {!positions.length ? (
                  <div className="empty">Позиций нет.</div>
                ) : (
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          <th>Тикер</th>
                          <th className="num">Акции</th>
                          <th className="num">Себест.</th>
                          <th className="num">Курс</th>
                          <th className="num">Рын. ст.</th>
                          <th className="num">Нереализ.</th>
                        </tr>
                      </thead>
                      <tbody>
                        {positions.map((p) => (
                          <tr key={p.ticker}>
                            <td>{p.ticker}</td>
                            <td className="num">{p.shares}</td>
                            <td className="num">{formatMoney(p.cost_basis)}</td>
                            <td className="num">{formatMoney(p.mark)}</td>
                            <td className="num">{formatMoney(p.market_value)}</td>
                            <td className={`num ${pnlClass(p.unrealized_pnl)}`}>
                              {formatMoney(p.unrealized_pnl)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </section>
        ) : null}

        {tab === 'portfolio' && !hasAny ? (
          <div className="empty">
            Пока пусто в <code>nitub/state/</code>. Нажмите <strong>Старт</strong> на вкладке
            Обзор.
          </div>
        ) : null}

        {tab === 'events' && hasAny ? (
          <section className="tab-panel" aria-label="События">
            {!events.length ? (
              <div className="empty">Лента пуста.</div>
            ) : (
              <ul className="feed">
                {events.map((e, i) => (
                  <li key={`${e.ts}-${e.kind}-${i}`} className="feed-row">
                    <time className="feed-time">{shortTime(e.ts)}</time>
                    <span className="feed-kind">{eventKindLabel(e.kind)}</span>
                    <span className="feed-text">
                      {clip(e.summary ?? e.skill ?? '', 110)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ) : null}

        {tab === 'events' && !hasAny ? (
          <div className="empty">Лента пуста. Запустите симуляцию кнопкой Старт.</div>
        ) : null}

        {tab === 'analysis' && hasAny ? (
          <section className="tab-panel" aria-label="Анализ">
            <div className="brief">
              <div className="brief-block">
                <h2 className="brief-label">Тезис</h2>
                <p className="brief-title">{analysis.thesis ?? '—'}</p>
                {analysis.rationale ? (
                  <p className="brief-body">{clip(analysis.rationale, 180)}</p>
                ) : null}
              </div>
              <div className="brief-block">
                <h2 className="brief-label">Риски</h2>
                <ul className="risk-list">
                  {analysis.risks.map((r) => (
                    <li key={r}>{clip(r, 120)}</li>
                  ))}
                </ul>
              </div>
              <div className="brief-block">
                <h2 className="brief-label">
                  Навыки ({skillsDone}/{skillBoard.length})
                </h2>
                <ul className="skill-lines">
                  {analysis.analyst ? (
                    <li>
                      <strong>аналитик</strong> — {clip(analysis.analyst, 100)}
                    </li>
                  ) : null}
                  {analysis.screener ? (
                    <li>
                      <strong>скринер</strong> — {clip(analysis.screener, 100)}
                    </li>
                  ) : null}
                  {skillBoard.map((s) => (
                    <li key={`a-${s.name}`} className={skillStatusClass(s.status)}>
                      <strong>{s.name}</strong>
                      <span className="muted">
                        {' '}
                        · {s.done ? s.status || 'ok' : 'ожидает'}
                        {s.group === 'vendor' ? ' · vendor' : ''}
                      </span>
                      {' — '}
                      {clip(s.result, 100)}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </section>
        ) : null}

        {tab === 'analysis' && !hasAny ? (
          <div className="empty">Анализа пока нет. Запустите симуляцию.</div>
        ) : null}

        {tab === 'backtests' ? (
          <section className="tab-panel" aria-label="Бэктесты">
            {!backtests.length ? (
              <div className="empty">
                Нет данных. Положите <code>backtest.json</code> или файлы в{' '}
                <code>nitub/state/backtests/</code>.
              </div>
            ) : (
              <div className="bt-list">
                {backtests.map((b, i) => (
                  <article
                    className="bt-item"
                    key={`${b.source}-${b.strategy}-${i}`}
                  >
                    <h2 className="bt-title">
                      {b.strategy}
                      {b.engine ? (
                        <span className="muted"> · {b.engine}</span>
                      ) : null}
                    </h2>
                    <div className="hero-metrics compact">
                      <div className="metric">
                        <span className="metric-k">Прибыль / убыток</span>
                        <span className={`metric-v ${pnlClass(b.pnl)}`}>
                          {formatMoney(b.pnl)}
                          {b.pnl_pct != null ? (
                            <span className="metric-sub">{formatPct(b.pnl_pct)}</span>
                          ) : null}
                        </span>
                      </div>
                      <div className="metric">
                        <span className="metric-k">Шарп</span>
                        <span className="metric-v">
                          {b.sharpe != null ? b.sharpe.toFixed(2) : '—'}
                        </span>
                      </div>
                      <div className="metric">
                        <span className="metric-k">Макс. просадка</span>
                        <span className="metric-v">
                          {b.max_drawdown_pct != null
                            ? `${b.max_drawdown_pct.toFixed(1)}%`
                            : '—'}
                        </span>
                      </div>
                      <div className="metric">
                        <span className="metric-k">Сделки</span>
                        <span className="metric-v">
                          {b.trades?.total ?? '—'}
                          {b.trades?.won != null || b.trades?.lost != null ? (
                            <span className="metric-sub">
                              {b.trades.won ?? 0}/{b.trades.lost ?? 0}
                            </span>
                          ) : null}
                        </span>
                      </div>
                    </div>
                    <p className="one-liner">
                      {b.symbols?.join(', ') ?? '—'}
                      {b.from ? ` · ${b.from}` : ''}
                      {b.to ? ` → ${b.to}` : ''}
                      {b.note ? ` · ${clip(b.note, 80)}` : ''}
                    </p>
                  </article>
                ))}
              </div>
            )}
          </section>
        ) : null}
      </main>

      <footer className="footer">
        <span>
          Источник: <code>{sourceDir}</code>
          {files.length ? ` · ${files.length} файл(ов)` : ''}
        </span>
        <span className="muted">{DISCLAIMER_RU}</span>
      </footer>
    </div>
  )
}
