import { useState } from 'react'

/** Asks for the backend API key when the backend requires one (health.authRequired). */
export default function ApiKeyPanel({ hasKey, onSave }) {
  const [value, setValue] = useState('')
  const [editing, setEditing] = useState(!hasKey)

  if (hasKey && !editing) {
    return (
      <div className="api-key">
        <span className="api-key__status">API key set for this tab</span>
        <button type="button" className="btn btn--sm" onClick={() => setEditing(true)}>
          Change
        </button>
      </div>
    )
  }

  function submit(event) {
    event.preventDefault()
    const key = value.trim()
    if (!key) return
    onSave(key)
    setValue('')
    setEditing(false)
  }

  return (
    <form className="api-key" onSubmit={submit}>
      <label htmlFor="api-key-input">{hasKey ? 'New API key' : 'API key required'}</label>
      <input
        id="api-key-input"
        type="password"
        autoComplete="off"
        placeholder="X-API-Key"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      <button type="submit" className="btn btn--sm" disabled={!value.trim()}>
        Save
      </button>
    </form>
  )
}
