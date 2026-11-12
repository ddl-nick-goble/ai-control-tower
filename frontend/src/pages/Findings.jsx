import React from 'react'
import PlaceholderPage from '../components/PlaceholderPage'

export default function Findings() {
  return (
    <PlaceholderPage title="Findings" description="Every finding raised against your agents, across the fleet."
      emptyTitle="No findings to triage yet"
      emptyText="This page will list every finding across the fleet, so you can triage them by severity, agent and status in one place." />
  )
}
