import { useRef, useState } from 'react'
import Panel from './Panel.jsx'
import ErrorBanner from './ErrorBanner.jsx'
import StatusBadge from './StatusBadge.jsx'

export default function UploadPanel({ state, actions, busy, onNavigate }) {
  const input = useRef(null)
  const [dragging, setDragging] = useState(false)
  const [fileName, setFileName] = useState(null)
  const uploading = state.busy === 'upload'

  function submit(file) {
    if (!file || busy) return
    setFileName(file.name)
    actions
      .upload(file)
      .then(() => onNavigate('analysis'))
      .catch(() => {})
  }

  return (
    <div className="stack">
      <Panel
        title="Upload ICAP Asset"
        subtitle="Upload an ICAP test case export (.json) or a CSV step table (.csv) with inline locators."
      >
        <div
          className={`dropzone ${dragging ? 'is-dragging' : ''} ${uploading ? 'is-busy' : ''}`}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            submit(event.dataTransfer.files[0])
          }}
          onClick={() => input.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => (event.key === 'Enter' || event.key === ' ') && input.current?.click()}
        >
          <div className="dropzone__icon">{uploading ? <span className="spinner spinner--lg" /> : '⇪'}</div>
          <div className="dropzone__title">
            {uploading ? `Parsing ${fileName}…` : 'Drop an ICAP export here or click to browse'}
          </div>
          <div className="dropzone__hint">JSON or CSV · parsed into the canonical test model on the server</div>
          <input
            ref={input}
            type="file"
            accept=".json,.csv,application/json,text/csv"
            hidden
            onChange={(event) => {
              submit(event.target.files[0])
              event.target.value = ''
            }}
          />
        </div>
        <ErrorBanner error={state.errors.upload} />
      </Panel>

      {state.migration && (
        <Panel title="Parsed Test Case" aside={<StatusBadge status="PASS" label="PARSED" />}>
          <div className="kv-grid">
            <Field label="Migration ID" value={state.migration.migrationId} mono />
            <Field label="ICAP Test Case" value={state.migration.testCaseId} mono />
            <Field label="Name" value={state.migration.name} />
            <Field label="Steps Detected" value={state.migration.stepsDetected} big />
            <Field label="Source Format" value={state.migration.sourceFormat} mono />
          </div>
          <div className="actions">
            <button type="button" className="btn" onClick={() => onNavigate('analysis')}>
              Continue to analysis →
            </button>
          </div>
        </Panel>
      )}
    </div>
  )
}

function Field({ label, value, mono, big }) {
  return (
    <div className="kv">
      <span className="kv__label">{label}</span>
      <span className={`kv__value ${mono ? 'mono' : ''} ${big ? 'kv__value--big' : ''}`}>{value ?? '—'}</span>
    </div>
  )
}
