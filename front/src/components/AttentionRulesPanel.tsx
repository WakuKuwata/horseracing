import type { ReactNode } from "react";

import { useAttentionRules } from "../api/queries";
import type {
  AttentionFrozenStats,
  AttentionJudgedFreshness,
  AttentionProspective,
  AttentionSelectedCalibration,
  CheckpointDecision,
  RuleSummary,
} from "../api/types";
import {
  backtestLevelLabel,
  DECISION_LABELS,
  EXCLUSION_LABELS,
  EXCLUSION_ORDER,
  stageLabel,
  type RoiBasis,
} from "../lib/attention";
import { formatJstDateTime, PLACEHOLDER } from "../lib/format";
import { formatCi, formatCount, formatP, formatRoi, RoiValue } from "./AttentionRoi";
import { PseudoValue } from "./PseudoValue";

/**
 * Feature 138 (T036): 注目条件の一覧(S1〜S5)。
 *
 * 凍結した過去検証・価格ずれ試験と、前向き検証の現況(読み取り時計算)を条件ごとに並べる。
 * 値はすべて `GET /attention-rules` の転記で、画面側で集計・判定しない。
 *
 * 表示規律(spec FR-008・FR-012・「やってはいけない操作」):
 * - 並びは順位の固定順だけ(成績・強調レベル・回収率・期待回収率で並べ替えない)。
 * - 「不通過」「判定保留」の条件も消さずに、段階名と理由で示す。
 * - 回収率の数値(`data-kind="roi"`)には必ず計算基準のラベル(`ROI_BASIS_LABELS`)を同じ要素内に添える。
 * - 損益色・購入誘導語を使わない。勝率とその推定値は出さない(期待回収率の単位だけ)。
 * - 価格ずれ試験は強弱の語を使わず、数値(回収率・点数・重なり)で見せる。
 */

/** 判断時鮮度帯(発走時刻 − 判断時のオッズ取得時刻)の表示語。描画時の「価格鮮度」とは別の軸。 */
const JUDGED_FRESHNESS_ROWS: { key: keyof AttentionJudgedFreshness; label: string }[] = [
  { key: "<=10m", label: "10 分以内" },
  { key: "<=60m", label: "10 分超・60 分以内" },
  { key: ">60m", label: "60 分超" },
];

// --- 数値の書式 -------------------------------------------------------------------------------------

/**
 * 回収率の数値ノード。値(と区間)と計算基準のラベルを**同じ要素**に置く(FR-008 の不変テスト)。
 * 描画は注目条件の共通経路 `RoiValue` に任せる。値が無い(集計 0 点など)ときも基準のラベルは添える。
 */
function Roi({
  value,
  ci,
  basis,
}: {
  value: number | null | undefined;
  ci?: readonly [number, number] | null;
  basis: RoiBasis;
}) {
  const ciText = formatCi(ci);
  return (
    <RoiValue basis={basis}>
      {formatRoi(value)}
      {ciText ? `(${ciText})` : null}
    </RoiValue>
  );
}

function Row({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <div className="attn-rule__row" data-testid={testId}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

// --- 過去検証 ----------------------------------------------------------------------------------------

function BacktestWindow({
  label,
  stats,
  testId,
}: {
  label: string;
  stats: AttentionFrozenStats;
  testId: string;
}) {
  return (
    <p className="attn-rule__line" data-testid={testId}>
      {label}{" "}
      <Roi value={stats.roi} ci={[stats.ci_low, stats.ci_high]} basis="closing_odds_approx" />
      ・{formatCount(stats.n)} 点・{formatCount(stats.hits)} 的中・{formatP(stats.p_one_sided)}
    </p>
  );
}

function SelectedLine({
  label,
  sel,
  testId,
}: {
  label: string;
  sel: AttentionSelectedCalibration;
  testId: string;
}) {
  return (
    <p className="attn-rule__line" data-testid={testId}>
      {label} 選ばれた馬の期待回収率の平均{" "}
      <span data-kind="expected_return">
        <PseudoValue kind="expected_return">{formatRoi(sel.mean_ev)}</PseudoValue>
      </span>
      (実際の回収率 <Roi value={sel.realized_roi} basis="closing_odds_approx" />・
      {formatCount(sel.n)} 点)
    </p>
  );
}

function Backtest({ rule }: { rule: RuleSummary }) {
  const bt = rule.backtest;
  const [y24, y25, y26] = bt.bets_2024_25_26;
  return (
    <Row label="過去検証" testId={`attention-rule-backtest-${rule.id}`}>
      <BacktestWindow
        label="通算 2010〜26"
        stats={bt.all}
        testId={`attention-rule-backtest-all-${rule.id}`}
      />
      <BacktestWindow
        label="確認窓 2019〜26"
        stats={bt.c}
        testId={`attention-rule-backtest-c-${rule.id}`}
      />
      <p className="attn-rule__line">
        年間点数 2024 年 {y24 ?? PLACEHOLDER}・2025 年 {y25 ?? PLACEHOLDER}・2026 年(途中){" "}
        {y26 ?? PLACEHOLDER}
      </p>
      <SelectedLine
        label="通算:"
        sel={bt.selected.all}
        testId={`attention-rule-selected-all-${rule.id}`}
      />
      <SelectedLine
        label="確認窓:"
        sel={bt.selected.c}
        testId={`attention-rule-selected-c-${rule.id}`}
      />
      <p className="attn-rule__line attn-rule__meta">
        区間と p 値は開催日クラスタ bootstrap(B={formatCount(bt.bootstrap.b)}・seed{" "}
        {bt.bootstrap.seed})の片側検定で、<strong>多重探索の補正なし</strong>。確定オッズ × 100 円の
        近似精算(同着除外)。
      </p>
      <p
        className="attn-rule__line"
        data-kind="criterion"
        data-testid={`attention-rule-backtest-level-${rule.id}`}
      >
        過去検証の軸: {backtestLevelLabel(rule.levels.backtest)}
      </p>
    </Row>
  );
}

// --- 価格ずれ試験 ------------------------------------------------------------------------------------

function PriceNoise({ rule }: { rule: RuleSummary }) {
  const base = rule.backtest.all.n;
  return (
    <Row label="価格ずれ試験(代理)" testId={`attention-rule-price-noise-${rule.id}`}>
      <p className="attn-rule__line attn-rule__meta">
        過去 2010〜26・モデルの推定は固定し、選定に使う単勝オッズだけを対数正規のずれで動かした代理実験
        (精算はずらす前の確定オッズ近似 × 100 円・10 反復の平均)。
      </p>
      <table className="data-table attn-rule__table">
        <thead>
          <tr>
            <th>ずれ</th>
            <th className="num">回収率</th>
            <th className="num">選ばれる点数</th>
            <th className="num">ずれ無しの選定との重なり</th>
          </tr>
        </thead>
        <tbody>
          {rule.price_noise.map((n) => (
            <tr key={n.sigma} data-testid={`attention-price-noise-${rule.id}-${n.sigma}`}>
              <td>
                <span data-kind="sigma">{`σ=${n.sigma}(ずれ ${Math.round(n.sigma * 100)}%)`}</span>
              </td>
              <td className="num">
                <Roi value={n.roi} basis="closing_odds_approx" />
              </td>
              <td className="num">
                {formatCount(n.n)} 点
                {base > 0 ? `(ずれ無しの ${(n.n / base).toFixed(1)} 倍)` : null}
              </td>
              <td className="num">
                <span data-kind="overlap">{`${Math.round(n.overlap * 100)}%`}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Row>
  );
}

// --- 前向き検証 --------------------------------------------------------------------------------------

function DecisionItem({ d }: { d: CheckpointDecision }) {
  return (
    <li data-testid={`attention-decision-${d.checkpoint}`}>
      {d.checkpoint} 点: {DECISION_LABELS[d.decision]}(判定{" "}
      {formatJstDateTime(d.decided_at) ?? PLACEHOLDER})・{formatCount(d.n_counted)} 点・
      {formatCount(d.n_hits)} 的中・回収率 <Roi value={d.roi_frozen} ci={d.ci} basis="frozen_pick_odds" />
      ・材料の締切 {formatJstDateTime(d.settlement_cutoff) ?? PLACEHOLDER}・判定時の集計開始日{" "}
      {d.prospective_start_date}・材料の前で結果未確定 {formatCount(d.skipped_pending_before_last)} 点
    </li>
  );
}

function stageReason(p: AttentionProspective): string | null {
  if (p.stage === "failed") {
    return `${p.checkpoint ?? ""} 点時点で区間の上限が 100% 未満でした。出走表のチップは外さず、主ラベルを段階名にして強調を最弱に固定します。`;
  }
  if (p.stage === "undecided") {
    return "600 点時点でも区間が 100% を跨いだため判定保留(検出力不足)で、以後は自動更新しません。";
  }
  if (p.checkpoint_pending) {
    return "判定待ち: 集計がチェックポイントに達しましたが、判定の記録がまだありません(発走から 3 日たった分だけを材料にします)。";
  }
  return null;
}

function Counts({ rule }: { rule: RuleSummary }) {
  const p = rule.prospective;
  const excluded = EXCLUSION_ORDER.reduce((sum, k) => sum + p.counts[k], 0);
  return (
    <>
      <table className="data-table attn-rule__table" data-testid={`attention-counts-${rule.id}`}>
        <thead>
          <tr>
            <th>分類</th>
            <th className="num">件数</th>
          </tr>
        </thead>
        <tbody>
          <tr data-testid={`attention-count-${rule.id}-counted`}>
            <td>集計対象</td>
            <td className="num">{formatCount(p.n_counted)}</td>
          </tr>
          {EXCLUSION_ORDER.map((k) => (
            <tr key={k} data-testid={`attention-count-${rule.id}-${k}`}>
              <td>集計外: {EXCLUSION_LABELS[k]}</td>
              <td className="num">{formatCount(p.counts[k])}</td>
            </tr>
          ))}
          <tr data-testid={`attention-count-${rule.id}-total`}>
            <td>合計(保存した該当の総数)</td>
            <td className="num">{formatCount(p.n_picks_total)}</td>
          </tr>
        </tbody>
      </table>
      <p className="attn-rule__line attn-rule__meta">
        集計対象 {formatCount(p.n_counted)} + 集計外 {formatCount(excluded)} = 合計{" "}
        {formatCount(p.n_picks_total)}
      </p>
      <p className="attn-rule__line" data-testid={`attention-flag-${rule.id}`}>
        監査フラグ 出走馬変更: {formatCount(p.flags.field_changed_after_pick)} 件(判断の後に出走馬が
        変わったレースの該当。集計対象にも数え、上の合計には足しません)
      </p>
    </>
  );
}

function JudgedFreshness({ rule }: { rule: RuleSummary }) {
  const bands = rule.prospective.by_judged_freshness;
  return (
    <>
      <p className="attn-rule__line attn-rule__meta">
        判断時鮮度帯(発走時刻 − 判断時のオッズ取得時刻)別の集計対象。区切りは経過時間の約束で、
        この間にオッズがどれだけ動くかは測っていません。
      </p>
      <table className="data-table attn-rule__table" data-testid={`attention-freshness-${rule.id}`}>
        <thead>
          <tr>
            <th>判断時鮮度帯</th>
            <th className="num">点数</th>
            <th className="num">的中</th>
            <th className="num">回収率</th>
          </tr>
        </thead>
        <tbody>
          {JUDGED_FRESHNESS_ROWS.map(({ key, label }) => (
            <tr key={key} data-testid={`attention-freshness-${rule.id}-${key}`}>
              <td>{label}</td>
              <td className="num">{formatCount(bands[key].n)}</td>
              <td className="num">{formatCount(bands[key].hits)}</td>
              <td className="num">
                <Roi value={bands[key].roi_frozen} basis="frozen_pick_odds" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function fmtLog(value: number | null): string {
  return value === null ? PLACEHOLDER : value.toFixed(3);
}

function OddsDrift({ rule }: { rule: RuleSummary }) {
  const d = rule.prospective.odds_drift;
  return (
    <p className="attn-rule__line" data-testid={`attention-odds-drift-${rule.id}`}>
      判断時オッズと保存オッズの比(診断・log(保存 ÷ 判断時)):{" "}
      {d.n === 0
        ? "対象なし"
        : `${formatCount(d.n)} 点・中央値 ${fmtLog(d.median_log_ratio)}・10 パーセント点 ${fmtLog(
            d.p10,
          )}・90 パーセント点 ${fmtLog(d.p90)}`}
    </p>
  );
}

function Prospective({ rule }: { rule: RuleSummary }) {
  const p = rule.prospective;
  const label = stageLabel({
    stage: p.stage,
    checkpoint: p.checkpoint,
    checkpoint_pending: p.checkpoint_pending,
  });
  const reason = stageReason(p);
  return (
    <Row label="前向き検証" testId={`attention-rule-prospective-${rule.id}`}>
      <p className="attn-rule__line">
        段階:{" "}
        <strong className="attn-rule__stage" data-testid={`attention-rule-stage-${rule.id}`}>
          {label}
        </strong>
      </p>
      {reason && (
        <p
          className="attn-rule__line"
          data-kind="criterion"
          data-testid={`attention-rule-stage-reason-${rule.id}`}
        >
          {reason}
        </p>
      )}
      <p className="attn-rule__line" data-testid={`attention-rule-start-${rule.id}`}>
        集計開始 {p.start_date ?? "集計開始前(集計開始日は未設定)"}・集計方針 {p.policy_version}
      </p>
      <p className="attn-rule__line" data-testid={`attention-rule-tally-${rule.id}`}>
        集計 {formatCount(p.n_counted)} 点・{formatCount(p.n_hits)} 的中・次のチェックポイント{" "}
        {p.next_checkpoint === null
          ? "なし(判定は終了)"
          : `${p.next_checkpoint} 点(あと ${formatCount(p.remaining_to_next ?? 0)} 点)`}
      </p>
      <p className="attn-rule__line" data-testid={`attention-rule-frozen-${rule.id}`}>
        回収率(段階判定の基準) <Roi value={p.frozen.roi} ci={p.frozen.ci} basis="frozen_pick_odds" />
        {p.frozen.p_one_sided !== null ? `・${formatP(p.frozen.p_one_sided)}` : null}
      </p>
      <p className="attn-rule__line" data-testid={`attention-rule-stored-${rule.id}`}>
        参考の回収率 <Roi value={p.stored.roi} ci={p.stored.ci} basis="stored_odds_mutable" />・
        {formatCount(p.stored.n)} 点(保存オッズの無い {formatCount(p.stored.n_missing_stored_odds)}{" "}
        点は除く。保存オッズは再取込で変わることがあります)
      </p>
      <p className="attn-rule__line attn-rule__meta">
        区間は開催日クラスタ bootstrap(B={formatCount(p.bootstrap.b)}・seed {p.bootstrap.seed})。
        公式の単勝払戻は保存していないため、どちらの回収率も近似です。
      </p>
      <div className="attn-rule__line" data-testid={`attention-rule-decisions-${rule.id}`}>
        判定記録:{" "}
        {p.decisions.length === 0 ? (
          "まだありません"
        ) : (
          <ul className="attn-rule__decisions">
            {p.decisions.map((d) => (
              <DecisionItem key={d.checkpoint} d={d} />
            ))}
          </ul>
        )}
      </div>
      <Counts rule={rule} />
      <JudgedFreshness rule={rule} />
      <OddsDrift rule={rule} />
    </Row>
  );
}

// --- 1 条件 ------------------------------------------------------------------------------------------

function RuleBadges({ rule }: { rule: RuleSummary }) {
  return (
    <>
      {rule.posthoc && (
        <span className="attn-badge" data-testid={`attention-badge-posthoc-${rule.id}`}>
          探索後固定
        </span>
      )}
      {rule.posthoc && rule.prospective.stage !== "passed" && (
        <span className="attn-badge" data-testid={`attention-badge-unconfirmed-${rule.id}`}>
          前向き未確認
        </span>
      )}
      {rule.control && (
        <span className="attn-badge" data-testid={`attention-badge-control-${rule.id}`}>
          対照
        </span>
      )}
    </>
  );
}

function RuleSection({ rule }: { rule: RuleSummary }) {
  return (
    <section
      className="attn-rule"
      data-testid={`attention-rule-${rule.id}`}
      data-rule-id={rule.id}
    >
      <h3 className="attn-rule__title">
        {rule.control ? `対照 ${rule.id}` : `注目条件 ${rule.id}`} <RuleBadges rule={rule} />
      </h3>
      <p className="attn-rule__definition" data-kind="definition">
        定義: {rule.definition_ja}
      </p>
      {rule.control && (
        <p className="attn-rule__line attn-rule__meta">
          対照条件(137 の現行の条件・単 seed)。前向き検証の比較のために残し、出走表にチップは出しません。
        </p>
      )}
      {rule.posthoc && (
        <p className="attn-rule__line attn-rule__meta">
          {rule.id} は結果を見てから見つけた条件を固定したもの(探索後固定)で、前向きにはまだ確認していません。
        </p>
      )}
      <dl className="attn-rule__rows">
        <Backtest rule={rule} />
        <PriceNoise rule={rule} />
        <Prospective rule={rule} />
      </dl>
    </section>
  );
}

/** 一覧の本体(取得済みの応答から描く)。並びは順位の固定順。 */
export function AttentionRulesList({
  rules,
  disclaimer,
}: {
  rules: RuleSummary[];
  disclaimer: string;
}) {
  // The API already returns rank order; re-assert the FIXED rank order (never by results).
  const ordered = [...rules].sort((a, b) => a.rank - b.rank);
  return (
    <div className="attn-rules__body">
      <p className="note">
        順位は固定で、成績による並び替えはしません。前向き検証が「不通過」「判定保留」になった条件も
        表示を続けます。回収率はいずれも近似で、計算基準を〔 〕で添えています。
      </p>
      {ordered.map((rule) => (
        <RuleSection key={rule.id} rule={rule} />
      ))}
      <p className="note" data-testid="attention-rules-disclaimer">
        {disclaimer}
      </p>
    </div>
  );
}

/**
 * 注目条件の一覧(折りたたみ)。レース詳細(table-hint の後)と専用ページ `/attention`(開いた状態)で使う。
 * 取得の失敗は中立の文で示す(ページの主内容を奪わない)。
 */
export function AttentionRulesPanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const { data, error } = useAttentionRules();
  let body: ReactNode;
  if (error) {
    body = (
      <p className="note" data-testid="attention-rules-error">
        注目条件の一覧を取得できませんでした(HTTP {error.status} {error.code})
      </p>
    );
  } else if (!data) {
    body = (
      <p className="note" data-testid="attention-rules-loading">
        注目条件の一覧を読み込み中…
      </p>
    );
  } else {
    body = <AttentionRulesList rules={data.items} disclaimer={data.disclaimer} />;
  }
  return (
    <details className="attn-rules" data-testid="attention-rules-panel" open={defaultOpen}>
      <summary className="attn-rules__title">
        注目条件の一覧(S1〜S5・過去検証と前向き検証の現況)
      </summary>
      {body}
    </details>
  );
}
