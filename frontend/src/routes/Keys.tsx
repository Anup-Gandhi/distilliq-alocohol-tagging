import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, del, post } from '../api'
import './Keys.scss'

type KeyFile = {
  name: string; provider: string; api_key: string; model?: string; effort?: string
  input_price?: number; output_price?: number; search_price?: number; base_url?: string
}
type KeyList = { keys: KeyFile[]; providers: Record<string, string>; efforts: string[] }

const empty = { name: '', provider: 'anthropic', api_key: '', model: '', effort: '', input_price: '', output_price: '', search_price: '', base_url: '' }

export default function Keys() {
  const [list, setList] = useState<KeyList>({ keys: [], providers: {}, efforts: [] })
  const [form, setForm] = useState(empty)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [more, setMore] = useState(false)

  const load = useCallback(() => api<KeyList>('/api/keys').then(setList).catch((e) => setError(e.message)), [])
  useEffect(() => { load() }, [load])

  function set(key: keyof typeof empty, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    const body: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(form)) {
      if (v === '') continue
      body[k] = ['input_price', 'output_price', 'search_price'].includes(k) ? Number(v) : v
    }
    try {
      await post('/api/keys', body)
      setForm(empty)
      await load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(name: string) {
    if (!window.confirm(`Delete the key file "${name}"?`)) return
    try { await del(`/api/keys/${name}`); await load() } catch (err) { setError((err as Error).message) }
  }

  return (
    <div className="keys">
      <h1>API keys</h1>
      <p className="muted">
        Each key is a JSON file in <span className="mono">backend/secrets/</span>. Its name is what commands take as the LLM key.
        Store as many per provider as you need.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="columns">
        <div className="card">
          <h2>Stored keys</h2>
          {list.keys.length === 0 && <p className="muted">None yet.</p>}
          {list.keys.length > 0 && (
            <table className="data">
              <thead><tr><th>Name</th><th>Provider</th><th>Model</th><th>Effort</th><th>Key</th><th></th></tr></thead>
              <tbody>
                {list.keys.map((k) => (
                  <tr key={k.name}>
                    <td className="mono">{k.name}</td>
                    <td>{k.provider}</td>
                    <td>{k.model || <span className="muted">default ({list.providers[k.provider]})</span>}</td>
                    <td>{k.effort || <span className="muted">medium</span>}</td>
                    <td className="mono">{k.api_key}</td>
                    <td><button className="danger small" onClick={() => remove(k.name)}>Delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <form className="card" onSubmit={save}>
          <h2>Add a key</h2>
          <div className="field">
            <label>Name *</label>
            <input value={form.name} required pattern="[A-Za-z0-9][A-Za-z0-9._-]*" placeholder="claude, chatgpt, gemini, final-tagger…"
                   onChange={(e) => set('name', e.target.value)} />
          </div>
          <div className="field">
            <label>Provider *</label>
            <select value={form.provider} onChange={(e) => set('provider', e.target.value)}>
              {Object.keys(list.providers).map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div className="field">
            <label>API key *</label>
            <input value={form.api_key} required type="password" autoComplete="off" onChange={(e) => set('api_key', e.target.value)} />
          </div>
          <div className="field">
            <label>Model</label>
            <input value={form.model} placeholder={`default: ${list.providers[form.provider] || ''}`} onChange={(e) => set('model', e.target.value)} />
          </div>
          <div className="field">
            <label>Effort</label>
            <select value={form.effort} onChange={(e) => set('effort', e.target.value)}>
              <option value="">medium (default)</option>
              {list.efforts.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </div>
          <button type="button" className="secondary small" onClick={() => setMore(!more)}>{more ? 'Hide' : 'Show'} price options</button>
          {more && (
            <div className="prices">
              <p className="muted">Needed only for a model not in the built-in price list. USD per 1M tokens, and per web search.</p>
              <div className="row">
                <div className="field"><label>Input price</label><input type="number" step="any" value={form.input_price} onChange={(e) => set('input_price', e.target.value)} /></div>
                <div className="field"><label>Output price</label><input type="number" step="any" value={form.output_price} onChange={(e) => set('output_price', e.target.value)} /></div>
                <div className="field"><label>Search price</label><input type="number" step="any" value={form.search_price} onChange={(e) => set('search_price', e.target.value)} /></div>
              </div>
              <div className="field"><label>Base URL (Gemini web proxy only)</label><input value={form.base_url} placeholder="http://localhost:4981/openai/v1" onChange={(e) => set('base_url', e.target.value)} /></div>
            </div>
          )}
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save key'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}
