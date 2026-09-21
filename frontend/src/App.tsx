import { useCallback, useEffect, useState } from "react";
import { ActionBoard } from "./components/ActionBoard";
import { LiveTape } from "./components/LiveTape";
import { NewsDipPanel } from "./components/NewsDipPanel";
import { SignalPanel } from "./components/SignalPanel";
import { PriceChart } from "./components/PriceChart";
import { ShortTermBookCard } from "./components/ShortTermBookCard";
import { StarterPlanCard } from "./components/StarterPlanCard";
import { fetchLiveQuotes, fetchReport, useStore } from "./store";

export default function App() {
  const {
    report,
    quotes,
    loading,
    error,
    selectedSymbol,
    setReport,
    setQuotes,
    setLoading,
    setError,
    setSelectedSymbol,
  } = useStore();
  const [tapeAt, setTapeAt] = useState<string | null>(null);

  const run = useCallback(async (refresh = true) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchReport(refresh);
      setReport(data);
      if (data.live_quotes?.length) {
        setQuotes(data.live_quotes);
        setTapeAt(new Date().toLocaleTimeString());
      }
      const first =
        data.short_term_book?.legs[0]?.symbol ||
        data.top_buys[0]?.symbol ||
        data.top_sells[0]?.symbol ||
        "BTC";
      setSelectedSymbol(first);
    } catch (e: any) {
      setError(e?.message || "Failed to load signals");
    } finally {
      setLoading(false);
    }
  }, [setError, setLoading, setQuotes, setReport, setSelectedSymbol]);

  const refreshPrices = useCallback(async () => {
    try {
      const q = await fetchLiveQuotes();
      setQuotes(q);
      setTapeAt(new Date().toLocaleTimeString());
    } catch {
      /* keep last tape */
    }
  }, [setQuotes]);

  useEffect(() => {
    run(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const id = window.setInterval(refreshPrices, 12000);
    refreshPrices();
    return () => window.clearInterval(id);
  }, [refreshPrices]);

  const fg = report?.fear_greed_index;
  const sent = report?.overall_sentiment ?? 0;
  const tape = quotes.length ? quotes : report?.live_quotes || [];

  return (
    <div className="app">
      <header className="brand-bar">
        <div>
          <div className="brand">
            Crypto<span>Signal</span>
          </div>
        </div>
        <div className="brand-meta">
          <div>€100 live buy / sell desk</div>
          <div>{report?.generated_at ? new Date(report.generated_at).toLocaleString() : "—"}</div>
          <div className="hero-actions">
            <button className="btn btn-primary" onClick={() => run(true)} disabled={loading}>
              {loading ? "Rebuilding plan…" : "Rebuild plan"}
            </button>
            <button className="btn btn-ghost" onClick={refreshPrices} disabled={loading}>
              Refresh prices
            </button>
          </div>
        </div>
      </header>

      {error && <div className="error">{error}. Start the API on port 8000 (`python -m uvicorn app.main:app --port 8000`).</div>}
      {loading && !report && (
        <div className="loading loading-pulse">Fetching live prices and building the €100 plan…</div>
      )}

      <LiveTape quotes={tape} onSelect={setSelectedSymbol} selected={selectedSymbol} updatedAt={tapeAt} />

      {report && (
        <>
          <section className="summary">
            <div className="summary-copy">
              <h2>Market pulse</h2>
              <p>{report.market_summary}</p>
              {report.risk_notes?.length > 0 && (
                <div className="risk-box">
                  {report.risk_notes.map((n) => (
                    <div key={n}>{n}</div>
                  ))}
                </div>
              )}
            </div>
            <div className="stats">
              <div className="stat">
                <div className="label">Fear & Greed</div>
                <div className={`value ${fg != null && fg >= 50 ? "pos" : "neg"}`}>
                  {fg ?? "—"}
                </div>
              </div>
              <div className="stat">
                <div className="label">Sentiment</div>
                <div className={`value ${sent >= 0 ? "pos" : "neg"}`}>
                  {sent >= 0 ? "+" : ""}
                  {sent.toFixed(2)}
                </div>
              </div>
              <div className="stat">
                <div className="label">Buy candidates</div>
                <div className="value pos">{report.top_buys.length}</div>
              </div>
              <div className="stat">
                <div className="label">Sell / short</div>
                <div className="value neg">
                  {report.top_sells.length}/{report.top_shorts.length}
                </div>
              </div>
            </div>
          </section>

          {report.short_term_book && (
            <ActionBoard book={report.short_term_book} quotes={tape} onSelect={setSelectedSymbol} />
          )}
          {report.starter_plan && <StarterPlanCard plan={report.starter_plan} />}
          {report.short_term_book && <ShortTermBookCard book={report.short_term_book} />}
          {report.news_context && <NewsDipPanel context={report.news_context} />}

          <div className="grid-3">
            <SignalPanel title="Top buys" kind="buy" signals={report.top_buys} onSelect={setSelectedSymbol} />
            <SignalPanel title="Top sells" kind="sell" signals={report.top_sells} onSelect={setSelectedSymbol} />
            <SignalPanel title="Top shorts" kind="short" signals={report.top_shorts} onSelect={setSelectedSymbol} />
          </div>

          {selectedSymbol && (
            <section className="chart-section">
              <h3>{selectedSymbol} — daily candles</h3>
              <PriceChart symbol={selectedSymbol} />
            </section>
          )}

          <footer className="disclaimer">{report.disclaimer}</footer>
        </>
      )}
    </div>
  );
}
