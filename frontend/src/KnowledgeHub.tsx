import { useMemo, useState } from 'react'

type Topic={title:string;tags:string;summary:string;points:string[]}

const SOURCE={name:'World Health Organization',reviewed:'21 August 2025',url:'https://www.who.int/news-room/fact-sheets/detail/dengue-and-severe-dengue'}
const TOPICS:Topic[]=[
  {title:'Dengue basics',tags:'dengue mosquito transmission infection',summary:'Dengue is a viral infection transmitted mainly through bites from infected Aedes mosquitoes.',points:['Many infections cause no symptoms or only mild illness.','Symptoms commonly begin 4–10 days after infection and last 2–7 days.']},
  {title:'Symptoms',tags:'symptoms fever headache rash nausea pain',summary:'Common symptoms include high fever, severe headache, pain behind the eyes, muscle or joint pain, nausea, vomiting, swollen glands, and rash.',points:['Symptoms alone cannot confirm dengue.','Contact a qualified health professional for assessment and testing guidance.']},
  {title:'Warning signs',tags:'warning emergency severe abdominal vomiting bleeding breathing weak',summary:'Severe dengue warning signs often appear after fever subsides and require urgent care.',points:['Seek care immediately for severe abdominal pain, persistent vomiting, rapid breathing, bleeding, blood in vomit or stool, extreme thirst, pale or cold skin, weakness, fatigue, or restlessness.','Severe dengue can be fatal without timely medical care.']},
  {title:'Prevention',tags:'prevention mosquito bites repellent water containers screens',summary:'Prevention relies on avoiding mosquito bites and reducing mosquito breeding sites.',points:['Use covering clothing, screens, nets, and suitable mosquito repellent.','Cover, empty, and clean water containers weekly; remove objects that collect standing water.']},
  {title:'Care and treatment',tags:'care treatment medicine hydration paracetamol aspirin ibuprofen',summary:'There is no specific treatment for dengue; early detection and proper medical care reduce severe-dengue deaths.',points:['Rest, drink fluids, and seek professional medical advice.','WHO advises avoiding aspirin and ibuprofen because they can increase bleeding risk; medication decisions belong with a qualified professional.']},
]

export default function KnowledgeHub({onClose}:{onClose:()=>void}){
  const [query,setQuery]=useState('')
  const topics=useMemo(()=>{const value=query.trim().toLowerCase();return value?TOPICS.filter(topic=>`${topic.title} ${topic.tags} ${topic.summary} ${topic.points.join(' ')}`.toLowerCase().includes(value)):TOPICS},[query])
  return <section className="knowledge-hub" aria-labelledby="knowledge-title">
    <header><div><span className="eyebrow">Reviewed health guidance</span><h2 id="knowledge-title">Dengue knowledge hub</h2></div><button type="button" aria-label="Close knowledge hub" onClick={onClose}>×</button></header>
    <input type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder="Search symptoms, prevention, or care…" aria-label="Search dengue knowledge"/>
    <div className="knowledge-results" aria-live="polite">{topics.length?topics.map(topic=><article key={topic.title}><h3>{topic.title}</h3><p>{topic.summary}</p><ul>{topic.points.map(point=><li key={point}>{point}</li>)}</ul></article>):<p className="service-state">No matching topic. Try symptoms, warning signs, prevention, or care.</p>}</div>
    <footer><strong>Educational guidance—not medical advice.</strong><span>Source: <a href={SOURCE.url} target="_blank" rel="noreferrer">{SOURCE.name}</a> · reviewed {SOURCE.reviewed}</span></footer>
  </section>
}
