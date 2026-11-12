import { useEffect, useRef, useState } from 'react'
import { api, post } from '../api'
import './LogView.scss'

export type Job = {
  id: string; command: string; argv: string[]; status: string; exit_code: number | null
  started_by: string | null; started_at: string; finished_at: string | null
}

type LogPage = { text: string; offset: number; status: string }

// Shows a job's whole log and keeps polling for more while it runs.
export default function LogView({ jobId, onFinish }: { jobId: string; onFinish?: (status: string) => void }) {
  const [text, setText] = useState('')
  const [status, setStatus] = useState('running')
  const [error, setError] = useState('')
  const pre = useRef<HTMLPreElement>(null)
  const stickToBottom = useRef(true)

  useEffect(() => {
    let offset = 0
    let timer: number | undefined
    let cancelled = false
    setText('')
    setStatus('running')
    setError('')

    async function poll() {
      try {
        const page = await api<LogPage>(`/api/jobs/${jobId}/log?offset=${offset}`)
        if (cancelled) return
        offset = page.offset
        if (page.text) setText((t) => t + page.text)
        setStatus(page.status)
        if (page.status === 'running') timer = window.setTimeout(poll, 1000)
        else onFinish?.(page.status)
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    }
    poll()
    return () => { cancelled = true; window.clearTimeout(timer) }
    // onFinish is a plain callback; re-subscribing on every render would restart the log
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  useEffect(() => {
    const el = pre.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [text])

  function onScroll() {
    const el = pre.current!
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }

  async function stop() {
    try { await post(`/api/jobs/${jobId}/stop`) } catch (e) { setError((e as Error).message) }
  }

  return (
    <div className="logview">
      <div className="logview-bar">
        <span className={`badge ${status}`}>{status}</span>
        <span className="muted mono">{jobId.slice(0, 8)}</span>
        {status === 'running' && <button className="danger small" onClick={stop}>Stop</button>}
      </div>
      {error && <div className="error">{error}</div>}
      <pre ref={pre} onScroll={onScroll}>{text || (status === 'running' ? 'Starting…' : '(no output)')}</pre>
    </div>
  )
}
