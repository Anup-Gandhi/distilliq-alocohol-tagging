import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, fmtDate, post } from '../api'
import LogView, { type Job } from '../components/LogView'
import './Dashboard.scss'

type Stats = {
  source: { rows: number; oldest_insert_dttm: string | null; newest_insert_dttm: string | null; loaded_at: string | null }
  counts: Record<string, number>
  passes: { total: number; tagged: number; skipped: number; errors: number }[]
  final: { total: number; named: number; misc: number; waiting: number; no_tag: number }
  pending: { pass_1: number; pass_2: number; pass_3: number }
  apis: { api_name: string | null; total: number; pass_1: number; pass_2: number; pass_3: number; errors: number }[]
  decisions: { decision: string; n: number }[]
  athena: { database: string; table: string; region: string; workgroup: string | null }
}

const passNames = ['Pass 1 (tag)', 'Pass 2 (review)', 'Pass 3 (final)']
const n = (v: number | undefined) => (v ?? 0).toLocaleString()
const pct = (part: number, whole: number) => (whole ? `${((100 * part) / whole).toFixed(1)}%` : '–')

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState('')
  const [job, setJob] = useState<Job | null>(null)

  const load = useCallback(() => {
    api<Stats>('/api/stats').then(setStats).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    load()
    // show a refresh that is already running, e.g. after a page reload
    api<Job[]>('/api/jobs').then((jobs) => {
      const running = jobs.find((j) => j.status === 'running' && j.command === 'refresh')
      if (running) setJob(running)
    }).catch(() => {})
  }, [load])

  async function refresh() {
    setError('')
    try { setJob(await post<Job>('/api/jobs', { command: 'refresh' })) } catch (e) { setError((e as Error).message) }
  }

  const s = stats?.source
  const f = stats?.final
  const errors = stats?.passes.reduce((sum, p) => sum + p.errors, 0) ?? 0
  const tiles: [string, number | undefined, string?][] = [
    ['Rows in pass 1', f?.total, s ? `of ${n(s.rows)} source rows (${pct(f?.total ?? 0, s.rows)})` : undefined],
    ['Final tag (liquor)', f?.named, f && pct(f.named, f.total)],
    ['Final: Misc', f?.misc, f && pct(f.misc, f.total)],
    ['Waiting for a pass', f?.waiting, f && pct(f.waiting, f.total)],
    ['No tag (skipped / failed)', f?.no_tag, f && pct(f.no_tag, f.total)],
    ['Errors (all passes)', stats ? errors : undefined],
    ['CSV rows uploaded', stats?.counts.csv_records],
  ]
  return (
    <div className="dashboard">
      <h1>Dashboard</h1>
      {error && <div className="error">{error}</div>}

      <section className="card source">
        <div className="row">
          <div>
            <h2>Source data</h2>
            <p className="muted">
              Local copy of Athena <span className="mono">{stats ? `${stats.athena.database}.${stats.athena.table}` : '…'}</span>
              {stats?.athena.workgroup && <> (workgroup {stats.athena.workgroup}, {stats.athena.region})</>}
            </p>
          </div>
          <button onClick={refresh} disabled={job?.status === 'running'}>
            {job?.status === 'running' ? 'Refreshing…' : 'Refresh from Athena'}
          </button>
        </div>
        <dl className="facts">
          <div><dt>Rows</dt><dd>{s ? s.rows.toLocaleString() : '…'}</dd></div>
          <div><dt>Oldest insert</dt><dd>{fmtDate(s?.oldest_insert_dttm) || '–'}</dd></div>
          <div><dt>Newest insert</dt><dd>{fmtDate(s?.newest_insert_dttm) || '–'}</dd></div>
          <div><dt>Last refresh</dt><dd>{fmtDate(s?.loaded_at) || 'never'}</dd></div>
        </dl>
        {job && <LogView jobId={job.id} onFinish={() => { load(); setJob((j) => j && { ...j, status: 'done' }) }} />}
      </section>

      <section className="tiles">
        {tiles.map(([label, value, sub]) => (
          <div className="card tile" key={label}>
            <div className="muted">{label}</div>
            <div className="big">{stats ? n(value) : '…'}</div>
            {sub && <div className="muted small">{sub}</div>}
          </div>
        ))}
        <div className="card tile">
          <div className="muted">Pending by pass</div>
          {(['pass_1', 'pass_2', 'pass_3'] as const).map((k, i) => (
            <div className="pending" key={k}>
              <span className="muted">Pass {i + 1}</span>
              <strong>{stats ? n(stats.pending[k]) : '…'}</strong>
            </div>
          ))}
        </div>
      </section>

      {stats && stats.apis.length > 0 && (
        <section>
          <h2>Taggings per API key</h2>
          <div className="tiles">
            {stats.apis.map((a) => (
              <div className="card tile" key={a.api_name ?? ''}>
                <div className="muted mono">{a.api_name ?? '(not recorded)'}</div>
                <div className="big">{n(a.total)}</div>
                <div className="muted small">
                  P1 {n(a.pass_1)} · P2 {n(a.pass_2)} · P3 {n(a.pass_3)}
                  {a.errors > 0 && <> · <span className="err">{n(a.errors)} errors ({pct(a.errors, a.total)})</span></>}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="card">
        <h2>Per pass</h2>
        <table className="data">
          <thead><tr><th>Pass</th><th>Rows</th><th>Tagged</th><th>Skipped</th><th>Errors</th><th>Error rate</th></tr></thead>
          <tbody>
            {stats?.passes.map((p, i) => (
              <tr key={i}>
                <td>{passNames[i]}</td><td>{n(p.total)}</td><td>{n(p.tagged)}</td><td>{n(p.skipped)}</td>
                <td>{n(p.errors)}</td><td>{pct(p.errors, p.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Status per pass 1 row</h2>
        {stats && stats.decisions.length === 0 && <p className="muted">Nothing tagged yet. Add an <Link to="/keys">API key</Link>, then start <Link to="/commands">Pass 1</Link>.</p>}
        {stats && stats.decisions.length > 0 && (
          <table className="data">
            <thead><tr><th>Status</th><th>Rows</th></tr></thead>
            <tbody>
              {stats.decisions.map((d) => <tr key={d.decision}><td>{d.decision}</td><td>{d.n.toLocaleString()}</td></tr>)}
            </tbody>
          </table>
        )}
        <p className="muted" style={{ marginTop: 10 }}>Full detail in <Link to="/results">Results</Link> (final_results).</p>
      </section>
    </div>
  )
}
