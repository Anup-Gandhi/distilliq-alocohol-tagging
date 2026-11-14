// A thin wrapper over fetch: adds the JWT, parses JSON, and signs the user out on 401.
const TOKEN = 'tagger.token'
const USER = 'tagger.user'

export type User = { id: number; email: string; first_name: string; last_name: string }

export const auth = {
  token: () => localStorage.getItem(TOKEN),
  user: (): User | null => JSON.parse(localStorage.getItem(USER) || 'null'),
  save(token: string, user: User) {
    localStorage.setItem(TOKEN, token)
    localStorage.setItem(USER, JSON.stringify(user))
  },
  clear() {
    localStorage.removeItem(TOKEN)
    localStorage.removeItem(USER)
  },
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) }
  const token = auth.token()
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(path, { ...init, headers })
  if (response.status === 401 && !path.endsWith('/login')) {
    auth.clear()
    window.location.assign('/login')
  }
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch { /* not JSON */ }
    throw new ApiError(response.status, detail)
  }
  return response
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  return (await request(path, init)).json()
}

export function post<T = unknown>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body) })
}

export function del<T = unknown>(path: string): Promise<T> {
  return api<T>(path, { method: 'DELETE' })
}

export function upload<T = unknown>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  return api<T>(path, { method: 'POST', body: form })
}

// Downloads need the Authorization header, which a plain link can't send.
export async function download(path: string, filename: string, transform?: (body: string) => string) {
  const response = await request(path)
  const blob = transform ? new Blob([transform(await response.text())], { type: 'text/csv' }) : await response.blob()
  const url = URL.createObjectURL(blob)
  const a = Object.assign(document.createElement('a'), { href: url, download: filename })
  a.click()
  URL.revokeObjectURL(url)
}

export const fmtDate = (value: unknown) =>
  value ? new Date(String(value)).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : ''
