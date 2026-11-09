import React from 'react'
import { Link } from 'react-router-dom'

export default function Register() {
  return (
    <div>
      <h1>Register external agent</h1>
      <p className="muted">Registration form arrives in a later task.</p>
      <Link to="/">Back to fleet</Link>
    </div>
  )
}
