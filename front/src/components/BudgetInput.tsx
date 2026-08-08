import { useState } from "react";
import type { FormEvent } from "react";

import { formatYen, validateBudget } from "../lib/budget";

/**
 * Feature 087 (FR-001/005): the race-budget input. CONTROLLED — this component never calls
 * useBudget() itself (single-owner rule, codex H3): the panel owns the state and passes it down.
 * Acceptance is validateBudget (≥¥100 positive integer; ¥550 is accepted un-rounded — step=100
 * is only an input hint). The budget lives in the browser only and is never sent to the server.
 */
export function BudgetInput({
  budget,
  onBudgetChange,
}: {
  budget: number | null;
  onBudgetChange: (value: number) => void;
}) {
  const [text, setText] = useState<string>(budget === null ? "" : String(budget));
  const [invalid, setInvalid] = useState(false);

  function submit(e: FormEvent) {
    e.preventDefault();
    const valid = validateBudget(text);
    if (valid === null) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    onBudgetChange(valid);
  }

  return (
    <form className="betslip__budget" onSubmit={submit} data-testid="budget-input">
      <label htmlFor="race-budget">このレースの予算</label>
      <input
        id="race-budget"
        type="number"
        inputMode="numeric"
        min={100}
        step={100}
        value={text}
        onChange={(e) => setText(e.target.value)}
        aria-describedby={invalid ? "race-budget-error" : undefined}
      />
      <button type="submit">設定</button>
      {budget !== null ? (
        <span className="betslip__budget-hint">現在: {formatYen(budget)}</span>
      ) : null}
      {invalid ? (
        <p id="race-budget-error" className="betslip__budget-hint" data-testid="budget-invalid">
          予算は 100 円以上の整数で入力してください
        </p>
      ) : null}
      {budget === null ? (
        <p className="betslip__budget-hint" data-testid="budget-unset-hint">
          予算を設定すると各買い目の金額が円で表示されます(未設定の間は比率表示)
        </p>
      ) : null}
    </form>
  );
}
