import { useEffect, useRef, useState } from 'react'
import './NavigationGuide.css'

type Step = { selector: string; title: string; text: string }
const steps: Step[] = [
  { selector: '.brand', title: 'Welcome to ORACLIS', text: 'This is your dengue and weather intelligence workspace for South Cotabato.' },
  { selector: '.left-panel', title: 'Read the scenario', text: 'Review projected cases, critical areas, and the current visualization layer here.' },
  { selector: '.map-stage', title: 'Explore the map', text: 'Select a barangay to inspect its risk, forecast, and location details.' },
  { selector: '.layers-button', title: 'Control map layers', text: 'Show or hide risk surfaces, boundaries, labels, and alerts.' },
  { selector: '.right-panel', title: 'Inspect details', text: 'View selected-barangay metrics, rankings, and pinned locations here.' },
  { selector: '.topbar', title: 'Open other pages', text: 'Use Help, Methodology, Analytics, Weather, and notifications from this bar.' },
]

export default function NavigationGuide({ onClose }: { onClose: () => void }) {
  const [index, setIndex] = useState(0)
  const dialog = useRef<HTMLDivElement>(null)
  const step = steps[index]
  const target = document.querySelector<HTMLElement>(step.selector)

  useEffect(() => {
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    dialog.current?.focus()
  }, [index, target])

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowRight') setIndex(value => Math.min(value + 1, steps.length - 1))
      if (event.key === 'ArrowLeft') setIndex(value => Math.max(value - 1, 0))
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [onClose])

  return <div className="guide-backdrop" role="presentation">
    {target && <div className="guide-spotlight" style={{ top: target.offsetTop, left: target.offsetLeft, width: target.offsetWidth, height: target.offsetHeight }} />}
    <section className="navigation-guide" role="dialog" aria-modal="true" aria-labelledby="guide-title" tabIndex={-1} ref={dialog}>
      <header><span className="eyebrow">Navigation guide · {index + 1}/{steps.length}</span><button aria-label="Close navigation guide" onClick={onClose}>×</button></header>
      <div className="guide-progress"><i style={{ width: `${((index + 1) / steps.length) * 100}%` }} /></div>
      <h2 id="guide-title">{step.title}</h2><p>{step.text}</p>
      <footer><button className="guide-skip" onClick={onClose}>Skip guide</button><div><button disabled={index === 0} onClick={() => setIndex(value => value - 1)}>Back</button><button className="guide-next" onClick={() => index === steps.length - 1 ? onClose() : setIndex(value => value + 1)}>{index === steps.length - 1 ? 'Finish' : 'Next'}</button></div></footer>
    </section>
  </div>
}
