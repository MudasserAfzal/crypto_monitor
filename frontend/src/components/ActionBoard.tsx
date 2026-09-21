import { useMemo, useState } from "react";
import type { LiveQuote, ShortTermBook } from "../store";

function money(n?: number | null, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-IE", { maximumFractionDigits: digits });
}

function decide(quote: LiveQuote | undefined, entry?: number | null, stop?: number | null, tp?: number | null) {
  if (!quote) return { action: "WAIT", detail: "Waiting for live price" };
  const p = quote.price_eur;
  if (stop && p <= stop) return { action: "STOP / do not buy", detail: "Price is through the stop. Cancel the limit." };
  if (entry && p <= entry * 1.005) return { action: "BUY", detail: "Live price is at/under the limit. The buy would fill." };
  if (tp && p >= tp) return { action: "SELL", detail: "Live price is at/above take-profit. If you hold, sell." };
  return { action: "WAIT", detail: "Above the limit. Keep the order working, do not chase." };
}

interface Props {
  book: ShortTermBook;
  quotes: LiveQuote[];
  onSelect: (symbol: string) => void;
}

export function ActionBoard({ book, quotes, onSelect }: Props) {
  const [confirmed, setConfirmed] = useState<Record<string, string>>({});
  const bySym = useMemo(() => Object.fromEntries(quotes.map((q) => [q.symbol, q])), [quotes]);

  return (
    <section className="starter action-board">
      <div className="starter-head">
        <h2>What to buy / sell now</h2>
        <div className="starter-action hold">Confirm a ticket to track it</div>
      </div>
      <p className="starter-why">{book.thesis}</p>
      <div className="action-list">
        {book.legs.map((leg) => {
          const q = bySym[leg.symbol];
          const live = q?.price_eur;
          const digits = (live ?? 0) < 10 ? 4 : (live ?? 0) < 1000 ? 2 : 0;
          const { action, detail } = decide(q, leg.entry_price_eur, leg.stop_loss_eur, leg.take_profit_eur);
          const tone = action.startsWith("BUY") ? "pos" : action.startsWith("SELL") || action.startsWith("STOP") ? "neg" : "hold";
          const mark = confirmed[leg.symbol];
          return (
            <div key={leg.symbol} className="action-card" onClick={() => onSelect(leg.symbol)}>
              <div className="action-top">
                <strong>
                  {leg.symbol} · €{money(leg.alloc_eur, 0)}
                </strong>
                <span className={`starter-action ${tone}`}>{action}</span>
              </div>
              <div className="action-grid">
                <span>
                  Live €{money(live, digits)}{" "}
                  {q ? (
                    <span className={q.change_24h_pct >= 0 ? "pos" : "neg"}>
                      {q.change_24h_pct >= 0 ? "+" : ""}
                      {q.change_24h_pct.toFixed(2)}%
                    </span>
                  ) : null}
                </span>
                <span>
                  Limit €{money(leg.entry_price_eur, digits)}
                  {q?.vs_limit_pct != null ? ` (${q.vs_limit_pct > 0 ? "+" : ""}${q.vs_limit_pct.toFixed(1)}%)` : ""}
                </span>
                <span className="neg">Stop €{money(leg.stop_loss_eur, digits)}</span>
                <span className="pos">TP €{money(leg.take_profit_eur, digits)}</span>
              </div>
              <p className="leg-why">{detail}</p>
              <div className="hero-actions" style={{ justifyContent: "flex-start" }}>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmed((c) => ({ ...c, [leg.symbol]: action }));
                  }}
                >
                  {mark ? `Confirmed: ${mark}` : `Confirm ${action}`}
                </button>
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmed((c) => ({ ...c, [leg.symbol]: "skipped" }));
                  }}
                >
                  Skip
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
