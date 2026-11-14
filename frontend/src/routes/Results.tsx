import { useEffect, useState } from 'react'
import { api, download } from '../api'
import { columnHelp } from '../columnHelp'
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
  const [exporting, setExporting] = useState(false)
  const [noErrors, setNoErrors] = useState(true)
  const skipErrors = name === 'final_results' && noErrors

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

  // second CSV row: the same column explanations the table shows; column names never contain commas or quotes
  function withHelpRow(body: string) {
    const end = body.search(/\r?\n/)
    if (end < 0) return body
    const help = body.slice(0, end).split(',').map((c) => `"${columnHelp(name, c).replace(/"/g, '""')}"`).join(',')
    const eol = body[end] === '\r' ? '\r\n' : '\n'
    return body.slice(0, end) + eol + help + body.slice(end)
  }

  async function exportCsv() {
    setExporting(true)
    setError('')
    const stamp = new Date().toISOString().slice(0, 10)
    const file = `${name}${search ? `_${search.replace(/[^\w-]+/g, '_')}` : ''}${skipErrors ? '_no_errors' : ''}_${stamp}.csv`
    try { await download(`/api/results/${name}/csv?q=${encodeURIComponent(search)}&no_errors=${skipErrors}`, file, withHelpRow) }
    catch (e) { setError((e as Error).message) }
    finally { setExporting(false) }
  }

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
        <button className="export" disabled={!name || exporting} onClick={exportCsv}
                title={search ? `Only rows matching "${search}"` : 'Every row, not just this page'}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3v12m0 0-4-4m4 4 4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
          </svg>
          {exporting ? 'Exporting…'
            : skipErrors ? 'Export CSV · without error rows'
            : `Export CSV${page ? ` · ${page.total.toLocaleString()} ${search ? 'matching ' : ''}rows` : ''}`}
        </button>
        {name === 'final_results' && (
          <label className="no-errors">
            <input type="checkbox" checked={noErrors} onChange={(e) => setNoErrors(e.target.checked)} />
            Leave out rows with errors
          </label>
        )}
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
              <thead>
                <tr>{page.columns.map((c) => <th key={c}>{c}</th>)}</tr>
                <tr className="help">{page.columns.map((c) => <th key={c}>{columnHelp(name, c)}</th>)}</tr>
              </thead>
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
