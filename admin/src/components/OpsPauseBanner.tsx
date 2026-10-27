import { usePauseState } from "../api/opsStatus";

export const PAUSE_SHORT = "データ取得を停止中のため受け付けていません";

/**
 * B5: header banner for the 172 pause. Shown while ops reports `collection_paused`; a second,
 * separate line when the policy file says paused but the running ops process does not enforce it
 * (it was started without the wrapper) — the two facts must not be merged into one claim.
 */
export function OpsPauseBanner() {
  const { paused, mismatch } = usePauseState();
  if (!paused && !mismatch) return null;
  return (
    <span className="app__banner app__banner--pause" role="status" data-testid="ops-pause-banner">
      {paused
        ? "データ取得停止中(collection_paused): 範囲更新などの書き込みは受け付けません"
        : "設定ファイルは取得停止ですが、この ops プロセスは停止 wrapper を経由せず書き込みを受け付けています"}
    </span>
  );
}
