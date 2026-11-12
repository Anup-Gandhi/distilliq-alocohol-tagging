import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, fmtDate, post } from '../api'
import LogView, { type Job } from '../components/LogView'
import './Dashboard.scss'

type Stats = {
  source: { rows: number; oldest_insert_dttm: string | null; newest_insert_dttm: string | null; loaded_at: string | null }
  counts: Record<string, number>
  decisions: { decision: string; n: number }[]
  athena: { database: string; table: string; region: string; workgroup: string | null }
}

const countLabels: Record<string, string> = {
  llm_tags: 'Pass 1 tags', llm_retags: 'Pass 2 reviews', llm_final_tags: 'Pass 3 decisions', csv_records: 'CSV rows uploaded',
}

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
        {Object.entries(countLabels).map(([key, label]) => (
          <div className="card tile" key={key}>
            <div className="muted">{label}</div>
            <div className="big">{stats ? (stats.counts[key] ?? 0).toLocaleString() : '…'}</div>
          </div>
        ))}
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
