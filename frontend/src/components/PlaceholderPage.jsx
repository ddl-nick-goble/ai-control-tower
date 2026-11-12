import React from 'react'
import { Breadcrumb } from './form'

// A designed empty state for a section that is not built yet: says what will appear there.
export default function PlaceholderPage({ title, description, emptyTitle, emptyText }) {
  return (
    <div className="ds-page">
      <header className="ds-page-header">
        <Breadcrumb items={[{ label: 'Fleet', to: '/' }, { label: title }]} />
        <div className="ds-page-header-row">
          <div>
            <h1 className="ds-title" data-testid="page-title">{title}</h1>
            <p className="ds-description" data-testid="page-description">{description}</p>
          </div>
        </div>
      </header>
      <div className="ds-card ds-empty" data-testid="empty-state">
        <h2 className="ds-empty-title">{emptyTitle}</h2>
        <p className="ds-helper">{emptyText}</p>
      </div>
    </div>
  )
}
