import { useEffect, useState } from 'react'
import { api, download } from '../api'
import './Results.scss'

type Page = { columns: string[]; rows: unknown[][]; total: number }
const LIMIT = 50

function cell(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (Array.isArray(v) && v.every((x) => typeof x === 'string')) return v.join(' | ')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export default function Results() {
  const [names, setNames] = useState<string[]>([])
  const [name, setName] = useState('')
  const [q, setQ] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<Page | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api<string[]>('/api/results').then((n) => { setNames(n); if (n.length) setName(n[0]) }).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!name) return
    setLoading(true)
    setError('')
    api<Page>(`/api/results/${name}?q=${encodeURIComponent(search)}&offset=${offset}&limit=${LIMIT}`)
      .then(setPage).catch((e) => setError(e.message)).finally(() => setLoading(false))
  }, [name, search, offset])

  function choose(n: string) { setName(n); setOffset(0); setQ(''); setSearch('') }

  const last = page ? Math.min(offset + LIMIT, page.total) : 0
  return (
    <div className="results">
      <h1>Results</h1>
      <div className="card toolbar">
        <div className="field">
          <label>Table or view</label>
          <select value={name} onChange={(e) => choose(e.target.value)}>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <form className="field" onSubmit={(e) => { e.preventDefault(); setOffset(0); setSearch(q) }}>
          <label>Search item name</label>
          <div className="row">
            <input value={q} placeholder="e.g. titos" onChange={(e) => setQ(e.target.value)} />
            <button type="submit" className="secondary">Search</button>
          </div>
        </form>
        <button className="secondary" disabled={!name} onClick={() => download(`/api/results/${name}/csv`, `${name}.csv`)}>Download CSV</button>
      </div>

      {error && <div className="error">{error}</div>}
      {page && (
        <div className="card table-card">
          <div className="pager">
            <span className="muted">{page.total ? `${offset + 1}–${last} of ${page.total.toLocaleString()}` : 'No rows'}{loading && ' · loading…'}</span>
            <button className="secondary small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>Previous</button>
            <button className="secondary small" disabled={last >= page.total} onClick={() => setOffset(offset + LIMIT)}>Next</button>
          </div>
          <div className="scroll">
            <table className="data">
              <thead><tr>{page.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {page.rows.map((r, i) => (
                  <tr key={i}>{r.map((v, j) => { const s = cell(v); return <td key={j} title={s}>{s}</td> })}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
