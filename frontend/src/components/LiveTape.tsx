import type { LiveQuote } from "../store";

function money(n: number, digits = 2) {
  if (!n && n !== 0) return "—";
  return n.toLocaleString("en-IE", { maximumFractionDigits: digits });
}

const ACTION_CLASS: Record<string, string> = {
  BUY: "pos",
  SELL: "neg",
  STOP: "neg",
  WAIT: "hold",
  WATCH: "",
  WATCH_DIP: "hold",
};

interface Props {
  quotes: LiveQuote[];
  onSelect: (symbol: string) => void;
  selected?: string | null;
  updatedAt?: string | null;
}

export function LiveTape({ quotes, onSelect, selected, updatedAt }: Props) {
  return (
    <section className="tape">
      <div className="tape-head">
        <h2>Live prices</h2>
        <span className="hint">{updatedAt ? `Updated ${updatedAt}` : "Polling every 12s from Binance/Kraken"}</span>
      </div>
      {!quotes.length ? (
        <div className="empty">Waiting for live EUR prices…</div>
      ) : (
        <div className="tape-row">
          {quotes.map((q) => {
            const up = q.change_24h_pct >= 0;
            const digits = q.price_eur < 10 ? 4 : q.price_eur < 1000 ? 2 : 0;
            return (
              <button
                key={q.symbol}
                className={`tape-chip ${selected === q.symbol ? "active" : ""}`}
                onClick={() => onSelect(q.symbol)}
                type="button"
              >
                <div className="tape-sym">{q.symbol}</div>
                <div className="tape-px">€{money(q.price_eur, digits)}</div>
                <div className={up ? "pos" : "neg"}>
                  {up ? "▲ +" : "▼ "}
                  {q.change_24h_pct.toFixed(2)}% 24h
                </div>
                <div className={`tape-act ${ACTION_CLASS[q.action_now] || ""}`}>
                  {q.action_now}
                  {q.vs_limit_pct != null ? ` · vs limit ${q.vs_limit_pct > 0 ? "+" : ""}${q.vs_limit_pct.toFixed(1)}%` : ""}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
