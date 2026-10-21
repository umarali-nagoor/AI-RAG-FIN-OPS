import { NavLink, Outlet } from 'react-router-dom'

const NAV = [
  { to: '/',           label: '⊞  Repositories' },
  { to: '/analyze',    label: '⬡  Root Cause' },
  { to: '/optimize',   label: '◈  Optimizations' },
  { to: '/pr-review',  label: '⊕  PR Review' },
  { to: '/ask',        label: '◎  Ask' },
  { to: '/analytics',  label: '▤  Platform Ranking' },
  { to: '/pricing',    label: '＄  Pricing' },
]

export default function Layout() {
  return (
    <div className="layout">
      <header className="header">
        <h1>IBM Cloud Logs — FinOps Copilot</h1>
        <span className="badge">BETA</span>
      </header>
      <div className="body">
        <nav className="sidebar">
          {NAV.map(({ to, label }) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => isActive ? 'active' : ''}>
              {label}
            </NavLink>
          ))}
        </nav>
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
