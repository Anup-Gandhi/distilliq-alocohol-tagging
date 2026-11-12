import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router'
import { auth } from './api'
import Layout from './components/Layout'
import Commands from './routes/Commands'
import Dashboard from './routes/Dashboard'
import Keys from './routes/Keys'
import Login from './routes/Login'
import Prompts from './routes/Prompts'
import Results from './routes/Results'

function RequireAuth() {
  return auth.token() ? <Outlet /> : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth />}>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="commands" element={<Commands />} />
            <Route path="keys" element={<Keys />} />
            <Route path="prompts" element={<Prompts />} />
            <Route path="results" element={<Results />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
