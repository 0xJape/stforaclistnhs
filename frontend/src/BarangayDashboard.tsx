import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Map as MapLibreMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import ReportingWorkspace from './ReportingWorkspace'
import './BarangayDashboard.css'

type User = { psgc: string; barangay_name: string; username: string }
type PatientSummary = { reported_date: string; case_status: string }

export default function BarangayDashboard({ user, onExit, onLogout }: { user: User; onExit: () => void; onLogout: () => void }) {
  const mapElement = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)
  const [patients, setPatients] = useState<PatientSummary[]>([])

  useEffect(() => {
    if (!mapElement.current || map.current) return
    const instance = new maplibregl.Map({ container: mapElement.current, center: [124.85, 6.25], zoom: 9, style: { version: 8, sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' } }, layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-opacity': .55 } }] } })
    map.current = instance
    instance.addControl(new maplibregl.NavigationControl(), 'bottom-right')
    instance.on('load', async () => {
      try {
        const geo = await fetch('/maps/south_cotabato_barangays_2023.geojson').then(read)
        const feature = geo.features.find((item: any) => String(item.properties.psgc ?? item.properties.ORACLIS_PSGC ?? item.properties.adm4_psgc) === user.psgc)
        if (!feature) return
        instance.addSource('assigned-barangay', { type: 'geojson', data: { type: 'FeatureCollection', features: [feature] } })
        instance.addLayer({ id: 'assigned-fill', type: 'fill', source: 'assigned-barangay', paint: { 'fill-color': '#e5484d', 'fill-opacity': .34 } })
        instance.addLayer({ id: 'assigned-line', type: 'line', source: 'assigned-barangay', paint: { 'line-color': '#fff', 'line-width': 3 } })
        const bounds = new maplibregl.LngLatBounds()
        const visit = (value: any): void => { if (Array.isArray(value?.[0])) value.forEach(visit); else bounds.extend(value) }
        visit(feature.geometry.coordinates)
        instance.fitBounds(bounds, { padding: 48, maxZoom: 13 })
      } catch { /* Base map remains usable. */ }
    })
    return () => { instance.remove(); map.current = null }
  }, [user.psgc])

  const activeCases = patients.filter(patient => patient.case_status === 'active').length
  const latestReport = patients.reduce((latest, patient) => patient.reported_date > latest ? patient.reported_date : latest, '')
  return <main className="barangay-dashboard">
    <header className="bd-topbar"><button className="bd-brand" onClick={onExit}>ORACLIS <span>Barangay Operations</span></button><div className="bd-account"><a href="#/help">Help</a><span>{user.barangay_name}</span><small>{user.psgc}</small><button onClick={onLogout}>Log out</button></div></header>
    <section className="bd-hero"><div><span className="bd-eyebrow">Assigned reporting area</span><h1>{user.barangay_name}</h1><p>Private dengue case workspace for authorized barangay personnel.</p></div><div className="bd-assignment"><span>Reporting account</span><strong>{user.username}</strong><small>PSGC {user.psgc}</small></div></section>
    <section className="bd-kpis"><article><span>Registered patients</span><strong>{patients.length}</strong><small>{latestReport || 'No records yet'}</small></article><article><span>Active cases</span><strong>{activeCases}</strong><small>Current patient records</small></article><article><span>Patient registry</span><strong>{patients.length ? 'Updated' : 'Ready'}</strong><small>Authorized personnel only</small></article></section>
    <section className="bd-map-card bd-map-wide"><header><div><span className="bd-eyebrow">Case reporting map</span><h2>{user.barangay_name}</h2></div><span className="bd-map-number">{patients.length}<small>registered patients</small></span></header><div ref={mapElement} className="bd-map"/><p>Summary count follows patient registry. Patient identities remain private and are not plotted.</p></section>
    <ReportingWorkspace user={user} embedded onPatientsChange={setPatients} />
  </main>
}

async function read(response: Response) { const body = await response.json(); if (!response.ok) throw new Error(body.message ?? `Request failed (${response.status})`); return body }
