import { useMemo, useState, type FormEvent } from 'react'
import './HelpSupportPage.css'

const guides = [
  ['Dashboard', 'Review current indicators, map status, priority rankings, and selected-barangay details.'],
  ['Map', 'Search for a barangay, choose map layers, pin locations, compare scenarios, or export the visible map.'],
  ['Forecasts', 'Treat projections as decision support. Check forecast date, uncertainty range, and model context before acting.'],
  ['Alerts', 'Open notifications to review elevated areas. Confirm conditions through local surveillance and official advisories.'],
  ['Patient registry', 'Authorized barangay personnel can add, search, edit, and update private patient records. Patient identities never appear on public maps.'],
]

export default function HelpSupportPage() {
  const [sent, setSent] = useState(false)
  const [query, setQuery] = useState('')
  const visibleGuides = useMemo(() => guides.filter(([title, text]) => `${title} ${text}`.toLowerCase().includes(query.toLowerCase())), [query])
  const submit = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setSent(true); event.currentTarget.reset() }
  return <main className="help-page">
    <header className="help-topbar"><a href="#/">← Back to ORACLIS</a><strong>Help &amp; Support</strong><span>Decision support center</span></header>
    <section className="help-hero"><span>ORACLIS user assistance</span><h1>Operate with confidence.</h1><p>Quick guidance for maps, forecasts, alerts, and restricted barangay reporting.</p></section>
    <section className="help-content">
      <section aria-labelledby="guide-title"><div className="help-heading"><span>01 / User guide</span><h2 id="guide-title">Feature guidance</h2><p>Find what you need, then follow one clear next step.</p></div><label className="help-search"><span>Search guides</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Try “map” or “patient”" /></label><div className="help-guides">{visibleGuides.length ? visibleGuides.map(([title, text], index) => <details key={title} open={index === 0}><summary>{title}<b>+</b></summary><p>{text}</p></details>) : <p className="help-empty">No guide matches. Try another keyword.</p>}</div></section>
      <section aria-labelledby="problem-title"><div className="help-heading"><span>02 / Technical support</span><h2 id="problem-title">Report a problem</h2><p>Do not include patient names, contact numbers, screenshots containing patient details, or other sensitive health information.</p></div>{sent ? <div className="help-confirmation" role="status"><strong>Report prepared</strong><p>Reference: ORA-{Date.now().toString().slice(-6)}</p><p>Share this reference with your designated ORACLIS administrator. No sensitive patient data was collected.</p><button onClick={() => setSent(false)}>Report another problem</button></div> : <form className="help-form" onSubmit={submit}><label>Category<select required defaultValue=""><option value="" disabled>Select issue</option><option>Login or access</option><option>Map or location</option><option>Forecast or alert</option><option>Patient registry</option><option>Performance or display</option><option>Other</option></select></label><label>What happened?<textarea required minLength={20} maxLength={1500} rows={6} placeholder="Describe expected result, actual result, and steps that caused the issue." /></label><label>Optional evidence<input type="file" accept="image/png,image/jpeg,.txt,.log" /><small>Remove patient identifiers before attaching evidence.</small></label><button type="submit">Prepare support report</button></form>}</section>
      <aside className="help-contact" aria-labelledby="contact-title"><span>03 / Administrator</span><h2 id="contact-title">Need direct assistance?</h2><p>Contact your designated ORACLIS system administrator or provincial health-office focal person using your organization’s approved internal channel.</p><div><strong>Before contacting support</strong><ul><li>Note page and action that failed.</li><li>Record visible error message and time.</li><li>Never send passwords or patient identifiers.</li></ul></div><p className="help-note">Emergency or medical concern? Contact qualified health professionals or local emergency services. ORACLIS does not diagnose illness.</p></aside>
    </section>
  </main>
}
