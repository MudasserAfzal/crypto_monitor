import type { NewsDipContext } from "../store";

interface Props {
  context: NewsDipContext;
}

export function NewsDipPanel({ context }: Props) {
  return (
    <section className="news-dip">
      <div className="news-dip-head">
        <h2>What the last dips were about</h2>
        <p>
          Current news bucket: <strong>{context.dominant_category}</strong> → {context.verdict.replace(/_/g, " ")}
        </p>
      </div>
      <p className="playbook">{context.playbook}</p>

      {context.historical_analogs.length > 0 && (
        <div className="analog-table">
          <div className="analog-row analog-head">
            <span>Date</span>
            <span>News</span>
            <span>Type</span>
            <span>Dip</span>
            <span>7d after</span>
            <span>Then</span>
          </div>
          {context.historical_analogs.map((a) => (
            <div className="analog-row" key={`${a.date}-${a.headline}`}>
              <span>{a.date}</span>
              <span className="headline" title={a.lesson}>
                {a.headline}
              </span>
              <span>{a.category}</span>
              <span className="neg">{a.drop_pct}%</span>
              <span className={a.bounce_7d_pct != null && a.bounce_7d_pct >= 0 ? "pos" : "neg"}>
                {a.bounce_7d_pct == null ? "—" : `${a.bounce_7d_pct}%`}
              </span>
              <span>{a.typical_action}</span>
            </div>
          ))}
        </div>
      )}

      {context.current_headlines.length > 0 && (
        <div className="headlines">
          <h3>Headlines driving this read</h3>
          <ul>
            {context.current_headlines.slice(0, 8).map((h) => (
              <li key={h.title}>
                <span className="tag">{h.category}</span>
                {h.url ? (
                  <a href={h.url} target="_blank" rel="noreferrer">
                    {h.title}
                  </a>
                ) : (
                  <span>{h.title}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {context.recent_dips.length > 0 && (
        <div className="recent-dips">
          <h3>Recent BTC dips vs known events</h3>
          <ul>
            {context.recent_dips
              .slice()
              .reverse()
              .map((d) => (
                <li key={`${d.date}-${d.drop_pct}`}>
                  {d.date}: {d.drop_pct}%{" "}
                  {d.matched_event ? `— ${d.matched_event} (${d.news_type})` : "— no catalog match"}
                  {d.bounce_7d_pct != null ? ` · 7d ${d.bounce_7d_pct}%` : ""}
                </li>
              ))}
          </ul>
        </div>
      )}
    </section>
  );
}
