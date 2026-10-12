import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import MethodologyPage from './MethodologyPage.tsx'
import HelpSupportPage from './HelpSupportPage.tsx'
import ThemeToggle from './ThemeToggle.tsx'
import './theme.css'

document.documentElement.dataset.theme = localStorage.getItem('oraclis-theme') || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')

function Root(){
  const [hash,setHash]=useState(location.hash)
  useEffect(()=>{const update=()=>setHash(location.hash);addEventListener('hashchange',update);return()=>removeEventListener('hashchange',update)},[])
  return <>{hash==='#/methodology'?<MethodologyPage/>:hash==='#/help'?<HelpSupportPage/>:<App/>}<ThemeToggle/></>
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
