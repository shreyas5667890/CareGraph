const MISSING = 'Not listed'

function isBlank(value) {
  return value === null || value === undefined || value === ''
}

function text(value) {
  return isBlank(value) ? MISSING : String(value)
}

function rupees(value) {
  if (isBlank(value)) return MISSING
  const number = Number(value)
  if (Number.isNaN(number)) return MISSING
  return `₹${number.toLocaleString('en-IN')}`
}

function rating(value) {
  if (isBlank(value)) return MISSING
  const number = Number(value)
  if (Number.isNaN(number)) return MISSING
  return `${number.toFixed(1)} / 5`
}

function years(value) {
  if (isBlank(value)) return MISSING
  const number = Number(value)
  if (Number.isNaN(number)) return MISSING
  return `${number} years`
}

function distance(value) {
  if (isBlank(value)) return MISSING
  const number = Number(value)
  if (Number.isNaN(number)) return MISSING
  return `${number.toFixed(2)} km`
}

function score(value) {
  if (isBlank(value)) return MISSING
  const number = Number(value)
  if (Number.isNaN(number)) return MISSING
  return `${number.toFixed(1)} / 100`
}

const ROWS = [
  { label: 'Specialty', render: (d) => text(d.specialization) },
  { label: 'Recommendation score', render: (d) => score(d.recommendation_score) },
  { label: 'Rating', render: (d) => rating(d.rating_out_of_5) },
  { label: 'Experience', render: (d) => years(d.experience_years) },
  { label: 'Consultation fee', render: (d) => rupees(d.consultation_fee_inr) },
  { label: 'Distance', render: (d) => distance(d.distance_km) },
  { label: 'City', render: (d) => text(d.city) },
  { label: 'Address', render: (d) => text([d.address, d.state].filter(Boolean).join(', ')) },
]

export default function ComparePanel({ doctors, specialty, disclaimer, onRemove, onBook, onClose }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal compare-modal">
        <div className="admin-head">
          <h3>Compare doctors{specialty ? ` · ${specialty}` : ''}</h3>
          <button type="button" className="secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <p className="hint">
          The same records and scores used by the ranking list. Missing values are shown as “Not
          listed”.
        </p>
        {disclaimer && <p className="hint">{disclaimer}</p>}

        <div className="table-wrap">
          <table className="table compare-table">
            <thead>
              <tr>
                <th />
                {doctors.map((doctor) => (
                  <th key={doctor.doctor_id}>
                    <div className="compare-head">
                      <strong>{doctor.name}</strong>
                      <span className="doctor-id">ID: {doctor.doctor_id}</span>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => (
                <tr key={row.label}>
                  <th scope="row">{row.label}</th>
                  {doctors.map((doctor) => (
                    <td key={doctor.doctor_id}>{row.render(doctor)}</td>
                  ))}
                </tr>
              ))}
              <tr>
                <th scope="row">Actions</th>
                {doctors.map((doctor) => (
                  <td key={doctor.doctor_id}>
                    <div className="compare-actions">
                      {onBook && (
                        <button type="button" className="primary" onClick={() => onBook(doctor)}>
                          Book
                        </button>
                      )}
                      <button
                        type="button"
                        className="link danger"
                        onClick={() => onRemove(doctor.doctor_id)}
                      >
                        Remove
                      </button>
                    </div>
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
