import { useState } from 'react'
import Panel from './Panel.jsx'
import CodeView from './CodeView.jsx'
import EmptyState from './EmptyState.jsx'
import ErrorBanner from './ErrorBanner.jsx'
import { formatTime } from '../lib/status.js'

const KIND_LABEL = {
  TEST: 'test',
  PAGE_OBJECT: 'page',
  TEST_DATA: 'data',
  CONFIG: 'config',
  CONFTEST: 'fixture',
  PYTEST_CONFIG: 'pytest',
  REQUIREMENTS: 'deps',
  OTHER: '',
}

export default function GeneratedCode({ state, actions, busy, onNavigate }) {
  const { project, plan } = state
  const [selected, setSelected] = useState(null)
  const [copied, setCopied] = useState(false)

  if (!project) {
    return (
      <Panel title="Generated Code">
        <ErrorBanner error={state.errors.generate} />
        <EmptyState icon="</>" title={state.busy === 'generate' ? 'Generating project…' : 'No code generated yet'}>
          {plan?.status === 'READY' ? (
            <button type="button" className="btn btn--primary" disabled={busy} onClick={() => actions.generate().catch(() => {})}>
              Generate now
            </button>
          ) : (
            'Analyze the asset and get a READY plan first.'
          )}
        </EmptyState>
      </Panel>
    )
  }

  const files = project.artifacts.filter((artifact) => artifact.content || artifact.kind !== 'OTHER')
  const current =
    files.find((artifact) => artifact.path === selected) ?? files.find((artifact) => artifact.kind === 'TEST') ?? files[0]
  const markers = Object.fromEntries(
    Object.entries(current.stepLineMap).map(([step, line]) => [line, `S${step}`]),
  )

  function copy() {
    navigator.clipboard?.writeText(current.content).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="stack">
      <Panel
        title="Generated Project"
        subtitle={`${project.projectPath} · generated ${formatTime(project.generatedAt)}`}
        aside={
          <div className="row">
            <span className="chip chip--target">{project.targetFramework}</span>
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={() =>
                actions
                  .validate()
                  .then(() => onNavigate('validation'))
                  .catch(() => {})
              }
            >
              {state.busy === 'validate' ? 'Validating…' : 'Validate →'}
            </button>
          </div>
        }
      >
        <ErrorBanner error={state.errors.validate} />
        <div className="code-browser">
          <ul className="file-tree">
            {files.map((artifact) => (
              <li key={artifact.path}>
                <button
                  type="button"
                  className={artifact.path === current.path ? 'is-active' : ''}
                  onClick={() => setSelected(artifact.path)}
                >
                  <span className="file-tree__path">{artifact.path}</span>
                  {KIND_LABEL[artifact.kind] && <span className="file-tree__kind">{KIND_LABEL[artifact.kind]}</span>}
                </button>
              </li>
            ))}
          </ul>
          <div className="code-pane">
            <div className="code-pane__head">
              <span className="mono">{current.path}</span>
              <span className="row">
                {Object.keys(current.stepLineMap).length > 0 && (
                  <span className="chip chip--soft">{Object.keys(current.stepLineMap).length} ICAP steps traced</span>
                )}
                <button type="button" className="btn btn--ghost btn--sm" onClick={copy}>
                  {copied ? 'Copied ✓' : 'Copy'}
                </button>
              </span>
            </div>
            <CodeView content={current.content} path={current.path} markers={markers} maxHeight="62vh" />
          </div>
        </div>
      </Panel>
    </div>
  )
}
