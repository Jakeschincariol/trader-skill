import { useEffect, useRef, useState } from 'react'
import type { SessionBundle } from './types'

const EMPTY: SessionBundle = {
  ok: false,
  portfolio: null,
  ledger: null,
  report: null,
  news: null,
  analysis: null,
  skills: null,
  events: [],
  backtest: null,
  backtests: [],
  meta: null,
}

export function useSession(pollMs = 4000) {
  const [data, setData] = useState<SessionBundle>(EMPTY)
  const [status, setStatus] = useState<'loading' | 'live' | 'error'>('loading')
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [flashKey, setFlashKey] = useState(0)
  const prevPnl = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    async function load() {
      try {
        const res = await fetch('/api/session/bundle', { cache: 'no-store' })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const json = (await res.json()) as SessionBundle
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
  }, [pollMs])

  const hasAny =
    !!data.portfolio ||
    !!data.ledger ||
    !!data.report ||
    (data.files && Object.keys(data.files).length > 0)

  return { data, status, error, updatedAt, flashKey, hasAny }
}
