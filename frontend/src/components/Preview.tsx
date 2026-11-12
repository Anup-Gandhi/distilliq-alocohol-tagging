import { useEffect, useState } from 'react'
import { post } from '../api'
import './Preview.scss'

type PreviewRow = { source_row_id: string; base_value: string; options: string[]; model: string; message: string }
type PreviewData = { prompt: string; system_prompt: string; total: number; rows: PreviewRow[] }

// What the chosen pass would send with the form's current values. The server runs the pass's own row selection
// with --preview, so this matches a real run; no model is called.
// `blocked` says what the form still needs; no request is sent until it's empty.
export default function Preview({ command, values, blocked }: { command: string; values: Record<string, unknown>; blocked?: string }) {
  const [data, setData] = useState<PreviewData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const key = JSON.stringify([command, values, blocked])

  useEffect(() => {
    if (blocked) return
    let stale = false
    setError('')
    setLoading(true)
    // wait for typing to settle: each preview starts a Python process and reads the tables (a few seconds)
    const timer = window.setTimeout(async () => {
      try {
        const d = await post<PreviewData>('/api/preview', { command, args: values })
        if (!stale) setData(d)
      } catch (e) {
        if (!stale) { setData(null); setError((e as Error).message) }
      } finally {
        if (!stale) setLoading(false)
      }
    }, 700)
    return () => { stale = true; window.clearTimeout(timer) }
    // key stands for command and values
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const models = data ? [...new Set(data.rows.map((r) => r.model))] : []
  if (blocked) return <div className="preview"><p className="muted">{blocked}</p></div>

  return (
    <div className="preview">
      {loading && <p className="muted">Working out which rows would run…</p>}
      {!loading && error && <div className="error">{error}</div>}
      {!loading && data && (
        <>
          <p className="summary">
            <strong>{data.total.toLocaleString()}</strong> {data.total === 1 ? 'row' : 'rows'} would be sent
            {models.length === 1 && <> to <span className="mono">{models[0]}</span></>} with{' '}
            <span className="mono">{data.prompt}</span>.
            {data.total > data.rows.length && <span className="muted"> Showing the first {data.rows.length}.</span>}
          </p>
          <details className="system">
            <summary>System prompt <span className="muted">· {data.prompt} · {data.system_prompt.length.toLocaleString()} characters</span></summary>
            <pre>{data.system_prompt}</pre>
          </details>
          <p className="muted note">Each row is sent as the system prompt above plus that row's message.</p>
          {data.total === 0 && <p className="muted">Nothing to send: every row this pass covers is already done.</p>}
          <ol className="rows">
            {data.rows.map((r) => (
              <li key={r.source_row_id}>
                <div className="head">
                  <strong>{r.base_value}</strong>
                  <span className="muted mono">{r.source_row_id}</span>
                  {models.length > 1 && <span className="badge">{r.model}</span>}
                </div>
                <div className="options">
                  {r.options.map((o, i) => <span className="chip" key={i}>{o}</span>)}
                </div>
                <details>
                  <summary>Message sent</summary>
                  <pre>{r.message}</pre>
                </details>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  )
}
