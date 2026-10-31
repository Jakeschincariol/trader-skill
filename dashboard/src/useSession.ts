import { useCallback, useEffect, useRef, useState } from 'react'
import type { RunnerState, SessionBundle } from './types'

const EMPTY: SessionBundle = {
  ok: false,
  portfolio: null,
  ledger: null,
  report: null,
  news: null,
  analysis: null,
  skills: null,
  skills_catalog: null,
  events: [],
  backtest: null,
  backtests: [],
  meta: null,
}

const EMPTY_RUNNER: RunnerState = {
  running: false,
  message: 'Остановлен',
}

export function useSession(pollMs = 4000) {
  const [data, setData] = useState<SessionBundle>(EMPTY)
  const [runner, setRunner] = useState<RunnerState>(EMPTY_RUNNER)
  const [status, setStatus] = useState<'loading' | 'live' | 'error'>('loading')
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [flashKey, setFlashKey] = useState(0)
  const [controlBusy, setControlBusy] = useState(false)
  const prevPnl = useRef<number | null>(null)

  const loadRunner = useCallback(async () => {
    try {
      const res = await fetch('/api/control/status', { cache: 'no-store' })
      if (!res.ok) return
      const json = (await res.json()) as { runner?: RunnerState }
      if (json.runner) setRunner(json.runner)
    } catch {
      /* ignore — session poll still primary */
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    async function load() {
      try {
        const [bundleRes] = await Promise.all([
          fetch('/api/session/bundle', { cache: 'no-store' }),
          loadRunner(),
        ])
        if (!bundleRes.ok) throw new Error(`HTTP ${bundleRes.status}`)
        const json = (await bundleRes.json()) as SessionBundle
        if (cancelled) return

        const pnl = json.portfolio?.total_pnl_vs_start ?? null
        if (prevPnl.current !== null && pnl !== null && pnl !== prevPnl.current) {
          setFlashKey((k) => k + 1)
        }
        prevPnl.current = pnl

        setData(json)
        setStatus('live')
        setError(null)
        setUpdatedAt(json.now ?? new Date().toISOString())
        timer = window.setTimeout(load, json.poll_ms ?? pollMs)
      } catch (err) {
        if (cancelled) return
        setStatus((s) => (s === 'loading' ? 'loading' : 'error'))
        setError(err instanceof Error ? err.message : String(err))
        timer = window.setTimeout(load, pollMs)
      }
    }

    void load()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [pollMs, loadRunner])

  const startRunner = useCallback(async () => {
    setControlBusy(true)
    try {
      const res = await fetch('/api/control/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      const json = (await res.json()) as { runner?: RunnerState; message?: string }
      if (json.runner) setRunner(json.runner)
    } finally {
      setControlBusy(false)
    }
  }, [])

  const stopRunner = useCallback(async () => {
    setControlBusy(true)
    try {
      const res = await fetch('/api/control/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      const json = (await res.json()) as { runner?: RunnerState }
      if (json.runner) setRunner(json.runner)
    } finally {
      setControlBusy(false)
    }
  }, [])

  const hasAny =
    !!data.portfolio ||
    !!data.ledger ||
    !!data.report ||
    (data.files && Object.keys(data.files).length > 0)

  return {
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
  }
}
