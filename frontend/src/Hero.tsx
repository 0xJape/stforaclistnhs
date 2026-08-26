import { useEffect, useRef, useState, type CSSProperties } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Map as MapLibreMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

type HeroRow={PSGC:string;POSTERIOR_MEAN_CASES:number;OUTBREAK_PROBABILITY:number}
const mosquitoStyle=(item:number)=>({'--n':item}) as CSSProperties

export default function Hero({rows,onEnter,leaving}:{rows:HeroRow[];onEnter:()=>void;leaving:boolean}){
 const container=useRef<HTMLDivElement>(null),mapRef=useRef<MapLibreMap|null>(null),geoRef=useRef<{type:'FeatureCollection';features:{properties:Record<string,string|number>}[]} | null>(null),rowsRef=useRef(rows),frameRef=useRef(0),resumeTimerRef=useRef(0),interactingRef=useRef(false),[sweeping,setSweeping]=useState(false)
 const applyRows=(geo:{type:'FeatureCollection';features:{properties:Record<string,string|number>}[]},data:HeroRow[])=>{const byPsgc=new Map(data.map(row=>[String(row.PSGC),row]));geo.features.forEach(feature=>{const p=feature.properties,id=String(p.psgc??p.ORACLIS_PSGC??p.adm4_psgc??''),row=byPsgc.get(id);p.cases=row?.POSTERIOR_MEAN_CASES??.35;p.risk=row?.OUTBREAK_PROBABILITY??.08})}
 useEffect(()=>{rowsRef.current=rows;const geo=geoRef.current,source=mapRef.current?.getSource('hero-barangays') as maplibregl.GeoJSONSource|undefined;if(geo&&source){applyRows(geo,rows);source.setData(geo as Parameters<typeof source.setData>[0])}},[rows])
 useEffect(()=>{if(!container.current)return
    const map=new maplibregl.Map({container:container.current,center:[124.82,6.31],zoom:8.15,minZoom:7.85,maxZoom:9.1,maxBounds:[[124.30,5.90],[125.31,6.76]],pitch:52,maxPitch:68,bearing:-18,dragRotate:true,touchZoomRotate:true,touchPitch:true,scrollZoom:true,dragPan:true,keyboard:true,cooperativeGestures:false,style:{version:8,sources:{base:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'}},layers:[{id:'base',type:'raster',source:'base',paint:{'raster-opacity':.16,'raster-saturation':-1,'raster-brightness-max':.34}}]}})
  mapRef.current=map
    map.dragPan.enable();map.dragRotate.enable();map.scrollZoom.enable();map.touchZoomRotate.enable();map.keyboard.enable()
  map.addControl(new maplibregl.NavigationControl({showCompass:true,showZoom:false,visualizePitch:true}),'bottom-right')
  map.once('load',async()=>{try{const payload=await fetch('/maps/south_cotabato_barangays_2023.geojson').then(response=>{if(!response.ok)throw new Error('Map unavailable');return response.json()});const {crs: _crs,...geo}=payload
   geoRef.current=geo;applyRows(geo,rowsRef.current)
   map.addSource('hero-barangays',{type:'geojson',data:geo})
  map.addLayer({id:'hero-fill',type:'fill',source:'hero-barangays',paint:{'fill-color':['interpolate',['linear'],['get','risk'],0,'#263238',.25,'#70452f',.55,'#b96442',.8,'#e5484d',1,'#ffddd8'],'fill-opacity':.72}})
   map.addLayer({id:'hero-extrusion',type:'fill-extrusion',source:'hero-barangays',paint:{'fill-extrusion-color':['interpolate',['linear'],['get','risk'],0,'#20262a',.25,'#70452f',.55,'#b96442',.8,'#e5484d',1,'#ffddd8'],'fill-extrusion-height':['interpolate',['linear'],['get','cases'],0,0,1,850,5,2500,12,5200],'fill-extrusion-base':0,'fill-extrusion-opacity':.88}})
  map.addLayer({id:'hero-line',type:'line',source:'hero-barangays',paint:{'line-color':'#00f5ff','line-width':1.4,'line-opacity':1}})
  requestAnimationFrame(()=>{map.resize();map.fitBounds([[124.406,6.010],[125.190,6.655]],{padding:40,duration:0,maxZoom:8.8})})
  const pauseRotation=()=>{interactingRef.current=true;window.clearTimeout(resumeTimerRef.current);resumeTimerRef.current=window.setTimeout(()=>{interactingRef.current=false},5000)}
  map.on('movestart',pauseRotation)
  const rotate=()=>{if(!interactingRef.current)map.rotateTo(map.getBearing()+.006,{duration:0});frameRef.current=requestAnimationFrame(rotate)}
  rotate()
  }catch(error){console.error('Hero map failed',error)}})
  const resizeObserver=new ResizeObserver(()=>map.resize());resizeObserver.observe(container.current)
  return()=>{resizeObserver.disconnect();cancelAnimationFrame(frameRef.current);window.clearTimeout(resumeTimerRef.current);map.remove();mapRef.current=null}
 },[])
 const sweep=()=>{setSweeping(true);mapRef.current?.easeTo({pitch:64,bearing:mapRef.current.getBearing()+28,duration:850,essential:true});window.setTimeout(()=>setSweeping(false),1050)}
 return <section className={`hero${leaving?' is-leaving':''}${sweeping?' is-sweeping':''}`} aria-label="ORACLIS intelligence landing"><div className="hero-map" ref={container}/><div className="hero-vignette"/><div className="hero-grid"/><div className="hero-scan"/><div className="hero-copy"><div className="hero-kicker"><i/>South Cotabato · live scenario intelligence</div><h1>See risk<br/><em>before spread.</em></h1><p>ORACLIS turns barangay-level Bayesian projections into operational spatial intelligence.</p><div className="hero-facts"><span><b>{rows.length||199}</b>barangays</span><span><b>3D</b>risk terrain</span><span><b>16D</b>weather context</span></div><div className="hero-actions"><button className="hero-enter" onClick={onEnter}>Open intelligence map <span>↓</span></button><button className="hero-sweep" onClick={sweep} aria-pressed={sweeping}>Run signal sweep <span>⌁</span></button></div><small className="hero-hint">Drag terrain · scroll or pinch zoom · right-drag tilt</small></div><div className="hero-mosquitoes" aria-hidden="true">{[1,2,3,4,5,6,7,8].map(item=><i key={item} style={mosquitoStyle(item)}>⌁</i>)}</div><button className="hero-skip" onClick={onEnter}>Skip intro</button><small className="hero-note">Scenario visualization · not official outbreak declaration</small></section>
}
