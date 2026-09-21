import type { ShortTermBook } from "../store";

function money(n?: number | null, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-IE", { maximumFractionDigits: digits });
}

interface Props {
  book: ShortTermBook;
}

export function ShortTermBookCard({ book }: Props) {
  return (
    <section className="starter short-book">
      <div className="starter-head">
        <h2>Short-term sleeve</h2>
        <div className="starter-action hold">BTC + SOL + XRP · limits only</div>
      </div>
      <p className="starter-why">{book.thesis}</p>
      <div className="leg-table">
        <div className="leg-row leg-head">
          <span>Coin</span>
          <span>€</span>
          <span>Last</span>
          <span>7d bounce</span>
          <span>Limit</span>
          <span>Stop</span>
          <span>Target</span>
        </div>
        {book.legs.map((l) => (
          <div className="leg-row" key={l.symbol}>
            <span>
              <strong>{l.symbol}</strong>
              <span className="hint"> {l.role}</span>
            </span>
            <span>{money(l.alloc_eur, 0)}</span>
            <span>€{money(l.last_eur, l.last_eur != null && l.last_eur < 10 ? 2 : 0)}</span>
            <span className={l.bounce_7d_pct != null && l.bounce_7d_pct >= 6 ? "neg" : "pos"}>
              {l.bounce_7d_pct}%
            </span>
            <span>€{money(l.entry_price_eur, l.entry_price_eur != null && l.entry_price_eur < 10 ? 2 : 0)}</span>
            <span className="neg">
              €{money(l.stop_loss_eur, l.stop_loss_eur != null && l.stop_loss_eur < 10 ? 2 : 0)}
            </span>
            <span className="pos">
              €{money(l.take_profit_eur, l.take_profit_eur != null && l.take_profit_eur < 10 ? 2 : 0)}
            </span>
          </div>
        ))}
      </div>
      {book.legs.map((l) => (
        <p className="leg-why" key={`${l.symbol}-why`}>
          <strong>{l.symbol}.</strong> {l.why} {l.news_analog}
        </p>
      ))}
      {book.skipped.length > 0 && (
        <div className="skipped">
          {book.skipped.map((s) => (
            <div key={s}>{s}</div>
          ))}
        </div>
      )}
    </section>
  );
}
