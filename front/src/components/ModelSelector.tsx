import type { AvailableModel } from "../api/types";

/**
 * Feature 057: race-detail model switcher. Lists the models that have a persisted prediction for
 * this race (server-provided `available_models`, active-first order). The active/adopted model is
 * always marked "（採用）" in the option AND a badge states whether the model currently on screen is
 * the adopted one (selected ≠ adopted). Purely a read selector — switching just re-fetches.
 */
export function ModelSelector({
  models,
  selected,
  onChange,
}: {
  models: AvailableModel[];
  selected: string | undefined;
  onChange: (modelVersion: string) => void;
}) {
  if (models.length === 0) return null;

  const current =
    models.find((m) => m.model_version === selected) ??
    models.find((m) => m.is_selected) ??
    models[0];
  const viewingAdopted = current.adoption_status === "active";
  const label = (m: AvailableModel) =>
    `${m.display_name ?? m.model_version}${m.adoption_status === "active" ? "（採用）" : ""}`;

  return (
    <div className="model-selector" data-testid="model-selector">
      <label htmlFor="model-select">予測モデル：</label>
      {models.length >= 2 ? (
        <select
          id="model-select"
          data-testid="model-select"
          value={current.model_version}
          onChange={(e) => onChange(e.target.value)}
        >
          {models.map((m) => (
            <option key={m.model_version} value={m.model_version}>
              {label(m)}
            </option>
          ))}
        </select>
      ) : (
        <span data-testid="model-single">{label(current)}</span>
      )}
      {viewingAdopted ? (
        <span className="badge badge--adopted" data-testid="viewing-adopted">
          採用モデルを表示中
        </span>
      ) : (
        <span className="badge badge--other" data-testid="viewing-non-adopted">
          非採用モデルを表示中
        </span>
      )}
      {current.purpose && (
        <span className="model-purpose" data-testid="model-purpose">
          {current.purpose}
        </span>
      )}
    </div>
  );
}
