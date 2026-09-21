import type { TradeSignal } from "../store";

interface Props {
  title: string;
  kind: "buy" | "sell" | "short";
  signals: TradeSignal[];
  onSelect: (symbol: string) => void;
}

function fmt(n?: number | null, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function SignalPanel({ title, kind, signals, onSelect }: Props) {
  return (
    <section className="panel">
      <h3>
        <i className={`dot ${kind}`} />
        {title}
      </h3>
      {signals.length === 0 && <p className="empty">No signals in this category.</p>}
      {signals.map((s) => (
        <div
          key={s.symbol}
          className="signal-row"
          onClick={() => onSelect(s.symbol)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && onSelect(s.symbol)}
        >
          <div className="sym">{s.symbol}</div>
          <div>
            <div className="meta">
              {s.primary_signal}
              {s.secondary_signal !== "NONE" ? ` · ${s.secondary_signal}` : ""}
              {" · "}
              entry {fmt(s.entry_price)}
              {s.stop_loss != null ? ` · SL ${fmt(s.stop_loss)}` : ""}
              {s.take_profit != null ? ` · TP ${fmt(s.take_profit)}` : ""}
              {s.portfolio_allocation_pct != null ? ` · ${fmt(s.portfolio_allocation_pct, 1)}%` : ""}
            </div>
            <div className={`conf-bar ${kind === "buy" ? "" : "sell"}`}>
              <i style={{ width: `${Math.min(100, s.confidence)}%` }} />
            </div>
          </div>
          <div className="conf-num">{Math.round(s.confidence)}%</div>
        </div>
      ))}
    </section>
  );
}
