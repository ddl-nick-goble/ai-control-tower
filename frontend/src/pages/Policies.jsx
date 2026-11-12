import React from 'react'
import PlaceholderPage from '../components/PlaceholderPage'

export default function Policies() {
  return (
    <PlaceholderPage title="Policies" description="The rules that decide how each agent is tiered."
      emptyTitle="No tiering rules to show yet"
      emptyText="This page will hold the tiering rules, with an impact preview that shows which agents would change tier before you save a rule." />
  )
}
