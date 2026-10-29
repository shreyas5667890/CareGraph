const DEMO_NOTICE =
  'Synthetic demo record — fictional doctor and clinic with approximate coordinates.'

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

function mapLink(doctor) {
  const { latitude, longitude } = doctor
  const query =
    latitude !== null && latitude !== undefined && longitude !== null && longitude !== undefined
      ? `${latitude},${longitude}`
      : [doctor.name, doctor.address, doctor.city].filter(Boolean).join(', ')
  if (!query) return null
  return `https://www.openstreetmap.org/search?query=${encodeURIComponent(query)}`
}

export default function DoctorCard({ doctor }) {
  const distance = formatDistance(doctor.distance_km)
  const link = mapLink(doctor)
  const isDemo = (doctor.data_status || '').toLowerCase().includes('synthetic')

  return (
    <article className="doctor-card">
      <header className="doctor-head">
        <div>
          <h3>{doctor.name}</h3>
          <p className="doctor-specialty">{doctor.specialization}</p>
        </div>
        <div className="doctor-rating" title="Rating out of 5">
          {formatRating(doctor.rating_out_of_5)}
        </div>
      </header>

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

      {isDemo && <p className="demo-notice">{DEMO_NOTICE}</p>}
    </article>
  )
}
