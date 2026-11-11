import React from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter, Routes, Route } from 'react-router-dom'
import './styles.css'
import './design.css'
import { Shell } from './components/ui'
import Fleet from './pages/Fleet'
import Agent from './pages/Agent'
import Run from './pages/Run'
import Register from './pages/Register'
import Settings from './pages/Settings'

createRoot(document.getElementById('root')).render(
  <HashRouter>
    <Shell>
      <Routes>
        <Route path="/" element={<Fleet />} />
        <Route path="/agents/:id" element={<Agent />} />
        <Route path="/runs/:runId" element={<Run />} />
        <Route path="/register" element={<Register />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Fleet />} />
      </Routes>
    </Shell>
  </HashRouter>
)
