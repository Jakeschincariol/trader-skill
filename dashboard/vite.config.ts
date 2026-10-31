import react from '@vitejs/plugin-react'
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'
import { defineConfig } from 'vite'

/** Active platform state (nitub run paper → сюда). Fallback: last paper session. */
const ROOT = path.resolve(import.meta.dirname, '..')
const STATE_DIR = path.resolve(import.meta.dirname, '../nitub/state')
const FALLBACK_DIR = path.resolve(import.meta.dirname, '../paper-trading/session-1')
const RUNNER_JSON = path.join(STATE_DIR, 'runner.json')
const RUNNER_STOP = path.join(STATE_DIR, 'runner.stop')
const PYTHON = path.join(ROOT, 'nitub/.venv/bin/python')
const POLL_HINT_MS = 4000
const GOAL_RU =
  'Попытаться вырастить бумажный счёт ~$100 (симуляция, без гарантий прибыли)'

let runnerChild: ChildProcess | null = null

function resolveSessionDir(): string {
  if (fs.existsSync(path.join(STATE_DIR, 'portfolio.json'))) return STATE_DIR
  if (fs.existsSync(STATE_DIR) && fs.readdirSync(STATE_DIR).length > 0) return STATE_DIR
  return FALLBACK_DIR
}

function sendJson(res: ServerResponse, status: number, body: unknown) {
  const payload = JSON.stringify(body)
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(payload)
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (c) => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

function utcNow(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')
}

type AutoExitCfg = {
  enabled?: boolean
  take_profit_pct?: number
  stop_loss_pct?: number
  portfolio_take_profit_pct?: number
  max_drawdown_pct?: number
}

type RunnerState = {
  running: boolean
  started_at: string | null
  pid: number | null
  goal: string
  last_action: string | null
  message: string
  stopped_at?: string | null
  updated_at?: string
  interval_s?: number
  auto_exit?: AutoExitCfg
  auto_exit_enabled?: boolean
}

function defaultRunner(): RunnerState {
  return {
    running: false,
    started_at: null,
    pid: null,
    goal: GOAL_RU,
    last_action: null,
    message: 'Остановлен',
    stopped_at: null,
  }
}

function readRunner(): RunnerState {
  try {
    if (!fs.existsSync(RUNNER_JSON)) return defaultRunner()
    const raw = JSON.parse(fs.readFileSync(RUNNER_JSON, 'utf8')) as Partial<RunnerState>
    return { ...defaultRunner(), ...raw }
  } catch {
    return defaultRunner()
  }
}

function writeRunner(patch: Partial<RunnerState>): RunnerState {
  fs.mkdirSync(STATE_DIR, { recursive: true })
  const next = { ...readRunner(), ...patch, updated_at: utcNow() }
  const tmp = `${RUNNER_JSON}.tmp`
  fs.writeFileSync(tmp, `${JSON.stringify(next, null, 2)}\n`, 'utf8')
  fs.renameSync(tmp, RUNNER_JSON)
  return next
}

function pidAlive(pid: number | null | undefined): boolean {
  if (!pid || pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

function refreshRunnerStatus(): RunnerState {
  const cur = readRunner()
  const alive = pidAlive(cur.pid)
  if (cur.running && !alive) {
    return writeRunner({
      running: false,
      pid: null,
      last_action: 'stale',
      message: 'Остановлен (процесс не найден)',
      stopped_at: cur.stopped_at ?? utcNow(),
    })
  }
  if (!cur.running && alive) {
    return writeRunner({
      running: true,
      message: cur.message?.startsWith('Работает') ? cur.message : 'Работает…',
    })
  }
  return cur
}

function startPaperWorker(): RunnerState {
  const cur = refreshRunnerStatus()
  if (cur.running && pidAlive(cur.pid)) {
    return writeRunner({
      last_action: 'already_running',
      message: 'Уже работает',
    })
  }

  if (!fs.existsSync(PYTHON)) {
    return writeRunner({
      running: false,
      last_action: 'error',
      message: `Нет интерпретатора: ${path.relative(ROOT, PYTHON)}`,
    })
  }

  try {
    if (fs.existsSync(RUNNER_STOP)) fs.unlinkSync(RUNNER_STOP)
  } catch {
    /* ignore */
  }

  const child = spawn(
    PYTHON,
    ['-m', 'nitub.runner', '--interval', '45', '--budget', '100', '--session', 'dashboard'],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        PYTHONPATH: ROOT,
        NITUB_MODE: 'paper',
      },
      detached: true,
      stdio: 'ignore',
    },
  )
  child.unref()
  runnerChild = child
  const pid = child.pid ?? null

  child.on('exit', () => {
    if (runnerChild === child) runnerChild = null
    const latest = readRunner()
    if (latest.pid === pid) {
      writeRunner({
        running: false,
        pid: null,
        last_action: 'exited',
        message: 'Остановлен',
        stopped_at: utcNow(),
      })
    }
  })

  return writeRunner({
    running: true,
    started_at: utcNow(),
    pid,
    goal: GOAL_RU,
    last_action: 'start',
    message: 'Работает… бумажный воркер запущен',
    stopped_at: null,
  })
}

function stopPaperWorker(): RunnerState {
  const cur = refreshRunnerStatus()
  fs.mkdirSync(STATE_DIR, { recursive: true })
  try {
    fs.writeFileSync(RUNNER_STOP, '1\n', 'utf8')
  } catch {
    /* ignore */
  }

  const pid = cur.pid
  if (pid && pidAlive(pid)) {
    try {
      process.kill(pid, 'SIGTERM')
    } catch {
      /* ignore */
    }
    // Force after brief wait if still alive
    setTimeout(() => {
      if (pidAlive(pid)) {
        try {
          process.kill(pid, 'SIGKILL')
        } catch {
          /* ignore */
        }
      }
    }, 2500)
  }

  if (runnerChild?.pid && runnerChild.pid === pid) {
    try {
      runnerChild.kill('SIGTERM')
    } catch {
      /* ignore */
    }
    runnerChild = null
  }

  return writeRunner({
    running: false,
    pid: null,
    last_action: 'stop',
    message: 'Остановлен (позиции не закрывались)',
    stopped_at: utcNow(),
  })
}

function controlApiPlugin(): Plugin {
  return {
    name: 'nitub-control-api',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const rawUrl = req.url ?? '/'
        if (!rawUrl.startsWith('/api/control')) {
          next()
          return
        }
        try {
          const url = new URL(rawUrl, 'http://localhost')
          const pathname = url.pathname.replace(/\/+$/, '') || '/'
          const method = (req.method || 'GET').toUpperCase()

          if (pathname === '/api/control/status' && method === 'GET') {
            sendJson(res, 200, { ok: true, runner: refreshRunnerStatus() })
            return
          }

          if (pathname === '/api/control/start' && method === 'POST') {
            await readBody(req)
            const runner = startPaperWorker()
            sendJson(res, 200, { ok: true, runner })
            return
          }

          if (pathname === '/api/control/stop' && method === 'POST') {
            await readBody(req)
            const runner = stopPaperWorker()
            sendJson(res, 200, { ok: true, runner })
            return
          }

          sendJson(res, 404, { ok: false, error: 'not_found' })
        } catch (err) {
          sendJson(res, 500, {
            ok: false,
            error: 'server_error',
            message: err instanceof Error ? err.message : String(err),
          })
        }
      })
    },
  }
}

function readEventsJsonl(dir: string, limit = 100): unknown[] {
  const p = path.join(dir, 'events.jsonl')
  if (!fs.existsSync(p)) return []
  const lines = fs.readFileSync(p, 'utf8').split('\n').filter(Boolean)
  const rows: unknown[] = []
  for (const line of lines.slice(-limit)) {
    try {
      rows.push(JSON.parse(line))
    } catch {
      /* skip bad line */
    }
  }
  return rows
}

function sessionApiPlugin(): Plugin {
  return {
    name: 'nitub-session-api',
    configureServer(server) {
      server.middlewares.use('/api/session', (req: IncomingMessage, res: ServerResponse) => {
        try {
          const url = new URL(req.url ?? '/', 'http://localhost')
          const pathname = url.pathname.replace(/\/+$/, '') || '/'
          const SESSION_DIR = resolveSessionDir()

          if (!fs.existsSync(SESSION_DIR)) {
            sendJson(res, 200, {
              ok: false,
              reason: 'missing_session_dir',
              dir: SESSION_DIR,
              poll_ms: POLL_HINT_MS,
              files: [],
            })
            return
          }

          if (pathname === '/' || pathname === '/index') {
            const entries = fs.readdirSync(SESSION_DIR, { withFileTypes: true })
            const files = entries
              .filter((e) => e.isFile())
              .map((e) => {
                const full = path.join(SESSION_DIR, e.name)
                const st = fs.statSync(full)
                return {
                  name: e.name,
                  size: st.size,
                  mtime: st.mtime.toISOString(),
                }
              })
              .sort((a, b) => a.name.localeCompare(b.name))

            sendJson(res, 200, {
              ok: true,
              dir: path.relative(path.resolve(import.meta.dirname, '..'), SESSION_DIR),
              poll_ms: POLL_HINT_MS,
              files,
              now: new Date().toISOString(),
            })
            return
          }

          if (pathname === '/bundle') {
            const wanted = [
              'portfolio.json',
              'ledger.md',
              'report.md',
              'news.json',
              'analysis.json',
              'skills.json',
              'skills_catalog.json',
              'events.jsonl',
              'backtest.json',
              'meta.json',
            ]
            const bundle: Record<string, unknown> = {
              ok: true,
              poll_ms: POLL_HINT_MS,
              now: new Date().toISOString(),
              dir: path.relative(path.resolve(import.meta.dirname, '..'), SESSION_DIR),
              files: {} as Record<string, { mtime: string; size: number }>,
              portfolio: null,
              ledger: null,
              report: null,
              news: null,
              analysis: null,
              skills: null,
              skills_catalog: null,
              events: [] as unknown[],
              backtest: null,
              backtests: [] as unknown[],
              meta: null,
              extras: {} as Record<string, unknown>,
            }

            const filesMeta: Record<string, { mtime: string; size: number }> = {}
            const extras: Record<string, unknown> = {}

            for (const name of fs.readdirSync(SESSION_DIR)) {
              const full = path.join(SESSION_DIR, name)
              const st = fs.statSync(full)
              if (!st.isFile()) continue
              filesMeta[name] = { mtime: st.mtime.toISOString(), size: st.size }
              const raw = fs.readFileSync(full, 'utf8')

              if (name === 'portfolio.json') {
                try {
                  bundle.portfolio = JSON.parse(raw)
                } catch {
                  bundle.portfolio = { _parse_error: true, raw }
                }
              } else if (name === 'ledger.md') {
                bundle.ledger = raw
              } else if (name === 'report.md') {
                bundle.report = raw
              } else if (name === 'news.json') {
                try {
                  bundle.news = JSON.parse(raw)
                } catch {
                  bundle.news = { _parse_error: true, raw }
                }
              } else if (name === 'analysis.json') {
                try {
                  bundle.analysis = JSON.parse(raw)
                } catch {
                  bundle.analysis = { _parse_error: true, raw }
                }
              } else if (name === 'skills.json') {
                try {
                  bundle.skills = JSON.parse(raw)
                } catch {
                  bundle.skills = { _parse_error: true, raw }
                }
              } else if (name === 'skills_catalog.json') {
                try {
                  bundle.skills_catalog = JSON.parse(raw)
                } catch {
                  bundle.skills_catalog = { _parse_error: true, raw }
                }
              } else if (name === 'events.jsonl') {
                bundle.events = readEventsJsonl(SESSION_DIR)
              } else if (name === 'backtest.json' || name === 'meta.json') {
                try {
                  const parsed = JSON.parse(raw)
                  if (name === 'backtest.json') bundle.backtest = parsed
                  else bundle.meta = parsed
                } catch {
                  extras[name] = raw
                }
              } else if (name.endsWith('.json')) {
                try {
                  extras[name] = JSON.parse(raw)
                } catch {
                  extras[name] = raw
                }
              } else if (name.endsWith('.md') && !wanted.includes(name)) {
                extras[name] = raw
              }
            }

            if (!bundle.events || (Array.isArray(bundle.events) && bundle.events.length === 0)) {
              bundle.events = readEventsJsonl(SESSION_DIR)
            }

            const backtests: unknown[] = []
            if (bundle.backtest && typeof bundle.backtest === 'object') {
              backtests.push(bundle.backtest)
            }
            const backtestsDir = path.join(SESSION_DIR, 'backtests')
            if (fs.existsSync(backtestsDir) && fs.statSync(backtestsDir).isDirectory()) {
              for (const name of fs.readdirSync(backtestsDir).sort()) {
                if (!name.endsWith('.json')) continue
                // Skip bulky grid dumps — winner.json / latest.json carry the summary.
                if (/^grid_/i.test(name)) {
                  const fullGrid = path.join(backtestsDir, name)
                  try {
                    const st = fs.statSync(fullGrid)
                    if (st.isFile()) {
                      filesMeta[`backtests/${name}`] = {
                        mtime: st.mtime.toISOString(),
                        size: st.size,
                      }
                    }
                  } catch {
                    /* ignore */
                  }
                  continue
                }
                const full = path.join(backtestsDir, name)
                try {
                  const st = fs.statSync(full)
                  if (!st.isFile()) continue
                  filesMeta[`backtests/${name}`] = {
                    mtime: st.mtime.toISOString(),
                    size: st.size,
                  }
                  const parsed = JSON.parse(fs.readFileSync(full, 'utf8')) as Record<
                    string,
                    unknown
                  >
                  backtests.push({ ...parsed, _file: name })
                } catch {
                  /* skip bad file */
                }
              }
            }
            bundle.backtests = backtests

            bundle.files = filesMeta
            bundle.extras = extras
            sendJson(res, 200, bundle)
            return
          }

          const fileName = decodeURIComponent(pathname.slice(1))
          if (fileName.includes('..') || path.isAbsolute(fileName)) {
            sendJson(res, 400, { error: 'invalid_path' })
            return
          }
          const full = path.join(SESSION_DIR, fileName)
          if (!full.startsWith(SESSION_DIR) || !fs.existsSync(full)) {
            sendJson(res, 404, { error: 'not_found', file: fileName })
            return
          }
          if (fileName === 'events.jsonl') {
            sendJson(res, 200, { ok: true, events: readEventsJsonl(SESSION_DIR) })
            return
          }
          const raw = fs.readFileSync(full)
          const lower = fileName.toLowerCase()
          if (lower.endsWith('.json')) {
            res.statusCode = 200
            res.setHeader('Content-Type', 'application/json; charset=utf-8')
            res.setHeader('Cache-Control', 'no-store')
            res.end(raw)
            return
          }
          res.statusCode = 200
          res.setHeader('Content-Type', 'text/plain; charset=utf-8')
          res.setHeader('Cache-Control', 'no-store')
          res.end(raw)
        } catch (err) {
          sendJson(res, 500, {
            error: 'server_error',
            message: err instanceof Error ? err.message : String(err),
          })
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), sessionApiPlugin(), controlApiPlugin()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
})
