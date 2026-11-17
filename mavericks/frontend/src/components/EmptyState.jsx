export default function EmptyState({ icon = '◇', title, children }) {
  return (
    <div className="empty">
      <div className="empty__icon" aria-hidden="true">{icon}</div>
      <h3>{title}</h3>
      {children && <div className="empty__body">{children}</div>}
    </div>
  )
}
