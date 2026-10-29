import { useEffect, useState } from 'react'
import DoctorRecommendation from './pages/DoctorRecommendation.jsx'
import AdminLogin from './pages/AdminLogin.jsx'
import AdminDoctors from './pages/AdminDoctors.jsx'
import { adminLogout, adminMe } from './api/client.js'

// Simple state-based routing — no router dependency needed for three pages.
const PAGES = {
  recommend: 'recommend',
  admin: 'admin',
}

export default function App() {
  const [page, setPage] = useState(PAGES.recommend)
  // Admin session lives in memory only; the Flask session cookie is HttpOnly.
  const [admin, setAdmin] = useState({ username: null, csrfToken: null, configured: true })

  // Restore the admin session after a page reload (cookie-based).
  useEffect(() => {
    let cancelled = false
    adminMe()
      .then((data) => {
        if (cancelled || !data) return
        setAdmin({
          username: data.authenticated ? data.username : null,
          csrfToken: data.authenticated ? data.csrf_token : null,
          configured: data.admin_configured !== false,
        })
      })
      .catch(() => {
        /* backend unreachable — stay logged out */
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleLogout() {
    try {
      await adminLogout(admin.csrfToken)
    } catch {
      /* ignore — clear local state regardless */
    }
    setAdmin({ username: null, csrfToken: null, configured: true })
    setPage(PAGES.recommend)
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">CG</span>
          <div>
            <div className="brand-name">CareGraph</div>
            <div className="brand-sub">Symptom → Specialty → Doctor</div>
          </div>
        </div>
        <nav className="nav">
          <button
            type="button"
            className={page === PAGES.recommend ? 'nav-link active' : 'nav-link'}
            onClick={() => setPage(PAGES.recommend)}
          >
            Find a doctor
          </button>
          <button
            type="button"
            className={page === PAGES.admin ? 'nav-link active' : 'nav-link'}
            onClick={() => setPage(PAGES.admin)}
          >
            Admin
          </button>
          {admin.username && (
            <button type="button" className="nav-link danger" onClick={handleLogout}>
              Log out ({admin.username})
            </button>
          )}
        </nav>
      </header>

      <main className="content">
        {page === PAGES.recommend && <DoctorRecommendation />}
        {page === PAGES.admin &&
          (admin.username ? (
            <AdminDoctors admin={admin} onLogout={handleLogout} />
          ) : (
            <AdminLogin
              configured={admin.configured}
              onSuccess={(username, csrfToken) => {
                setAdmin({ username, csrfToken, configured: true })
              }}
            />
          ))}
      </main>

      <footer className="footer">
        Educational prototype. Model output is not a diagnosis. Doctor directory
        records are synthetic demo data — verify real providers before patient use.
      </footer>
    </div>
  )
}
