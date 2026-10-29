import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import DoctorCard from '../components/DoctorCard.jsx'
import { analyzeSymptoms, fetchDoctors, fetchSpecialties } from '../api/client.js'

const SORT_OPTIONS = [
  { value: 'recommended', label: 'Recommended' },
  { value: 'rating', label: 'Highest rating' },
  { value: 'fees', label: 'Lowest consultation fee' },
  { value: 'distance', label: 'Nearest doctor' },
  { value: 'experience', label: 'Most experienced' },
]

const EMPTY_FILTERS = {
  latitude: '',
  longitude: '',
  maxDistanceKm: '',
  maxFee: '',
  minRating: '',
  sort: 'recommended',
}

const EMPTY_RESULT = { doctors: [], total: 0, page: 1, pageSize: 20 }

export default function DoctorRecommendation() {
  const [symptomText, setSymptomText] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeError, setAnalyzeError] = useState(null)
  const [analysis, setAnalysis] = useState(null)
  const [recommendation, setRecommendation] = useState(null)

  const [specialties, setSpecialties] = useState([])
  const [cities, setCities] = useState([])
  const [selectedSpecialty, setSelectedSpecialty] = useState('')
  const [selectedCity, setSelectedCity] = useState('')

  const [form, setForm] = useState(EMPTY_FILTERS)
  const [applied, setApplied] = useState(EMPTY_FILTERS)
  const [locationStatus, setLocationStatus] = useState(null)

  const [result, setResult] = useState(EMPTY_RESULT)
  const [message, setMessage] = useState(null)
  const [loadingDoctors, setLoadingDoctors] = useState(false)
  const [doctorsError, setDoctorsError] = useState(null)
  const [page, setPage] = useState(1)
  const [filterError, setFilterError] = useState(null)

  const requestId = useRef(0)

  // Load the specialty list from the directory once.
  useEffect(() => {
    fetchSpecialties()
      .then((data) => {
        setSpecialties(data.specialties || [])
        setCities(data.cities || [])
      })
      .catch(() => {
        /* the directory explorer simply stays empty */
      })
  }, [])

  const loadDoctors = useCallback(async () => {
    if (!selectedSpecialty) {
      setResult(EMPTY_RESULT)
      setMessage(null)
      setDoctorsError(null)
      return
    }
    const current = ++requestId.current
    setLoadingDoctors(true)
    setDoctorsError(null)
    try {
      const data = await fetchDoctors({
        specialty: selectedSpecialty,
        city: selectedCity || undefined,
        user_latitude: applied.latitude || undefined,
        user_longitude: applied.longitude || undefined,
        max_distance_km: applied.maxDistanceKm || undefined,
        max_fee: applied.maxFee || undefined,
        min_rating: applied.minRating || undefined,
        sort: applied.sort || 'recommended',
        page,
        page_size: 20,
      })
      if (current !== requestId.current) return
      setResult({
        doctors: data.doctors || [],
        total: data.total ?? (data.doctors || []).length,
        page: data.page ?? page,
        pageSize: data.page_size ?? 20,
      })
      setMessage(data.message || null)
    } catch (err) {
      if (current !== requestId.current) return
      setResult(EMPTY_RESULT)
      setMessage(null)
      setDoctorsError(err.message || 'Could not load doctors.')
    } finally {
      if (current === requestId.current) setLoadingDoctors(false)
    }
  }, [selectedSpecialty, selectedCity, applied, page])

  useEffect(() => {
    loadDoctors()
  }, [loadDoctors])

  async function handleAnalyze(event) {
    event.preventDefault()
    const text = symptomText.trim()
    if (!text) {
      setAnalyzeError('Describe your symptoms before analyzing.')
      return
    }
    setAnalyzing(true)
    setAnalyzeError(null)
    try {
      const data = await analyzeSymptoms(text)
      setAnalysis(data.symptom_analysis || null)
      setRecommendation(data.recommendation || null)
      const candidates = data.recommendation?.ranked_candidates || []
      const suggested = candidates.find((c) => c.possible_specialty)?.possible_specialty
      if (suggested) {
        setSelectedSpecialty(suggested)
        setPage(1)
      }
    } catch (err) {
      setAnalyzeError(err.message || 'Could not analyze this input.')
      setAnalysis(null)
      setRecommendation(null)
    } finally {
      setAnalyzing(false)
    }
  }

  function updateForm(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function handleUseMyLocation() {
    if (!navigator.geolocation) {
      setLocationStatus({ type: 'error', text: 'This browser does not support geolocation.' })
      return
    }
    setLocationStatus({ type: 'pending', text: 'Requesting your location…' })
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setForm((prev) => ({
          ...prev,
          latitude: String(Number(position.coords.latitude.toFixed(6))),
          longitude: String(Number(position.coords.longitude.toFixed(6))),
        }))
        setLocationStatus({
          type: 'ok',
          text: 'Location captured. Press Apply to use it.',
        })
      },
      (error) => {
        setLocationStatus({
          type: 'error',
          text:
            error.code === error.PERMISSION_DENIED
              ? 'Location permission denied. Enter coordinates manually.'
              : 'Could not determine your location. Enter coordinates manually.',
        })
      },
      { enableHighAccuracy: false, timeout: 10000 },
    )
  }

  function handleApply() {
    const errors = []
    const lat = form.latitude.trim()
    const lon = form.longitude.trim()
    if ((lat && !lon) || (!lat && lon)) {
      errors.push('Latitude and longitude must be supplied together.')
    }
    if (lat && (Number(lat) < -90 || Number(lat) > 90 || Number.isNaN(Number(lat)))) {
      errors.push('Latitude must be a number between -90 and 90.')
    }
    if (lon && (Number(lon) < -180 || Number(lon) > 180 || Number.isNaN(Number(lon)))) {
      errors.push('Longitude must be a number between -180 and 180.')
    }
    const fee = form.maxFee.trim()
    if (fee && (Number(fee) < 0 || Number.isNaN(Number(fee)))) {
      errors.push('Maximum fee must be zero or greater.')
    }
    const rating = form.minRating.trim()
    if (rating && (Number(rating) < 0 || Number(rating) > 5 || Number.isNaN(Number(rating)))) {
      errors.push('Minimum rating must be between 0 and 5.')
    }
    const distance = form.maxDistanceKm.trim()
    if (distance && Number(distance) < 0) {
      errors.push('Maximum distance must be zero or greater.')
    }
    if (distance && !lat) {
      errors.push('Maximum distance requires your coordinates.')
    }
    if (form.sort === 'distance' && !lat) {
      errors.push('Sorting by nearest doctor requires your coordinates.')
    }
    setFilterError(errors.length ? errors.join(' ') : null)
    if (errors.length) return
    setPage(1)
    setApplied({ ...form })
  }

  function handleClear() {
    setForm(EMPTY_FILTERS)
    setApplied(EMPTY_FILTERS)
    setFilterError(null)
    setLocationStatus(null)
    setPage(1)
  }

  const suggestedSpecialties = useMemo(() => {
    const candidates = recommendation?.ranked_candidates || []
    const seen = new Set()
    const list = []
    candidates.forEach((candidate) => {
      const specialty = candidate.possible_specialty
      if (!specialty || seen.has(specialty.toLowerCase())) return
      seen.add(specialty.toLowerCase())
      list.push(specialty)
    })
    return list
  }, [recommendation])

  const confirmedSymptoms = useMemo(
    () => (analysis?.confirmed_symptoms || []).map((s) => s.normalized_symptom).filter(Boolean),
    [analysis],
  )

  const totalPages = Math.max(1, Math.ceil((result.total || 0) / (result.pageSize || 20)))
  const groups = recommendation?.doctor_recommendations || []

  return (
    <div className="stack">
      <section className="panel">
        <h2>1. Describe your symptoms</h2>
        <p className="hint">
          Free text is matched to known symptoms by the existing extraction pipeline, then the
          model suggests possible conditions and a matching specialty.
        </p>
        <form onSubmit={handleAnalyze} className="stack">
          <textarea
            className="symptom-input"
            rows={4}
            maxLength={5000}
            placeholder="e.g. I have had a headache and mild fever since yesterday, and I feel dizzy."
            value={symptomText}
            onChange={(e) => setSymptomText(e.target.value)}
          />
          <div className="row">
            <button type="submit" className="primary" disabled={analyzing}>
              {analyzing ? 'Analyzing…' : 'Analyze symptoms'}
            </button>
            <span className="hint">{symptomText.length}/5000</span>
          </div>
        </form>

        {analyzeError && <p className="alert error">{analyzeError}</p>}

        {analysis && (
          <div className="analysis">
            <h3>Detected symptoms</h3>
            {confirmedSymptoms.length ? (
              <ul className="chips">
                {confirmedSymptoms.map((symptom) => (
                  <li key={symptom} className="chip">
                    {symptom.replace(/_/g, ' ')}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="hint">No confirmed symptoms were identified in that text.</p>
            )}

            {recommendation?.ranked_candidates?.length > 0 && (
              <>
                <h3>Possible conditions (educational only)</h3>
                <ul className="candidates">
                  {recommendation.ranked_candidates.map((candidate) => (
                    <li key={candidate.disease_candidate}>
                      <span>{candidate.disease_candidate.replace(/_/g, ' ')}</span>
                      <span className="candidate-side">
                        {candidate.possible_specialty || 'no specialty mapped'}
                        {candidate.model_score !== null && candidate.model_score !== undefined
                          ? ` · ${Number(candidate.model_score).toFixed(3)}`
                          : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}

            {suggestedSpecialties.length > 0 && (
              <>
                <h3>Suggested specialties</h3>
                <div className="row wrap">
                  {suggestedSpecialties.map((specialty) => (
                    <button
                      type="button"
                      key={specialty}
                      className={
                        selectedSpecialty === specialty ? 'chip-button active' : 'chip-button'
                      }
                      onClick={() => {
                        setSelectedSpecialty(specialty)
                        setPage(1)
                      }}
                    >
                      {specialty}
                    </button>
                  ))}
                </div>
              </>
            )}

            {recommendation?.safety_note && (
              <p className="alert warn">{recommendation.safety_note}</p>
            )}
          </div>
        )}
      </section>

      {groups.length > 0 && (
        <section className="panel">
          <h2>Suggested doctors by specialty</h2>
          {groups.map((group) => (
            <div key={`${group.specialty}-${group.disease_candidate}`} className="group">
              <h3>
                {group.doctors?.[0]?.specialization || group.specialty}
                <span className="group-sub"> · {group.disease_candidate?.replace(/_/g, ' ')}</span>
              </h3>
              {group.doctors?.length ? (
                <div className="cards">
                  {group.doctors.map((doctor) => (
                    <DoctorCard key={doctor.doctor_id} doctor={doctor} />
                  ))}
                </div>
              ) : (
                <p className="hint">
                  No doctors are currently available for this specialty with the selected filters.
                </p>
              )}
            </div>
          ))}
        </section>
      )}

      <section className="panel">
        <h2>2. Browse the doctor directory</h2>
        <div className="filters">
          <label>
            Specialty
            <select
              value={selectedSpecialty}
              onChange={(e) => {
                setSelectedSpecialty(e.target.value)
                setPage(1)
              }}
            >
              <option value="">Select a specialty…</option>
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
              value={selectedCity}
              onChange={(e) => {
                setSelectedCity(e.target.value)
                setPage(1)
              }}
            >
              <option value="">Any city</option>
              {cities.map((city) => (
                <option key={city} value={city}>
                  {city}
                </option>
              ))}
            </select>
          </label>

          <label>
            Sort by
            <select value={form.sort} onChange={(e) => updateForm('sort', e.target.value)}>
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label>
            Max fee (₹)
            <input
              type="number"
              min="0"
              step="50"
              value={form.maxFee}
              onChange={(e) => updateForm('maxFee', e.target.value)}
              placeholder="e.g. 1500"
            />
          </label>

          <label>
            Min rating (0–5)
            <input
              type="number"
              min="0"
              max="5"
              step="0.1"
              value={form.minRating}
              onChange={(e) => updateForm('minRating', e.target.value)}
              placeholder="e.g. 4"
            />
          </label>

          <label>
            Max distance (km)
            <input
              type="number"
              min="0"
              step="1"
              value={form.maxDistanceKm}
              onChange={(e) => updateForm('maxDistanceKm', e.target.value)}
              placeholder="e.g. 25"
            />
          </label>

          <label>
            Your latitude
            <input
              type="number"
              step="any"
              value={form.latitude}
              onChange={(e) => updateForm('latitude', e.target.value)}
              placeholder="e.g. 19.2183"
            />
          </label>

          <label>
            Your longitude
            <input
              type="number"
              step="any"
              value={form.longitude}
              onChange={(e) => updateForm('longitude', e.target.value)}
              placeholder="e.g. 72.9781"
            />
          </label>

          <div className="filter-actions">
            <button type="button" className="secondary" onClick={handleUseMyLocation}>
              Use my location
            </button>
            <button type="button" className="primary" onClick={handleApply}>
              Apply filters
            </button>
            <button type="button" className="secondary" onClick={handleClear}>
              Clear
            </button>
          </div>
        </div>

        {locationStatus && (
          <p className={locationStatus.type === 'error' ? 'alert error' : 'hint'}>
            {locationStatus.text}
          </p>
        )}
        {filterError && <p className="alert error">{filterError}</p>}

        {!selectedSpecialty && (
          <p className="empty">
            Select a specialty to see matching doctors. Distances are only shown when you provide
            your coordinates.
          </p>
        )}

        {selectedSpecialty && loadingDoctors && <p className="hint">Loading doctors…</p>}

        {selectedSpecialty && doctorsError && <p className="alert error">{doctorsError}</p>}

        {selectedSpecialty && !loadingDoctors && !doctorsError && (
          <>
            <p className="hint">
              {result.total} doctor{result.total === 1 ? '' : 's'} found for{' '}
              <strong>{selectedSpecialty}</strong>.
            </p>
            {result.doctors.length === 0 ? (
              <p className="empty">
                {message ||
                  'No doctors are currently available for this specialty with the selected filters.'}
              </p>
            ) : (
              <div className="cards">
                {result.doctors.map((doctor) => (
                  <DoctorCard key={doctor.doctor_id} doctor={doctor} />
                ))}
              </div>
            )}

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
                <span className="hint">
                  Page {result.page} of {totalPages}
                </span>
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
          </>
        )}
      </section>
    </div>
  )
}
