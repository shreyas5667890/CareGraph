import { useCallback, useEffect, useState } from 'react'
import {
  adminCreateDoctor,
  adminDeleteDoctor,
  adminListDoctors,
  adminUpdateDoctor,
} from '../api/client.js'

const EMPTY_FORM = {
  Doctor_ID: '',
  Doctor_Name: '',
  Specialization: '',
  Age: '',
  Experience_Years: '',
  Consultation_Fee_INR: '',
  Rating_Out_of_5: '',
  Latitude: '',
  Longitude: '',
  Address: '',
  City: '',
  State: '',
  Description: '',
  Data_Status: '',
}

const NUMBER_FIELDS = new Set([
  'Age',
  'Experience_Years',
  'Consultation_Fee_INR',
  'Rating_Out_of_5',
  'Latitude',
  'Longitude',
])

// Build the JSON payload, converting numeric fields and omitting empty optional values.
function toPayload(form) {
  const payload = {}
  Object.entries(form).forEach(([key, raw]) => {
    const value = typeof raw === 'string' ? raw.trim() : raw
    if (value === '') return
    payload[key] = NUMBER_FIELDS.has(key) ? Number(value) : value
  })
  return payload
}

export default function AdminDoctors({ admin, onLogout }) {
  const [query, setQuery] = useState('')
  const [specialtyFilter, setSpecialtyFilter] = useState('')
  const [cityFilter, setCityFilter] = useState('')
  const [specialties, setSpecialties] = useState([])
  const [cities, setCities] = useState([])

  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [loading, setLoading] = useState(false)
  const [listError, setListError] = useState(null)

  const [editing, setEditing] = useState(null) // { mode: 'create' | 'edit', form }
  const [formErrors, setFormErrors] = useState([])
  const [saving, setSaving] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [banner, setBanner] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setListError(null)
    try {
      const data = await adminListDoctors(
        {
          q: query || undefined,
          specialization: specialtyFilter || undefined,
          city: cityFilter || undefined,
          page,
          page_size: pageSize,
        },
        admin.csrfToken,
      )
      setRows(data.doctors || [])
      setTotal(data.total ?? 0)
      if (data.specialties) setSpecialties(data.specialties)
      if (data.cities) setCities(data.cities)
    } catch (err) {
      if (err.status === 401) {
        onLogout()
        return
      }
      setListError(err.message || 'Could not load doctors.')
    } finally {
      setLoading(false)
    }
  }, [admin.csrfToken, query, specialtyFilter, cityFilter, page, pageSize, onLogout])

  useEffect(() => {
    load()
  }, [load])

  function startCreate() {
    setFormErrors([])
    setEditing({ mode: 'create', form: { ...EMPTY_FORM } })
  }

  function startEdit(doctor) {
    setFormErrors([])
    setEditing({
      mode: 'edit',
      originalId: doctor.doctor_id,
      form: {
        Doctor_ID: doctor.doctor_id ?? '',
        Doctor_Name: doctor.name ?? '',
        Specialization: doctor.specialization ?? '',
        Age: doctor.age ?? '',
        Experience_Years: doctor.experience_years ?? '',
        Consultation_Fee_INR: doctor.consultation_fee_inr ?? '',
        Rating_Out_of_5: doctor.rating_out_of_5 ?? '',
        Latitude: doctor.latitude ?? '',
        Longitude: doctor.longitude ?? '',
        Address: doctor.address ?? '',
        City: doctor.city ?? '',
        State: doctor.state ?? '',
        Description: doctor.description ?? '',
        Data_Status: doctor.data_status ?? '',
      },
    })
  }

  function updateField(key, value) {
    setEditing((prev) => (prev ? { ...prev, form: { ...prev.form, [key]: value } } : prev))
  }

  async function handleSave(event) {
    event.preventDefault()
    setFormErrors([])
    setSaving(true)
    const payload = toPayload(editing.form)
    try {
      if (editing.mode === 'create') {
        await adminCreateDoctor(payload, admin.csrfToken)
        setBanner('Doctor added.')
      } else {
        await adminUpdateDoctor(editing.originalId, payload, admin.csrfToken)
        setBanner('Doctor updated.')
      }
      setEditing(null)
      load()
    } catch (err) {
      if (err.status === 401) {
        onLogout()
        return
      }
      const details = err.payload?.errors
      setFormErrors(
        Array.isArray(details) && details.length ? details : [err.message || 'Could not save.'],
      )
    } finally {
      setSaving(false)
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await adminDeleteDoctor(deleteTarget.doctor_id, admin.csrfToken)
      setBanner(`Deleted ${deleteTarget.name} (${deleteTarget.doctor_id}).`)
      setDeleteTarget(null)
      load()
    } catch (err) {
      if (err.status === 401) {
        onLogout()
        return
      }
      setListError(err.message || 'Could not delete doctor.')
      setDeleteTarget(null)
    } finally {
      setDeleting(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <section className="panel">
      <div className="admin-head">
        <h2>Manage doctors</h2>
        <button type="button" className="primary" onClick={startCreate}>
          Add doctor
        </button>
      </div>

      {banner && <p className="alert ok">{banner}</p>}
      {listError && <p className="alert error">{listError}</p>}

      <div className="filters compact">
        <label>
          Search
          <input
            type="search"
            value={query}
            placeholder="Name, ID, city or specialty"
            onChange={(e) => {
              setQuery(e.target.value)
              setPage(1)
            }}
          />
        </label>
        <label>
          Specialty
          <select
            value={specialtyFilter}
            onChange={(e) => {
              setSpecialtyFilter(e.target.value)
              setPage(1)
            }}
          >
            <option value="">All specialties</option>
            {specialties.map((specialty) => (
              <option key={specialty} value={specialty}>
                {specialty}
              </option>
            ))}
          </select>
        </label>
        <label>
          City
          <select
            value={cityFilter}
            onChange={(e) => {
              setCityFilter(e.target.value)
              setPage(1)
            }}
          >
            <option value="">All cities</option>
            {cities.map((city) => (
              <option key={city} value={city}>
                {city}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading ? (
        <p className="hint">Loading doctors…</p>
      ) : rows.length === 0 ? (
        <p className="empty">No doctors match the current search.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Specialty</th>
                <th>City</th>
                <th>Fee</th>
                <th>Rating</th>
                <th>Exp.</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((doctor) => (
                <tr key={doctor.doctor_id}>
                  <td>{doctor.doctor_id}</td>
                  <td>{doctor.name}</td>
                  <td>{doctor.specialization}</td>
                  <td>{doctor.city}</td>
                  <td>
                    {doctor.consultation_fee_inr === null || doctor.consultation_fee_inr === undefined
                      ? '—'
                      : `₹${Number(doctor.consultation_fee_inr).toLocaleString('en-IN')}`}
                  </td>
                  <td>
                    {doctor.rating_out_of_5 === null || doctor.rating_out_of_5 === undefined
                      ? '—'
                      : Number(doctor.rating_out_of_5).toFixed(1)}
                  </td>
                  <td>{doctor.experience_years ?? '—'}</td>
                  <td className="table-actions">
                    <button type="button" className="link" onClick={() => startEdit(doctor)}>
                      Edit
                    </button>
                    <button
                      type="button"
                      className="link danger"
                      onClick={() => setDeleteTarget(doctor)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="hint">
        {total} record{total === 1 ? '' : 's'} · page {page} of {totalPages}
      </p>
      {totalPages > 1 && (
        <div className="row pager">
          <button
            type="button"
            className="secondary"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Previous
          </button>
          <button
            type="button"
            className="secondary"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            Next
          </button>
        </div>
      )}

      {editing && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>{editing.mode === 'create' ? 'Add doctor' : `Edit ${editing.originalId}`}</h3>
            <form onSubmit={handleSave} className="stack">
              <div className="form-grid">
                {Object.keys(EMPTY_FORM).map((field) => (
                  <label key={field}>
                    {field.replace(/_/g, ' ')}
                    {field === 'Description' || field === 'Address' || field === 'Data_Status' ? (
                      <textarea
                        rows={2}
                        value={editing.form[field]}
                        onChange={(e) => updateField(field, e.target.value)}
                      />
                    ) : (
                      <input
                        type={NUMBER_FIELDS.has(field) ? 'number' : 'text'}
                        step={NUMBER_FIELDS.has(field) ? 'any' : undefined}
                        value={editing.form[field]}
                        onChange={(e) => updateField(field, e.target.value)}
                      />
                    )}
                  </label>
                ))}
              </div>

              {formErrors.length > 0 && (
                <div className="alert error">
                  <ul>
                    {formErrors.map((message) => (
                      <li key={message}>{message}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="row">
                <button type="submit" className="primary" disabled={saving}>
                  {saving ? 'Saving…' : 'Save'}
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setEditing(null)}
                  disabled={saving}
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal narrow">
            <h3>Delete doctor?</h3>
            <p>
              This permanently removes <strong>{deleteTarget.name}</strong> (ID:{' '}
              {deleteTarget.doctor_id}) from the directory.
            </p>
            <div className="row">
              <button
                type="button"
                className="danger-solid"
                onClick={confirmDelete}
                disabled={deleting}
              >
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
