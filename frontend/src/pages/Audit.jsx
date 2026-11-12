import React from 'react'
import PlaceholderPage from '../components/PlaceholderPage'

export default function Audit() {
  return (
    <PlaceholderPage title="Audit" description="A record of every action taken in Agent Control Tower."
      emptyTitle="No audit entries to show yet"
      emptyText="This page will list every action, with a verified hash chain that proves nothing was altered, and a CSV export for your auditors." />
  )
}
