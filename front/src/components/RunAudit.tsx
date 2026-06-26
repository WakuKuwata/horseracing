import type { RunAudit } from "../api/types";
import { formatDateTime } from "../lib/format";

/**
 * Surfaces the deterministic prediction_run selection (constitution V auditability): the API picks
 * active → computed_at DESC → run_id tie-break and returns the chosen run; we always show WHICH run.
 */
export function RunAuditView({ run }: { run: RunAudit }) {
  return (
    <div className="audit">
      <span>
        予測ラン: <code>{run.prediction_run_id}</code>
      </span>
      <span>
        ロジック版: <code>{run.logic_version}</code>
      </span>
      <span>
        モデル版: <code>{run.model_version}</code>
      </span>
      <span>
        算出時刻: <code>{formatDateTime(run.computed_at)}</code>
      </span>
    </div>
  );
}
