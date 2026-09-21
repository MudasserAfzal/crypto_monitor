import { create } from "zustand";

export type SignalType = "BUY" | "SELL" | "HOLD";
export type Direction = "LONG" | "SHORT" | "NONE";

export interface TradeSignal {
  symbol: string;
  primary_signal: SignalType;
  secondary_signal: Direction;
  confidence: number;
  technical_score: number;
  sentiment_score: number;
  onchain_score: number;
  volume_score: number;
  macro_score: number;
  composite_score: number;
  entry_price?: number | null;
  take_profit?: number | null;
  stop_loss?: number | null;
  risk_reward?: number | null;
  leverage_suggestion?: number | null;
  portfolio_allocation_pct?: number | null;
  time_horizon: string;
  timeframe: string;
  rationale?: string | null;
}

export interface NewsHeadline {
  title: string;
  url?: string | null;
  category: string;
  sentiment?: number | null;
  published_at?: string | null;
  source: string;
}

export interface DipAnalog {
  date: string;
  symbol: string;
  drop_pct: number;
  headline: string;
  category: string;
  bounce_7d_pct?: number | null;
  bounce_30d_pct?: number | null;
  lesson: string;
  typical_action: string;
}

export interface DipEvent {
  date: string;
  symbol: string;
  drop_pct: number;
  bounce_7d_pct?: number | null;
  matched_event?: string | null;
  news_type?: string | null;
}

export interface StarterPlan {
  budget_eur: number;
  action: string;
  symbol: string;
  venue: string;
  pair: string;
  deploy_now_eur: number;
  reserve_eur: number;
  entry_price_eur?: number | null;
  entry_note: string;
  stop_loss_eur?: number | null;
  stop_loss_pct?: number | null;
  risk_eur?: number | null;
  take_profit_eur?: number | null;
  take_profit_pct?: number | null;
  coins_if_filled?: number | null;
  time_horizon: string;
  why: string;
  invalidation: string;
  sell_rule: string;
}

export interface NewsDipContext {
  dominant_category: string;
  headline_mix: Record<string, number>;
  current_headlines: NewsHeadline[];
  recent_dips: DipEvent[];
  historical_analogs: DipAnalog[];
  playbook: string;
  verdict: string;
}

export interface TradeLeg {
  symbol: string;
  role: string;
  rank: number;
  action: string;
  alloc_eur: number;
  pair: string;
  venue: string;
  last_eur?: number | null;
  bounce_7d_pct?: number | null;
  entry_price_eur?: number | null;
  stop_loss_eur?: number | null;
  stop_loss_pct?: number | null;
  take_profit_eur?: number | null;
  take_profit_pct?: number | null;
  coins_if_filled?: number | null;
  news_analog: string;
  why: string;
}

export interface ShortTermBook {
  thesis: string;
  budget_eur: number;
  deploy_now_eur: number;
  reserve_eur: number;
  legs: TradeLeg[];
  skipped: string[];
}

export interface LiveQuote {
  symbol: string;
  price_eur: number;
  price_usd: number;
  change_24h_pct: number;
  action_now: string;
  vs_limit_pct?: number | null;
  vs_stop_pct?: number | null;
  vs_tp_pct?: number | null;
}

export interface DailyReport {
  generated_at: string;
  market_summary: string;
  overall_sentiment: number;
  fear_greed_index?: number | null;
  top_buys: TradeSignal[];
  top_sells: TradeSignal[];
  top_shorts: TradeSignal[];
  risk_notes: string[];
  portfolio_suggestions: Record<string, number>;
  news_context?: NewsDipContext | null;
  starter_plan?: StarterPlan | null;
  short_term_book?: ShortTermBook | null;
  live_quotes?: LiveQuote[];
  disclaimer: string;
}

interface AppState {
  report: DailyReport | null;
  quotes: LiveQuote[];
  loading: boolean;
  error: string | null;
  selectedSymbol: string | null;
  setReport: (r: DailyReport) => void;
  setQuotes: (q: LiveQuote[]) => void;
  setLoading: (v: boolean) => void;
  setError: (e: string | null) => void;
  setSelectedSymbol: (s: string | null) => void;
}

export const useStore = create<AppState>((set) => ({
  report: null,
  quotes: [],
  loading: false,
  error: null,
  selectedSymbol: null,
  setReport: (report) => set({ report }),
  setQuotes: (quotes) => set({ quotes }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  setSelectedSymbol: (selectedSymbol) => set({ selectedSymbol }),
}));

const API = import.meta.env.VITE_API_URL || "";

export async function fetchReport(refresh = false, budgetEur = 100): Promise<DailyReport> {
  const q = new URLSearchParams();
  if (refresh) q.set("refresh", "true");
  q.set("budget_eur", String(budgetEur));
  const res = await fetch(`${API}/api/v1/signals/report?${q.toString()}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchOhlcv(symbol: string, timeframe = "1d") {
  const res = await fetch(`${API}/api/v1/market/ohlcv/${symbol}?timeframe=${timeframe}&limit=200`);
  if (!res.ok) throw new Error(`OHLCV error ${res.status}`);
  return res.json();
}

export async function fetchLiveQuotes(): Promise<LiveQuote[]> {
  const res = await fetch(`${API}/api/v1/market/live`);
  if (!res.ok) throw new Error(`Live prices error ${res.status}`);
  return res.json();
}
