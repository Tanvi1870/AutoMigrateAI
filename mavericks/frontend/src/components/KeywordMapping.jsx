import Panel from './Panel.jsx'
import EmptyState from './EmptyState.jsx'
import StatusBadge from './StatusBadge.jsx'
import { fileName } from '../lib/status.js'

export default function KeywordMapping({ state, onNavigate }) {
  const { keywords, project, target } = state
  if (!keywords) {
    return (
      <EmptyState icon="⇄" title="No keyword mapping yet">
        The mapping is produced when the asset is analyzed.
        <div className="actions actions--center">
          <button type="button" className="btn" onClick={() => onNavigate(state.migration ? 'analysis' : 'upload')}>
            {state.migration ? 'Go to analysis' : 'Upload an asset'}
          </button>
        </div>
      </EmptyState>
    )
  }

  const testFile = project?.artifacts.find((artifact) => artifact.kind === 'TEST')
  const resolved = keywords.filter((mapping) => mapping.status === 'RESOLVED').length
  const distinct = new Set(keywords.map((mapping) => mapping.keyword)).size

  return (
    <Panel
      title="ICAP Keyword → Python Mapping"
      subtitle={`From the keyword knowledge base for ${target === 'playwright' ? 'Playwright' : 'Selenium'}. Unmapped keywords are never guessed.`}
      aside={
        <div className="row">
          <span className="chip">{distinct} distinct keywords</span>
          <StatusBadge status={resolved === keywords.length ? 'PASS' : 'FAIL'} label={`${resolved}/${keywords.length} steps mapped`} />
        </div>
      }
    >
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Step</th>
              <th>ICAP keyword</th>
              <th>Meaning</th>
              <th>Python {target === 'playwright' ? 'Playwright' : 'Selenium'}</th>
              <th>Generated</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {keywords.map((mapping) => {
              const line = testFile?.stepLineMap[String(mapping.stepSequence)]
              const notes = [mapping.message, mapping.assumption, ...mapping.warnings, ...mapping.issues.map((i) => i.message)].filter(Boolean)
              return (
                <tr key={mapping.stepSequence}>
                  <td className="mono">{mapping.stepSequence}</td>
                  <td>
                    <code className="kw">{mapping.keyword}</code>
                  </td>
                  <td>
                    {mapping.description ?? '—'}
                    {notes.map((note, index) => (
                      <div key={index} className="table__note">
                        {note}
                      </div>
                    ))}
                  </td>
                  <td>
                    {mapping.template ? <code className="snippet">{mapping.template}</code> : <span className="muted">no mapping</span>}
                  </td>
                  <td className="mono">
                    {line ? (
                      <button type="button" className="link" onClick={() => onNavigate('code')}>
                        {fileName(testFile.path)}:{line}
                      </button>
                    ) : (
                      <span className="muted">not generated</span>
                    )}
                  </td>
                  <td>
                    <StatusBadge status={mapping.status} label={mapping.status} size="sm" />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
