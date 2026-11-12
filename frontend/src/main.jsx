import React from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter, Routes, Route } from 'react-router-dom'
import './styles.css'
import './design.css'
import './shell.css'
import { Shell } from './components/ui'
import Fleet from './pages/Fleet'
import Agent from './pages/Agent'
import Run from './pages/Run'
import Register from './pages/Register'
import Settings from './pages/Settings'
import Home from './pages/Home'
import Findings from './pages/Findings'
import Policies from './pages/Policies'
import Connections from './pages/Connections'
import Audit from './pages/Audit'
import NotFound from './pages/NotFound'

createRoot(document.getElementById('root')).render(
  <HashRouter>
    <Shell>
      <Routes>
        <Route path="/" element={<Fleet />} />
        <Route path="/agents/:id" element={<Agent />} />
        <Route path="/runs/:runId" element={<Run />} />
        <Route path="/register" element={<Register />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/home" element={<Home />} />
        <Route path="/findings" element={<Findings />} />
        <Route path="/policies" element={<Policies />} />
        <Route path="/connections" element={<Connections />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Shell>
  </HashRouter>
)
