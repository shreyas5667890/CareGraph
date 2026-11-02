import { useCallback, useEffect, useState } from 'react'
import PatientAuth from '../components/PatientAuth.jsx'
import BookingModal from '../components/BookingModal.jsx'
import {
  EMPTY_PATIENT_SESSION,
  cancelAppointment,
  fetchMyAppointments,
} from '../api/client.js'

const MISSING = 'Not listed'

const TABS = [
  { key: 'upcoming', label: 'Upcoming' },
  { key: 'past', label: 'Past' },
  { key: 'cancelled', label: 'Cancelled' },
]

function formatRupees(value) {
  if (value === null || value === undefined || value === '') return MISSING
  const number = Number(value)
  if (Number.isNaN(number)) return MISSING
  return `₹${number.toLocaleString('en-IN')}`
}

function formatDate(value) {
  if (!value) return MISSING
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString('en-IN', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function AppointmentCard({ item, onCancel, onReschedule }) {
  const actionable = item.category === 'upcoming' && item.status === 'booked'
  return (
    <article className="appointment-card">
      <header className="appointment-head">
        <div>
          <h3>{item.doctor_name || item.doctor_id}</h3>
          <p className="doctor-specialty">{item.specialty || MISSING}</p>
        </div>
        <span className={`status status-${item.status}`}>{item.status}</span>
      </header>

      <dl className="doctor-meta">
        <div>
          <dt>Date</dt>
          <dd>{formatDate(item.date)}</dd>
        </div>
        <div>
          <dt>Time</dt>
          <dd>
            {item.start_time}–{item.end_time}
          </dd>
        </div>
        <div>
          <dt>Duration</dt>
          <dd>{item.duration_minutes ? `${item.duration_minutes} min` : MISSING}</dd>
        </div>
        <div>
          <dt>Fee</dt>
          <dd>{formatRupees(item.fee_inr)}</dd>
        </div>
        <div>
          <dt>Type</dt>
          <dd>{item.consultation_type || MISSING}</dd>
        </div>
        <div>
          <dt>City</dt>
          <dd>{item.doctor_city || MISSING}</dd>
        </div>
      </dl>

      <footer className="appointment-foot">
        <span className="doctor-id">ID: {item.appointment_id}</span>
        {actionable && (
          <div className="row">
            <button type="button" className="secondary" onClick={() => onReschedule(item)}>
              Reschedule
            </button>
            <button type="button" className="danger-solid" onClick={() => onCancel(item)}>
              Cancel
            </button>
          </div>
        )}
      </footer>
    </article>
  )
}

export default function Appointments({ patient, onPatientChange }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [banner, setBanner] = useState(null)
  const [tab, setTab] = useState('upcoming')

  const [cancelTarget, setCancelTarget] = useState(null)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState(null)
  const [rescheduleTarget, setRescheduleTarget] = useState(null)

  const authenticated = Boolean(patient?.authenticated)

  const load = useCallback(async () => {
    if (!authenticated) {
      setData(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const payload = await fetchMyAppointments()
      setData(payload)
    } catch (err) {
      if (err.status === 401) {
        onPatientChange({ ...EMPTY_PATIENT_SESSION })
        return
      }
      setError(err.message || 'Could not load appointments.')
    } finally {
      setLoading(false)
    }
  }, [authenticated, onPatientChange])

  useEffect(() => {
    load()
  }, [load])

  async function confirmCancel() {
    if (!cancelTarget) return
    setCancelling(true)
    setCancelError(null)
    try {
      await cancelAppointment(cancelTarget.appointment_id, patient.csrfToken)
      setBanner(`Appointment ${cancelTarget.appointment_id} was cancelled.`)
      setCancelTarget(null)
      load()
    } catch (err) {
      if (err.status === 401) {
        onPatientChange({ ...EMPTY_PATIENT_SESSION })
        return
      }
      setCancelError(err.message || 'Could not cancel the appointment.')
    } finally {
      setCancelling(false)
    }
  }

  if (!authenticated) {
    return (
      <PatientAuth
        onSuccess={onPatientChange}
        note="Sign in to view and manage your appointments. Your booking history is tied to this account."
      />
    )
  }

  const counts = data?.counts || { upcoming: 0, past: 0, cancelled: 0, total: 0 }
  const items = (data && data[tab]) || []

  return (
    <div className="stack">
      <section className="panel">
        <div className="admin-head">
          <div>
            <h2>My appointments</h2>
            <p className="hint">Signed in as {patient.username}.</p>
          </div>
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setBanner(null)
              load()
            }}
            disabled={loading}
          >
            Refresh
          </button>
        </div>

        {banner && <p className="alert ok">{banner}</p>}
        {error && <p className="alert error">{error}</p>}

        <div className="tabs">
          {TABS.map((entry) => (
            <button
              type="button"
              key={entry.key}
              className={tab === entry.key ? 'chip-button active' : 'chip-button'}
              onClick={() => setTab(entry.key)}
            >
              {entry.label} ({counts[entry.key] ?? 0})
            </button>
          ))}
        </div>

        {loading ? (
          <p className="hint">Loading appointments…</p>
        ) : items.length === 0 ? (
          <p className="empty">
            {tab === 'upcoming'
              ? 'You have no upcoming appointments. Book one from the “Find a doctor” page.'
              : `No ${tab} appointments.`}
          </p>
        ) : (
          <div className="appointments">
            {items.map((item) => (
              <AppointmentCard
                key={item.appointment_id}
                item={item}
                onCancel={setCancelTarget}
                onReschedule={setRescheduleTarget}
              />
            ))}
          </div>
        )}
      </section>

      {cancelTarget && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal narrow">
            <h3>Cancel appointment?</h3>
            <p>
              This cancels <strong>{cancelTarget.doctor_name || cancelTarget.doctor_id}</strong> on{' '}
              {formatDate(cancelTarget.date)} at {cancelTarget.start_time}. The slot is released and
              the record is kept in your history.
            </p>
            {cancelError && <p className="alert error">{cancelError}</p>}
            <div className="row">
              <button
                type="button"
                className="danger-solid"
                onClick={confirmCancel}
                disabled={cancelling}
              >
                {cancelling ? 'Cancelling…' : 'Cancel appointment'}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setCancelTarget(null)
                  setCancelError(null)
                }}
                disabled={cancelling}
              >
                Keep it
              </button>
            </div>
          </div>
        </div>
      )}

      {rescheduleTarget && (
        <BookingModal
          appointment={rescheduleTarget}
          patient={patient}
          onPatientChange={onPatientChange}
          onClose={() => setRescheduleTarget(null)}
          onDone={() => {
            setBanner(`Appointment ${rescheduleTarget.appointment_id} was rescheduled.`)
            load()
          }}
        />
      )}
    </div>
  )
}
