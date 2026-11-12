import React from 'react'
import PlaceholderPage from '../components/PlaceholderPage'

export default function Home() {
  return (
    <PlaceholderPage title="Home" description="What needs your attention today, in one place."
      emptyTitle="Nothing to show here yet"
      emptyText="This page will list what needs you today: pending reviews, approvals that are about to expire, critical findings, and agents that have no identity yet." />
  )
}
