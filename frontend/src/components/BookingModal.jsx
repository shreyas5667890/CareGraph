import { useCallback, useEffect, useState } from 'react'
import { createAppointment, fetchDoctorSlots, rescheduleAppointment } from '../api/client.js'
import PatientAuth from './PatientAuth.jsx'

const MAX_ADVANCE_DAYS = 90

function toIso(date) {
  const offset = date.getTimezoneOffset()
  return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 10)
}

function todayIso() {
  return toIso(new Date())
}

function maxDateIso() {
  const date = new Date()
  date.setDate(date.getDate() + MAX_ADVANCE_DAYS)
  return toIso(date)
}

function formatRupees(value) {
  if (value === null || value === undefined || value === '') return 'Not listed'
  const number = Number(value)
  if (Number.isNaN(number)) return 'Not listed'
  return `₹${number.toLocaleString('en-IN')}`
}

function slotReason(reason) {
  if (reason === 'past') return 'Too soon to book'
  if (reason === 'booked') return 'Already booked'
  return 'Unavailable'
}

// Booking panel. Handles both a new booking (doctor) and a reschedule
// (appointment). The confirmation view is only shown after the backend
// transaction succeeds — there is no simulated confirmation.
export default function BookingModal({
  doctor,
  specialty,
  patient,
  appointment,
  onPatientChange,
  onClose,
  onDone,
}) {
  const isReschedule = Boolean(appointment)
  const doctorId = isReschedule ? appointment.doctor_id : doctor?.doctor_id
  const doctorName = isReschedule ? appointment.doctor_name : doctor?.name
  const specialtyName = isReschedule
    ? appointment.specialty
    : specialty || doctor?.specialization

  const [date, setDate] = useState(isReschedule ? appointment.date : todayIso())
  const [dayData, setDayData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState(null)
  const [slot, setSlot] = useState(isReschedule ? appointment.start_time : null)
  const [consultationType, setConsultationType] = useState(
    isReschedule ? appointment.consultation_type || '' : '',
  )
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState(null)
  const [confirmation, setConfirmation] = useState(null)

  const authenticated = Boolean(patient?.authenticated)

  const loadSlots = useCallback(async () => {
    if (!authenticated || !doctorId || !date) return
    setLoading(true)
    setLoadError(null)
    try {
      const data = await fetchDoctorSlots(doctorId, date)
      setDayData(data)
      setConsultationType((prev) => prev || data.consultation_type || '')
      setSlot((prev) => {
        const stillFree = (data.slots || []).some((s) => s.start === prev && s.available)
        return stillFree ? prev : null
      })
    } catch (err) {
      setDayData(null)
      setLoadError(err.message || 'Could not load availability for this day.')
    } finally {
      setLoading(false)
    }
  }, [authenticated, doctorId, date])

  useEffect(() => {
    loadSlots()
  }, [loadSlots])

  async function handleConfirm() {
    if (!slot) {
      setSubmitError('Choose an available time slot first.')
      return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      if (isReschedule) {
        const data = await rescheduleAppointment(
          appointment.appointment_id,
          { date, start_time: slot },
          patient.csrfToken,
        )
        setConfirmation(data.appointment)
      } else {
        const data = await createAppointment(
          {
            doctor_id: doctorId,
            date,
            start_time: slot,
            consultation_type: consultationType || undefined,
          },
          patient.csrfToken,
        )
        setConfirmation(data.appointment)
      }
      if (onDone) onDone()
    } catch (err) {
      setSubmitError(err.message || 'Could not complete the appointment.')
      // The slot may have gone while confirming — refresh so the UI is honest.
      loadSlots()
    } finally {
      setSubmitting(false)
    }
  }

  function closeAll() {
    onClose()
  }

  if (!authenticated) {
    return (
      <div className="modal-backdrop" role="dialog" aria-modal="true">
        <div className="modal">
          <div className="admin-head">
            <h3>Sign in to book</h3>
            <button type="button" className="secondary" onClick={closeAll}>
              Close
            </button>
          </div>
          <PatientAuth
            onSuccess={onPatientChange}
            note={`Booking ${doctorName || 'this doctor'} requires a patient account, so the appointment can be shown in your history.`}
          />
        </div>
      </div>
    )
  }

  if (confirmation) {
    return (
      <div className="modal-backdrop" role="dialog" aria-modal="true">
        <div className="modal">
          <h3>{isReschedule ? 'Appointment rescheduled' : 'Appointment confirmed'}</h3>
          <p className="alert ok">
            {isReschedule
              ? 'Your appointment has been rescheduled and the change is saved.'
              : 'Your appointment was created and saved.'}
          </p>
          <dl className="confirm-grid">
            <div>
              <dt>Appointment ID</dt>
              <dd>{confirmation.appointment_id}</dd>
            </div>
            <div>
              <dt>Doctor</dt>
              <dd>{confirmation.doctor_name || confirmation.doctor_id}</dd>
            </div>
            <div>
              <dt>Specialty</dt>
              <dd>{confirmation.specialty || 'Not listed'}</dd>
            </div>
            <div>
              <dt>Date</dt>
              <dd>{confirmation.date}</dd>
            </div>
            <div>
              <dt>Time</dt>
              <dd>
                {confirmation.start_time}–{confirmation.end_time}
              </dd>
            </div>
            <div>
              <dt>Consultation fee</dt>
              <dd>{formatRupees(confirmation.fee_inr)}</dd>
            </div>
            <div>
              <dt>Consultation type</dt>
              <dd>{confirmation.consultation_type || 'In-person'}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{confirmation.status}</dd>
            </div>
          </dl>
          <div className="row">
            <button type="button" className="primary" onClick={closeAll}>
              Done
            </button>
          </div>
        </div>
      </div>
    )
  }

  const slots = dayData?.slots || []
  const consultationTypes = dayData?.consultation_types || ['In-person']

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <div className="admin-head">
          <h3>{isReschedule ? 'Reschedule appointment' : 'Book an appointment'}</h3>
          <button type="button" className="secondary" onClick={closeAll} disabled={submitting}>
            Close
          </button>
        </div>

        <p className="hint">
          <strong>{doctorName}</strong>
          {specialtyName ? ` · ${specialtyName}` : ''} · ID: {doctorId}
        </p>

        <div className="form-grid">
          <label>
            Appointment date
            <input
              type="date"
              min={todayIso()}
              max={maxDateIso()}
              value={date}
              onChange={(e) => {
                setDate(e.target.value)
                setSlot(null)
              }}
            />
          </label>
          {consultationTypes.length > 1 && (
            <label>
              Consultation type
              <select
                value={consultationType}
                onChange={(e) => setConsultationType(e.target.value)}
              >
                {consultationTypes.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        {loading && <p className="hint">Loading available slots…</p>}
        {loadError && <p className="alert error">{loadError}</p>}

        {!loading && !loadError && (
          <>
            {dayData && dayData.working_hours?.length > 0 && (
              <p className="hint">
                Working hours:{' '}
                {dayData.working_hours
                  .map((block) => `${block.start}–${block.end}`)
                  .join(', ')}{' '}
                · {dayData.day_name}
              </p>
            )}

            {slots.length === 0 ? (
              <p className="empty">
                {dayData?.message || 'No appointment slots are configured for this day.'}
              </p>
            ) : (
              <>
                <p className="hint">
                  {dayData.available_count} of {dayData.total_slots} slots available.
                </p>
                <div className="slots">
                  {slots.map((entry) => (
                    <button
                      type="button"
                      key={entry.start}
                      className={slot === entry.start ? 'slot-button active' : 'slot-button'}
                      disabled={!entry.available}
                      title={entry.available ? undefined : slotReason(entry.reason)}
                      onClick={() => setSlot(entry.start)}
                    >
                      {entry.start}
                    </button>
                  ))}
                </div>
              </>
            )}

            {dayData?.notice && <p className="hint">{dayData.notice}</p>}
          </>
        )}

        {submitError && <p className="alert error">{submitError}</p>}

        <div className="row">
          <button
            type="button"
            className="primary"
            onClick={handleConfirm}
            disabled={submitting || !slot}
          >
            {submitting
              ? 'Saving…'
              : isReschedule
                ? 'Confirm reschedule'
                : 'Confirm appointment'}
          </button>
          <button type="button" className="secondary" onClick={closeAll} disabled={submitting}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
