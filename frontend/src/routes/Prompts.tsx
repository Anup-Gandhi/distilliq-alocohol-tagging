import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import './Prompts.scss'

type PromptInfo = { name: string; used_by: string | null; size: number; updated_at: number }

function put(name: string, content: string) {
  return api(`/api/prompts/${encodeURIComponent(name)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
  })
}

export default function Prompts() {
  const [list, setList] = useState<PromptInfo[]>([])
  const [name, setName] = useState('')
  const [saved, setSaved] = useState('')
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const dirty = draft !== saved

  const loadList = useCallback(() => api<PromptInfo[]>('/api/prompts').then((l) => { setList(l); return l }), [])

  const open = useCallback(async (n: string) => {
    setError('')
    setNotice('')
    try {
      const p = await api<{ content: string }>(`/api/prompts/${encodeURIComponent(n)}`)
      setName(n)
      setSaved(p.content)
      setDraft(p.content)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    loadList().then((l) => { if (l.length) open(l[0].name) }).catch((e) => setError(e.message))
  }, [loadList, open])

  // warn before closing the tab with unsaved edits
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  function choose(n: string) {
    if (n === name) return
    if (dirty && !window.confirm(`Discard your unsaved changes to ${name}?`)) return
    open(n)
  }

  async function save() {
    setBusy(true)
    setError('')
    try {
      await put(name, draft)
      setSaved(draft)
      setNotice('Saved. The next job that uses this prompt will read the new text.')
      loadList()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function saveAs() {
    let n = window.prompt('Name for the new prompt (letters, digits, . - _):', name.replace(/\.md$/, '_copy.md'))
    if (!n) return
    n = n.trim()
    if (!n.endsWith('.md')) n += '.md'
    if (list.some((p) => p.name === n) && !window.confirm(`${n} already exists. Overwrite it?`)) return
    setBusy(true)
    setError('')
    try {
      await put(n, draft)
      await loadList()
      setName(n)
      setSaved(draft)
      setNotice(`Saved as ${n}. No pass uses it until it replaces one of the files a pass reads.`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const current = list.find((p) => p.name === name)
  return (
    <div className="prompts">
      <h1>Prompts</h1>
      <p className="muted">
        System prompts sent to the models, stored in <span className="mono">backend/prompts/</span>.
        A command reads them when it starts, so an edit applies from the next job on.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="columns">
        <div className="card list">
          {list.map((p) => (
            <button key={p.name} className={p.name === name ? 'item active' : 'item'} onClick={() => choose(p.name)}>
              <span className="mono">{p.name}</span>
              <span className="muted">{p.used_by ? `used by ${p.used_by}` : 'not used by a pass'}</span>
            </button>
          ))}
        </div>

        <div className="card editor">
          <div className="bar">
            <span className="mono">{name}</span>
            {dirty && <span className="badge running">unsaved</span>}
            <button className="secondary small" onClick={() => setDraft(saved)} disabled={!dirty || busy}>Undo changes</button>
            <button className="secondary small" onClick={saveAs} disabled={!name || busy}>Save as new…</button>
            <button className="small" onClick={save} disabled={!dirty || busy}>{busy ? 'Saving…' : 'Save'}</button>
          </div>
          {current?.used_by && (
            <p className="muted hint">
              {current.used_by} uses this file. Keep the JSON output format it describes,
              or that pass will fail to read the model's answers.
            </p>
          )}
          {notice && !dirty && <p className="notice">{notice}</p>}
          <textarea value={draft} spellCheck={false} onChange={(e) => setDraft(e.target.value)} disabled={!name}
                    onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); if (dirty) save() } }} />
        </div>
      </div>
    </div>
  )
}
