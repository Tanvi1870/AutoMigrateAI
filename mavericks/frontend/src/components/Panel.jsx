export default function Panel({ title, subtitle, aside, children, className = '' }) {
  return (
    <section className={`panel ${className}`}>
      {(title || aside) && (
        <header className="panel__head">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p className="panel__subtitle">{subtitle}</p>}
          </div>
          {aside && <div className="panel__aside">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  )
}
