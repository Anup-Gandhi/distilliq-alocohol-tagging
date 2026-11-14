import { useCallback, useEffect, useState, type ChangeEvent } from 'react'
import { api, download, fmtDate, post, upload } from '../api'
import LogView, { type Job } from '../components/LogView'
import Preview from '../components/Preview'
import './Commands.scss'

type Field = {
  key: string; label: string; type: 'key' | 'keys' | 'upload' | 'uploads' | 'prompt' | 'text' | 'number' | 'effort' | 'bool'
  required?: boolean; hint?: string; placeholder?: string
}

const shared: Field[] = [
  { key: 'max_cost', label: 'Spending cap (USD)', type: 'number', placeholder: '15' },
  { key: 'limit', label: 'At most N rows', type: 'number', placeholder: 'all' },
  { key: 'workers', label: 'Parallel requests', type: 'number', placeholder: 'TAG_WORKERS' },
  { key: 'effort', label: 'Reasoning effort', type: 'effort' },
  { key: 'retry_errors', label: 'Also redo rows that failed with an error', type: 'bool' },
  { key: 'no_web_search', label: 'No web search', type: 'bool' },
  { key: 'dry_run', label: 'Dry run: print, save nothing (still calls the model, default 5 rows)', type: 'bool' },
]

const commands: Record<string, { title: string; help: string; fields: Field[] }> = {
  pass1: {
    title: 'Pass 1',
    help: 'Tags new rows from the local topk_options copy (or an uploaded CSV) into llm_tags.',
    fields: [
      { key: 'llm', label: 'LLM key', type: 'key', required: true },
      { key: 'prompt', label: 'Prompt', type: 'prompt', placeholder: 'system_prompt.md' },
      { key: 'csv', label: 'CSV instead of topk_options', type: 'upload' },
      { key: 'full', label: 'Ignore the watermark and read the whole table (--full)', type: 'bool' },
      ...shared,
    ],
  },
  pass2: {
    title: 'Pass 2',
    help: 'Another provider checks the rows pass 1 called Misc - Not a Liquor or left without a tag. Leave the keys empty to use one key per provider.',
    fields: [
      { key: 'llm', label: 'LLM keys (one per provider)', type: 'keys' },
      { key: 'prompt', label: 'Prompt', type: 'prompt', placeholder: 'retag_system_prompt.md' },
      { key: 'from', label: 'Pass 1 table', type: 'text', placeholder: 'llm_tags' },
      { key: 'table', label: 'Save to table', type: 'text', placeholder: 'llm_retags' },
      ...shared,
    ],
  },
  pass3: {
    title: 'Pass 3',
    help: 'Names every row not confirmed Misc by passes 1 and 2, plus rows where they disagree or gave no tag. It does not see their answers.',
    fields: [
      { key: 'llm', label: 'LLM key', type: 'key', placeholder: 'final-tagger' },
      { key: 'prompt', label: 'Prompt', type: 'prompt', placeholder: 'pass3_system_prompt.md' },
      ...shared,
    ],
  },
  import: {
    title: 'Import',
    help: 'Uploads CSV files to csv_records without tagging them (for human_tag_comparison).',
    fields: [{ key: 'files', label: 'CSV files', type: 'uploads', required: true }],
  },
  export: { title: 'Export', help: 'Downloads llm_tags or llm_retags as CSV. Other tables and views: Results page.', fields: [] },
}

type Options = { prompts: string[]; uploads: { name: string; size: number }[]; efforts: string[] }
type KeyList = { keys: { name: string; provider: string; model?: string }[] }

export default function Commands() {
  const [command, setCommand] = useState('pass1')
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [options, setOptions] = useState<Options>({ prompts: [], uploads: [], efforts: [] })
  const [keys, setKeys] = useState<KeyList['keys']>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [current, setCurrent] = useState<Job | null>(null)
  const [view, setView] = useState<'preview' | 'log' | 'history'>('preview')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const loadJobs = useCallback(() => api<Job[]>('/api/jobs').then(setJobs).catch(() => {}), [])
  const loadOptions = useCallback(() => api<Options>('/api/options').then(setOptions).catch(() => {}), [])

  useEffect(() => {
    loadOptions()
    api<KeyList>('/api/keys').then((k) => setKeys(k.keys)).catch(() => {})
    loadJobs().then(() => {})
    api<Job[]>('/api/jobs').then((all) => { const running = all.find((j) => j.status === 'running'); if (running) { setCurrent(running); setView('log') } }).catch(() => {})
  }, [loadJobs, loadOptions])

  function pick(name: string) {
    setCommand(name)
    setValues({})
    setError('')
  }

  function set(key: string, value: unknown) {
    setValues((v) => ({ ...v, [key]: value }))
  }

  async function onUpload(e: ChangeEvent<HTMLInputElement>, key: string, multi: boolean) {
    const files = Array.from(e.target.files || [])
    e.target.value = ''
    setError('')
    try {
      const names: string[] = []
      for (const f of files) names.push((await upload<{ name: string }>('/api/uploads', f)).name)
      await loadOptions()
      set(key, multi ? [...((values[key] as string[]) || []), ...names] : names[0])
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function start() {
    setBusy(true)
    setError('')
    try {
      const job = await post<Job>('/api/jobs', { command, args: values })
      setCurrent(job)
      setView('log')
      loadJobs()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const spec = commands[command]
  // what the form still needs before a preview or run can work
  const missingField = spec.fields.find((f) => f.required && !(Array.isArray(values[f.key]) ? (values[f.key] as unknown[]).length : values[f.key]))
  const missingDefaultKey = spec.fields.find((f) => f.type === 'key' && !f.required && f.placeholder && !values[f.key]
    && !keys.some((k) => k.name === f.placeholder))
  const blocked = missingField ? `Choose the ${missingField.label} to see which rows would run.`
    : missingDefaultKey ? `Choose an LLM key to see which rows would run. The default, ${missingDefaultKey.placeholder}, isn't on the API keys page.`
    : undefined
  // same rule as conflict() in backend/api/jobs.py: different passes may overlap, anything else runs alone
  const runningJobs = jobs.filter((j) => j.status === 'running')
  const isPass = (c: string) => ['pass1', 'pass2', 'pass3'].includes(c)
  const blocker = jobs.find((j) => j.status === 'running' && (j.command === command || !isPass(command) || !isPass(j.command)))

  function field(f: Field) {
    const v = values[f.key]
    switch (f.type) {
      case 'bool':
        return <label className="check"><input type="checkbox" checked={!!v} onChange={(e) => set(f.key, e.target.checked)} />{f.label}</label>
      case 'key':
        return (
          <select value={(v as string) || ''} onChange={(e) => set(f.key, e.target.value)}>
            <option value="">{f.placeholder ? `default (${f.placeholder})` : 'choose a key…'}</option>
            {keys.map((k) => <option key={k.name} value={k.name}>{k.name} · {k.provider}{k.model ? ` · ${k.model}` : ''}</option>)}
          </select>
        )
      case 'keys':
        return (
          <div className="checks">
            {keys.length === 0 && <span className="muted">No keys yet.</span>}
            {keys.map((k) => {
              const chosen = (v as string[]) || []
              return (
                <label className="check" key={k.name}>
                  <input type="checkbox" checked={chosen.includes(k.name)}
                         onChange={(e) => set(f.key, e.target.checked ? [...chosen, k.name] : chosen.filter((n) => n !== k.name))} />
                  {k.name} · {k.provider}
                </label>
              )
            })}
          </div>
        )
      case 'prompt':
        return (
          <select value={(v as string) || ''} onChange={(e) => set(f.key, e.target.value)}>
            <option value="">{f.placeholder ? `default (${f.placeholder})` : 'choose a prompt…'}</option>
            {options.prompts.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        )
      case 'upload':
        return (
          <div className="row">
            <select value={(v as string) || ''} onChange={(e) => set(f.key, e.target.value)}>
              <option value="">none (use topk_options)</option>
              {options.uploads.map((u) => <option key={u.name} value={u.name}>{u.name}</option>)}
            </select>
            <label className="btn secondary small">Upload<input type="file" accept=".csv" hidden onChange={(e) => onUpload(e, f.key, false)} /></label>
          </div>
        )
      case 'uploads': {
        const chosen = (v as string[]) || []
        return (
          <div>
            <div className="checks">
              {options.uploads.map((u) => (
                <label className="check" key={u.name}>
                  <input type="checkbox" checked={chosen.includes(u.name)}
                         onChange={(e) => set(f.key, e.target.checked ? [...chosen, u.name] : chosen.filter((n) => n !== u.name))} />
                  {u.name} <span className="muted">&nbsp;({Math.round(u.size / 1024)} KB)</span>
                </label>
              ))}
            </div>
            <label className="btn secondary small">Upload CSV files<input type="file" accept=".csv" multiple hidden onChange={(e) => onUpload(e, f.key, true)} /></label>
          </div>
        )
      }
      case 'effort':
        return (
          <select value={(v as string) || ''} onChange={(e) => set(f.key, e.target.value)}>
            <option value="">the key file's (default medium)</option>
            {options.efforts.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        )
      case 'number':
        return <input type="number" step="any" placeholder={f.placeholder} value={(v as string) ?? ''} onChange={(e) => set(f.key, e.target.value)} />
      default:
        return <input type="text" placeholder={f.placeholder} value={(v as string) ?? ''} onChange={(e) => set(f.key, e.target.value)} />
    }
  }

  return (
    <div className="commands">
      <h1>Commands</h1>
      <div className="columns">
        <div className="left">
          <div className="tabs">
            {Object.keys(commands).map((name) => (
              <button key={name} className={name === command ? 'tab active' : 'tab'} onClick={() => pick(name)}>{commands[name].title}</button>
            ))}
          </div>
          <div className="card">
            <h2>{spec.title}</h2>
            <p className="muted">{spec.help}</p>
            {command === 'export' ? (
              <div className="row">
                <button className="secondary" onClick={() => download('/api/results/llm_tags/csv', 'llm_tags.csv')}>Download llm_tags.csv</button>
                <button className="secondary" onClick={() => download('/api/results/llm_retags/csv', 'llm_retags.csv')}>Download llm_retags.csv</button>
              </div>
            ) : (
              <>
                {spec.fields.map((f) => (
                  <div className="field" key={f.key}>
                    {f.type !== 'bool' && <label>{f.label}{f.required && ' *'}</label>}
                    {field(f)}
                  </div>
                ))}
                {error && <div className="error">{error}</div>}
                <button onClick={start} disabled={busy || !!blocker}>
                  {blocker ? `A ${blocker.command} job is running…` : `Start ${spec.title}`}
                </button>
              </>
            )}
          </div>
        </div>

        <div className="right card">
          <div className="tabs">
            <button className={view === 'preview' ? 'tab active' : 'tab'} onClick={() => setView('preview')}>Preview</button>
            <button className={view === 'log' ? 'tab active' : 'tab'} onClick={() => setView('log')}>
              Log{current?.status === 'running' && <span className="badge running">running</span>}
            </button>
            <button className={view === 'history' ? 'tab active' : 'tab'} onClick={() => { loadJobs(); setView('history') }}>History</button>
          </div>

          {view === 'preview' && (spec.fields.length && command.startsWith('pass')
            ? <Preview command={command} values={values} blocked={blocked} />
            : <p className="muted">{spec.title} has no rows to preview.</p>)}

          {view === 'log' && runningJobs.length > 1 && (
            <div className="tabs">
              {runningJobs.map((j) => (
                <button key={j.id} className={current?.id === j.id ? 'tab active' : 'tab'} onClick={() => setCurrent(j)}>{j.command}</button>
              ))}
            </div>
          )}
          {view === 'log' && (current
            ? <LogView key={current.id} jobId={current.id} onFinish={loadJobs} />
            : <p className="muted">No job yet. Start one, or open a past job from History.</p>)}

          {view === 'history' && (jobs.length === 0 ? <p className="muted">No jobs yet.</p> : (
            <table className="data jobs">
              <thead><tr><th>Command</th><th>Status</th><th>Started</th><th>By</th></tr></thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id} className={current?.id === j.id ? 'selected' : ''} onClick={() => { setCurrent(j); setView('log') }}>
                    <td className="mono" title={j.argv.join(' ')}>{j.argv.join(' ')}</td>
                    <td><span className={`badge ${j.status}`}>{j.status}</span></td>
                    <td>{fmtDate(j.started_at)}</td>
                    <td>{j.started_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        </div>
      </div>
    </div>
  )
}
