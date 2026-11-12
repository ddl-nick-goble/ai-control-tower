import React from 'react'
import { Link } from 'react-router-dom'
import { Breadcrumb } from '../components/form'

export default function NotFound() {
  return (
    <div className="ds-page">
      <header className="ds-page-header">
        <Breadcrumb items={[{ label: 'Fleet', to: '/' }, { label: 'Page not found' }]} />
        <h1 className="ds-title">Page not found</h1>
      </header>
      <div className="ds-card ds-empty" data-testid="not-found">
        <h2 className="ds-empty-title">This page doesn't exist</h2>
        <p className="ds-helper">The link may be out of date or mistyped. Go back to the fleet to find what you need.</p>
        <Link to="/">Back to Fleet</Link>
      </div>
    </div>
  )
}
