import type { StarterPlan } from "../store";

function money(n?: number | null, digits = 0) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-IE", { maximumFractionDigits: digits });
}

const ACTION_LABEL: Record<string, string> = {
  BUY_NOW: "Buy now (partial)",
  WAIT_PULLBACK: "Wait for pullback",
  WAIT: "Wait",
  AVOID: "Do not buy",
  SELL: "Do not add / trim",
};

interface Props {
  plan: StarterPlan;
}

export function StarterPlanCard({ plan }: Props) {
  const actionClass =
    plan.action === "BUY_NOW" ? "pos" : plan.action === "AVOID" || plan.action === "SELL" ? "neg" : "hold";

  return (
    <section className="starter">
      <div className="starter-head">
        <h2>€{money(plan.budget_eur, 0)} starter ticket</h2>
        <div className={`starter-action ${actionClass}`}>{ACTION_LABEL[plan.action] || plan.action}</div>
      </div>
      <p className="starter-why">{plan.entry_note}</p>

      <div className="starter-grid">
        <div>
          <div className="label">Coin</div>
          <div className="value">{plan.symbol}</div>
          <div className="hint">{plan.pair}</div>
        </div>
        <div>
          <div className="label">Where</div>
          <div className="venue">{plan.venue}</div>
        </div>
        <div>
          <div className="label">Buy limit</div>
          <div className="value">€{money(plan.entry_price_eur, 0)}</div>
          <div className="hint">
            Deploy €{money(plan.deploy_now_eur, 0)} now · keep €{money(plan.reserve_eur, 0)}
          </div>
        </div>
        <div>
          <div className="label">Stop loss</div>
          <div className="value neg">€{money(plan.stop_loss_eur, 0)}</div>
          <div className="hint">
            {plan.stop_loss_pct}% · risk about €{money(plan.risk_eur, 0)}
          </div>
        </div>
        <div>
          <div className="label">Take profit</div>
          <div className="value pos">€{money(plan.take_profit_eur, 0)}</div>
          <div className="hint">{plan.take_profit_pct}%</div>
        </div>
        <div>
          <div className="label">If filled</div>
          <div className="value">{plan.coins_if_filled}</div>
          <div className="hint">{plan.time_horizon}</div>
        </div>
      </div>

      <div className="starter-rules">
        <div>
          <strong>Why</strong>
          <p>{plan.why}</p>
        </div>
        <div>
          <strong>When to sell</strong>
          <p>{plan.sell_rule}</p>
        </div>
        <div>
          <strong>Invalidation</strong>
          <p>{plan.invalidation}</p>
        </div>
      </div>
    </section>
  );
}
