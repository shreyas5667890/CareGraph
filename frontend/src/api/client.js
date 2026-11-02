// Thin fetch wrapper around the existing CareGraph Flask API.
// The base URL is empty by default so the Vite dev proxy handles `/api`
// same-origin. Set VITE_API_BASE_URL when the frontend is hosted separately.

const RAW_BASE = (import.meta.env?.VITE_API_BASE_URL || '').trim()
export const API_BASE_URL = RAW_BASE.replace(/\/+$/, '')

export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

async function request(path, { method = 'GET', body, csrfToken } = {}) {
  const headers = {}
  const options = { method, credentials: 'include', headers }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(body)
  }
  // CSRF token is required by the admin API for state-changing requests.
  if (csrfToken) headers['X-CSRF-Token'] = csrfToken

  let response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, options)
  } catch {
    throw new ApiError(
      'Could not reach the CareGraph API. Make sure the backend is running.',
      0,
    )
  }

  const text = await response.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }

  if (!response.ok) {
    const message =
      (data && (data.error || data.message)) ||
      `Request failed with status ${response.status}.`
    throw new ApiError(message, response.status, data)
  }
  return data
}

function buildQuery(params) {
  const search = new URLSearchParams()
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

// --- public API --------------------------------------------------------------

export function analyzeSymptoms(text, { topK = 3, city = null } = {}) {
  const body = { text, top_k: topK }
  if (city) body.city = city
  return request('/api/analyze', { method: 'POST', body })
}

export function fetchSpecialties() {
  return request('/api/specialties')
}

export function fetchDoctors(params) {
  return request(`/api/doctors${buildQuery(params)}`)
}

// --- ranking / comparison / availability -------------------------------------

export function fetchRankedDoctors(params) {
  return request(`/api/ranked-doctors${buildQuery(params)}`)
}

export function compareDoctors(doctorIds, { specialty, latitude, longitude } = {}) {
  const body = { doctor_ids: doctorIds }
  if (specialty) body.specialty = specialty
  if (latitude !== undefined && latitude !== null && latitude !== '') {
    body.user_latitude = latitude
  }
  if (longitude !== undefined && longitude !== null && longitude !== '') {
    body.user_longitude = longitude
  }
  return request('/api/compare-doctors', { method: 'POST', body })
}

export function fetchDoctorDetail(doctorId, params) {
  return request(`/api/doctors/${encodeURIComponent(doctorId)}${buildQuery(params)}`)
}

export function fetchDoctorSlots(doctorId, date) {
  return request(`/api/doctors/${encodeURIComponent(doctorId)}/slots${buildQuery({ date })}`)
}

// --- patient API -------------------------------------------------------------

// A signed-out patient session. Kept in one place so every caller agrees.
export const EMPTY_PATIENT_SESSION = Object.freeze({
  authenticated: false,
  patientId: null,
  username: null,
  csrfToken: null,
})

// Normalise the login/me payloads into the shape the SPA stores in state.
export function patientSessionFrom(data) {
  if (!data || !data.authenticated) return { ...EMPTY_PATIENT_SESSION }
  return {
    authenticated: true,
    patientId: data.patient?.patient_id ?? null,
    username: data.patient?.username ?? null,
    csrfToken: data.csrf_token ?? null,
  }
}

export function patientRegister(payload) {
  return request('/api/patient/register', { method: 'POST', body: payload })
}

export function patientLogin(username, password) {
  return request('/api/patient/login', { method: 'POST', body: { username, password } })
}

export function patientLogout(csrfToken) {
  return request('/api/patient/logout', { method: 'POST', csrfToken })
}

export function patientMe() {
  return request('/api/patient/me')
}

export function fetchMyAppointments() {
  return request('/api/patient/appointments')
}

export function createAppointment(payload, csrfToken) {
  return request('/api/patient/appointments', { method: 'POST', body: payload, csrfToken })
}

export function cancelAppointment(appointmentId, csrfToken) {
  return request(`/api/patient/appointments/${encodeURIComponent(appointmentId)}/cancel`, {
    method: 'POST',
    csrfToken,
  })
}

export function rescheduleAppointment(appointmentId, payload, csrfToken) {
  return request(`/api/patient/appointments/${encodeURIComponent(appointmentId)}/reschedule`, {
    method: 'POST',
    body: payload,
    csrfToken,
  })
}

// --- admin API ---------------------------------------------------------------

export function adminLogin(username, password) {
  return request('/api/admin/login', { method: 'POST', body: { username, password } })
}

export function adminLogout(csrfToken) {
  return request('/api/admin/logout', { method: 'POST', csrfToken })
}

export function adminMe() {
  return request('/api/admin/me')
}

export function adminListDoctors(params, csrfToken) {
  return request(`/api/admin/doctors${buildQuery(params)}`, { csrfToken })
}

export function adminCreateDoctor(doctor, csrfToken) {
  return request('/api/admin/doctors', { method: 'POST', body: doctor, csrfToken })
}

export function adminUpdateDoctor(doctorId, doctor, csrfToken) {
  return request(`/api/admin/doctors/${encodeURIComponent(doctorId)}`, {
    method: 'PUT',
    body: doctor,
    csrfToken,
  })
}

export function adminDeleteDoctor(doctorId, csrfToken) {
  return request(`/api/admin/doctors/${encodeURIComponent(doctorId)}`, {
    method: 'DELETE',
    csrfToken,
  })
}
