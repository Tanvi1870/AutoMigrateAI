export default function ErrorBanner({ error }) {
  if (!error) return null
  const details = (error.details ?? []).filter((detail) => detail.field || detail.message !== error.message)
  return (
    <div className="error-banner" role="alert">
      <div className="error-banner__head">
        <span className="error-banner__code">{error.code}</span>
        <span>{error.message}</span>
      </div>
      {details.length > 0 && (
        <ul>
          {details.map((detail, index) => (
            <li key={index}>
              {detail.field && <code>{detail.field}</code>} {detail.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
