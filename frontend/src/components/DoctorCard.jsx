function formatRupees(value) {
  if (value === null || value === undefined || value === '') return 'Not listed'
  const number = Number(value)
  if (Number.isNaN(number)) return 'Not listed'
  return `₹${number.toLocaleString('en-IN')}`
}

function formatRating(value) {
  if (value === null || value === undefined || value === '') return 'Not rated'
  const number = Number(value)
  if (Number.isNaN(number)) return 'Not rated'
  return `${number.toFixed(1)} / 5`
}

function formatDistance(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  if (Number.isNaN(number)) return null
  return `${number.toFixed(2)} km away`
}

// The recommendation score is a ranking signal, not a clinical quality score.
function formatScore(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  if (Number.isNaN(number)) return null
  return number.toFixed(1)
}

function mapLink(doctor) {
  const { latitude, longitude } = doctor
  const query =
    latitude !== null && latitude !== undefined && longitude !== null && longitude !== undefined
      ? `${latitude},${longitude}`
      : [doctor.name, doctor.address, doctor.city].filter(Boolean).join(', ')
  if (!query) return null
  return `https://www.openstreetmap.org/search?query=${encodeURIComponent(query)}`
}

export default function DoctorCard({
  doctor,
  selectable = false,
  selected = false,
  selectionDisabled = false,
  onToggleSelect,
  onBook,
  bookingDisabled = false,
}) {
  const distance = formatDistance(doctor.distance_km)
  const link = mapLink(doctor)
  const score = formatScore(doctor.recommendation_score)
  const hasActions = selectable || Boolean(onBook)

  return (
    <article className={selected ? 'doctor-card selected' : 'doctor-card'}>
      <header className="doctor-head">
        <div>
          <h3>{doctor.name}</h3>
          <p className="doctor-specialty">{doctor.specialization || 'Specialty not listed'}</p>
        </div>
        <div className="doctor-rating" title="Rating out of 5">
          {formatRating(doctor.rating_out_of_5)}
        </div>
      </header>

      {score !== null && (
        <div className="score-row">
          <span className="score-value">{score}</span>
          <span className="score-label">
            recommendation score / 100
            <small>Accessibility &amp; price — not clinical quality.</small>
          </span>
        </div>
      )}

      <dl className="doctor-meta">
        <div>
          <dt>Experience</dt>
          <dd>
            {doctor.experience_years === null || doctor.experience_years === undefined
              ? 'Not listed'
              : `${doctor.experience_years} years`}
          </dd>
        </div>
        <div>
          <dt>Consultation fee</dt>
          <dd>{formatRupees(doctor.consultation_fee_inr)}</dd>
        </div>
        {distance && (
          <div>
            <dt>Distance</dt>
            <dd>{distance}</dd>
          </div>
        )}
      </dl>

      {(doctor.address || doctor.city || doctor.state) && (
        <p className="doctor-address">
          {[doctor.address, doctor.city, doctor.state].filter(Boolean).join(', ')}
        </p>
      )}

      {doctor.description && <p className="doctor-description">{doctor.description}</p>}

      <footer className="doctor-foot">
        {link && (
          <a href={link} target="_blank" rel="noreferrer noopener" className="map-link">
            View on map
          </a>
        )}
        <span className="doctor-id">ID: {doctor.doctor_id}</span>
      </footer>

      {hasActions && (
        <div className="doctor-actions">
          {selectable && (
            <label className="compare-toggle">
              <input
                type="checkbox"
                checked={selected}
                disabled={selectionDisabled && !selected}
                onChange={() => onToggleSelect && onToggleSelect(doctor)}
              />
              Compare
            </label>
          )}
          {onBook && (
            <button
              type="button"
              className="primary"
              disabled={bookingDisabled}
              onClick={() => onBook(doctor)}
            >
              Book appointment
            </button>
          )}
        </div>
      )}
    </article>
  )
}
