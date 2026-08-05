import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import MethodologyPage from './MethodologyPage.tsx'

function Root(){
  const [hash,setHash]=useState(location.hash)
  useEffect(()=>{const update=()=>setHash(location.hash);addEventListener('hashchange',update);return()=>removeEventListener('hashchange',update)},[])
  return hash==='#/methodology'?<MethodologyPage/>:<App/>
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
