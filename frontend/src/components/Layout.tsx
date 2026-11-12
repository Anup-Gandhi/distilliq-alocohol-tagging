import { NavLink, Outlet, useNavigate } from 'react-router'
import { auth, post } from '../api'
import './Layout.scss'

const links = [
  { to: '/', label: 'Dashboard' },
  { to: '/commands', label: 'Commands' },
  { to: '/keys', label: 'API keys' },
  { to: '/prompts', label: 'Prompts' },
  { to: '/results', label: 'Results' },
]

export default function Layout() {
  const navigate = useNavigate()
  const user = auth.user()

  async function signOut() {
    try { await post('/api/auth/logout') } catch { /* the session may already be gone */ }
    auth.clear()
    navigate('/login', { replace: true })
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">D</span>
          <span>DistillIQ Tagger</span>
        </div>
        <nav>
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.to === '/'}>{l.label}</NavLink>
          ))}
        </nav>
        <div className="user">
          <div className="name">{user ? `${user.first_name} ${user.last_name}`.trim() || user.email : ''}</div>
          <div className="email">{user?.email}</div>
          <button className="secondary small" onClick={signOut}>Sign out</button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
