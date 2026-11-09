import React from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter, Routes, Route } from 'react-router-dom'

function Home() {
  return (
    <main style={{ fontFamily: 'sans-serif', padding: 24 }}>
      <h1>Agent Control Tower</h1>
      <p>Placeholder. Screens arrive in a later task.</p>
    </main>
  )
}

createRoot(document.getElementById('root')).render(
  <HashRouter>
    <Routes>
      <Route path="*" element={<Home />} />
    </Routes>
  </HashRouter>
)
