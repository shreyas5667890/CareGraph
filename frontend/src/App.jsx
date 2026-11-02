import { useEffect, useState } from 'react'
import DoctorRecommendation from './pages/DoctorRecommendation.jsx'
import Appointments from './pages/Appointments.jsx'
import AdminLogin from './pages/AdminLogin.jsx'
import AdminDoctors from './pages/AdminDoctors.jsx'
import {
  EMPTY_PATIENT_SESSION,
  adminLogout,
  adminMe,
  patientLogout,
  patientMe,
  patientSessionFrom,
} from './api/client.js'

// Simple state-based routing — no router dependency needed for these pages.
const PAGES = {
  recommend: 'recommend',
  appointments: 'appointments',
  admin: 'admin',
}

export default function App() {
  const [page, setPage] = useState(PAGES.recommend)
  // Admin session lives in memory only; the Flask session cookie is HttpOnly.
  const [admin, setAdmin] = useState({ username: null, csrfToken: null, configured: true })
  // Patient session mirrors the same pattern (cookie in the browser, CSRF in memory).
  const [patient, setPatient] = useState({ ...EMPTY_PATIENT_SESSION })

  // Restore the admin and patient sessions after a page reload (cookie-based).
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
    patientMe()
      .then((data) => {
        if (cancelled || !data) return
        setPatient(patientSessionFrom(data))
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

  async function handlePatientLogout() {
    try {
      await patientLogout(patient.csrfToken)
    } catch {
      /* ignore — clear local state regardless */
    }
    setPatient({ ...EMPTY_PATIENT_SESSION })
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
            className={page === PAGES.appointments ? 'nav-link active' : 'nav-link'}
            onClick={() => setPage(PAGES.appointments)}
          >
            My appointments
          </button>
          <button
            type="button"
            className={page === PAGES.admin ? 'nav-link active' : 'nav-link'}
            onClick={() => setPage(PAGES.admin)}
          >
            Admin
          </button>
          {patient.authenticated && (
            <button type="button" className="nav-link danger" onClick={handlePatientLogout}>
              Sign out ({patient.username})
            </button>
          )}
          {admin.username && (
            <button type="button" className="nav-link danger" onClick={handleLogout}>
              Log out ({admin.username})
            </button>
          )}
        </nav>
      </header>

      <main className="content">
        {page === PAGES.recommend && (
          <DoctorRecommendation patient={patient} onPatientChange={setPatient} />
        )}
        {page === PAGES.appointments && (
          <Appointments patient={patient} onPatientChange={setPatient} />
        )}
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
        Educational prototype. Model output is not a diagnosis. Appointment schedules are
        system-generated configuration, not verified provider availability.
      </footer>
    </div>
  )
}
