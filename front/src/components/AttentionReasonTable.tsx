import type { AttentionHorse, ReasonComponent, ReasonValue, RuleId } from "../api/types";
import {
  alsoApplicableText,
  componentValueText,
  criterionText,
  REASON_STATUS_GLYPHS,
  REASON_STATUS_LABELS,
  reasonStatusKey,
  reasonSummaryText,
} from "../lib/attention";
import { formatJstDateTime } from "../lib/format";
import { PseudoValue } from "./PseudoValue";

/**
 * Feature 177: 「この馬がなぜその条件に該当するか」— 条件の部品ごとに「条件 / 判断時点 / 現在値」
 * を並べる照合表と一文の要約(contracts/display.md)。
 *
 * 材料はすべて API の `AttentionHorse.reason`(部品の条件値・値・適合は eval の同じ判定関数が
 * 総合判定と同時に生成)。**front は適合を導き直さず、閾値を定数に持たない。** 主役は判断時点
 * (レースの最初の計算)で満たした事実 — チップはそこで固定され、オッズの変化で付け外ししない。
 * 現在値の列は「いまも満たしているか」の補足。間隔(前走から何日)は判断時点に凍結した値で、
 * 描画時刻で増えないので現在値の列には同じ値を「判断時点で固定」と添える。
 *
 * 適合は記号と語の両方(記号は `aria-hidden`、語は常に読み上げ)。損益色なし・並べ替えなし・
 * 満たさない行を隠さない。「最新の計算なし」(計算そのものが無い)と「値なし」(入力が欠けた)は
 * 別の語。`status="unavailable"`(応答の整合が取れない=実装の欠陥・API がログに出す)は表も要約も
 * 出さず、その事実だけを書く。
 */
export function AttentionReasonTable({
  horse,
  judgedAt,
  controlIds,
}: {
  horse: AttentionHorse;
  /** レースの最初の計算の時刻(ISO)。列見出しと要約文に JST で出す。 */
  judgedAt: string | null;
  /** 対照の条件 id(一覧の `control` から。未取得なら空)。 */
  controlIds: readonly RuleId[];
}) {
  const reason = horse.reason;
  if (!reason) return null;
  if (reason.status === "unavailable") {
    return (
      <div className="attn-reason" data-testid="attention-reason">
        <p className="attn-panel__note" data-testid="attention-reason-unavailable">
          該当理由を表示できません(応答の整合が取れていません)
        </p>
      </div>
    );
  }
  const judgedAtText = formatJstDateTime(judgedAt);
  const currentAtText = formatJstDateTime(horse.current?.computed_at);
  const hasCurrent = horse.current != null;
  const also = alsoApplicableText(reason, horse.stages, controlIds);

  return (
    <div className="attn-reason" data-testid="attention-reason">
      <p className="attn-reason__summary" data-testid="attention-reason-summary">
        {reasonSummaryText(reason, judgedAtText)}
      </p>
      <table className="attn-reason__table" data-testid="attention-reason-table">
        <thead>
          <tr>
            <th scope="col">条件</th>
            <th scope="col">判断時点{judgedAtText ? `(${judgedAtText})` : ""}</th>
            <th scope="col">現在値{hasCurrent && currentAtText ? `(${currentAtText})` : ""}</th>
          </tr>
        </thead>
        <tbody>
          {reason.components.map((c) => (
            <tr key={c.kind} data-testid="attention-reason-row" data-component={c.kind}>
              <th scope="row">
                <span data-kind="criterion">{criterionText(c)}</span>
              </th>
              <td>
                <ValueCell component={c} value={c.judged} frozenNote={false} />
              </td>
              <td>
                <ValueCell
                  component={c}
                  value={c.current}
                  frozenNote={c.kind === "days_since_last" && c.current != null}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {also && (
        <p className="attn-panel__note" data-testid="attention-reason-also">
          <span data-kind="criterion">{also}</span>
        </p>
      )}
      <p className="attn-panel__note" data-testid="attention-reason-sources">
        判断時点の値はこのレースの最初の計算で凍結したもの、現在値は最新の計算の値です
      </p>
      {horse.field_changed_after_pick && (
        <p className="attn-panel__note" data-testid="attention-field-changed">
          出走馬がその後変わっています(判断時点の値は変わる前の出走馬で計算したものです)
        </p>
      )}
    </div>
  );
}

/** 値 + 適合(記号は飾り・語が本体)。`value == null` は「最新の計算なし」。 */
function ValueCell({
  component,
  value,
  frozenNote,
}: {
  component: ReasonComponent;
  value: ReasonValue | null | undefined;
  frozenNote: boolean;
}) {
  const key = reasonStatusKey(value);
  const status = (
    <span className="attn-reason__status" data-status={key}>
      <span aria-hidden="true" className="attn-reason__glyph">
        {REASON_STATUS_GLYPHS[key]}
      </span>{" "}
      {REASON_STATUS_LABELS[key]}
    </span>
  );
  if (!value) return status;
  if (value.value == null) return status;
  const shown =
    component.kind === "ens_expected_return" ? (
      <span data-kind="expected_return">
        <PseudoValue kind="expected_return">{componentValueText(component, value.value)}</PseudoValue>
      </span>
    ) : (
      <span>{componentValueText(component, value.value)}</span>
    );
  return (
    <>
      <span className="attn-reason__value">{shown}</span>
      {frozenNote && <span className="attn-panel__note">(判断時点で固定)</span>} {status}
    </>
  );
}
