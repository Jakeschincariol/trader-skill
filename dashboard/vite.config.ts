import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'
import { defineConfig } from 'vite'

/** Active platform state (nitub run paper → сюда). Fallback: last paper session. */
const STATE_DIR = path.resolve(import.meta.dirname, '../nitub/state')
const FALLBACK_DIR = path.resolve(import.meta.dirname, '../paper-trading/session-1')
const POLL_HINT_MS = 4000

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
  plugins: [react(), sessionApiPlugin()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
})
