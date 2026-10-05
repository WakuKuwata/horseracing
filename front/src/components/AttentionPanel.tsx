import type { ReactNode } from "react";

import { useAttentionRules } from "../api/queries";
import type {
  AttentionAvailable,
  AttentionHorse,
  EvSnapshot,
  RuleId,
  RuleSummary,
  StageDetail,
} from "../api/types";
import {
  backtestLevelLabel,
  BUY_TIME_LABEL,
  CHIP_NOW_LABELS,
  chipLabel,
  DECISION_LABELS,
  decisionRoiBasis,
  emphasisLevel,
  EXCLUSION_LABELS,
  freshnessLevel,
  isClosedStage,
  type Level,
  PROSPECTIVE_BASIS_LABELS,
  ruleFlags,
  ruleIdText,
  stageLabel,
} from "../lib/attention";
import { formatJstDateTime, formatNum, formatPct, PLACEHOLDER } from "../lib/format";
import {
  formatCi,
  formatCount,
  formatDuration,
  formatP,
  formatRoi,
  RoiValue,
} from "./AttentionRoi";
import { AttentionProgress } from "./AttentionProgress";
import { BuyTimeExpectationLine } from "./AttentionRulesPanel";
import { PseudoValue } from "./PseudoValue";

/**
 * Feature 138: 出走表の展開行に出す注目条件の内訳(T035)。
 *
 * どの条件に該当するか・チップの条件・段階は API の値をそのまま使い、**導き直さない**。
 * front がここで決めるのは価格鮮度(最新の計算のオッズ取得時刻 × 描画時刻)と強調レベル
 * (4 軸の最小値)だけで、どちらも `lib/attention.ts` の関数を呼ぶ。
 *
 * 凍結した過去検証・価格ずれ試験・前向きの現況は `GET /attention-rules`(`useAttentionRules`)から
 * チップの条件の分を引く。回収率の数値はすべて `RoiValue` を通し計算基準のラベルを添える。
 * 期待回収率(判断時点・現在値・選ばれた馬の平均)は推定値なので `PseudoValue` を通す。
 * 前向き検証は公式の単勝払戻での回収率が段階判定の基準(集計方針 v2・139)で、判断時オッズでの精算
 * (v1)と保存オッズでの値は参考として並べる。判断時点の見込み(過去データからの換算)は一覧と同じ
 * `BuyTimeExpectationLine` で描く。
 *
 * 表示しないもの: 勝率・p̂・強弱の語・損益色・成績順の並び替え・購入を勧める表現。
 */

const WINDOW_ALL = "通算(2010〜26)";
const WINDOW_C = "確認窓(2019〜26)";

type RaceContext = Pick<AttentionAvailable, "post_time" | "has_results" | "judged_at">;

/** パネルを出す内容があるか(該当条件がある、または取消で無効になった pick がある)。 */
export function hasAttentionDetail(horse: AttentionHorse): boolean {
  return (
    horse.applicable.length > 0 ||
    Object.values(horse.pick_status).some((status) => status !== "none")
  );
}

function voidedRules(horse: AttentionHorse): RuleId[] {
  return (Object.keys(horse.pick_status) as RuleId[]).filter(
    (id) => horse.pick_status[id] === "void:scratched",
  );
}

function parseMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? null : t;
}

/** 判断時点で該当した条件(いまも有効な pick と、取消で無効にした pick)。 */
function judgedRules(horse: AttentionHorse): RuleId[] {
  return (Object.keys(horse.pick_status) as RuleId[]).filter(
    (id) => horse.pick_status[id] !== "none",
  );
}

/** 条件 id の並び(対照なら「対照 S5」)。どれが対照かは `GET /attention-rules` の `control` から
 *  引く(手書きの定数を持たない)。一覧が未取得の間は id だけを出す。 */
function RuleIdList({ ids }: { ids: readonly RuleId[] }) {
  const { data } = useAttentionRules();
  const control = ruleFlags(data?.items)?.control ?? null;
  return <>{ids.map((id) => ruleIdText(id, control)).join(", ")}</>;
}

export function AttentionPanel({
  horse,
  race,
  now,
  cancelled = false,
}: {
  horse: AttentionHorse;
  race: RaceContext;
  /** 描画時刻(価格鮮度の評価に使う)。呼び出し側が 1 回だけ取る — 自動では更新しない。 */
  now: Date | number;
  /** 出走表でこの馬が出走取消・競走除外か(`entry_status !== "started"`)。取消馬にチップは無い。 */
  cancelled?: boolean;
}) {
  const voided = voidedRules(horse);
  const chip = horse.chip_rule;

  // 取消馬: チップ・バッジ・検証の進み具合は出さない(spec「取消馬: チップなし」)。pick の後に
  // 取消になり、次の計算が void を追記する前は API がまだ pick として返す — それでも出走表は
  // 取消を正とし、判断時点で該当した事実だけを出す。
  if (cancelled) {
    const judged = judgedRules(horse);
    return (
      <div className="attn-panel attn-panel--minimal" data-testid="attention-panel">
        <p data-testid="attention-cancelled">
          出走取消
          {judged.length > 0 && (
            <>
              (判断時点の該当: <RuleIdList ids={judged} />)
            </>
          )}
        </p>
        {voided.length > 0 && (
          <p data-testid="attention-voided">
            出走取消のため無効: <RuleIdList ids={voided} />
          </p>
        )}
      </div>
    );
  }

  // 対照だけ・取消で全部無効: チップは無く、該当の事実だけを出す(US1 シナリオ 7)。
  // `chip_rule` が null になるのは対照でない条件が 1 つも該当しないときだけ(eval `chip_rule`)
  // なので、ここで該当している条件はすべて対照 — API 自身の答えから言える(一覧は要らない)。
  if (chip == null) {
    return (
      <div className="attn-panel attn-panel--minimal" data-testid="attention-panel">
        {horse.applicable.length > 0 && (
          <p data-testid="attention-control-only">対照 {horse.applicable.join(", ")} に該当</p>
        )}
        {voided.length > 0 && (
          <p data-testid="attention-voided">
            出走取消のため無効: <RuleIdList ids={voided} />
          </p>
        )}
      </div>
    );
  }

  return <ChipPanel horse={horse} chip={chip} race={race} now={now} voided={voided} />;
}

function ChipPanel({
  horse,
  chip,
  race,
  now,
  voided,
}: {
  horse: AttentionHorse;
  chip: RuleId;
  race: RaceContext;
  now: Date | number;
  voided: RuleId[];
}) {
  const rulesQuery = useAttentionRules();
  const rule = rulesQuery.data?.items.find((r) => r.id === chip);
  // 探索後固定・対照はレジストリの属性(一覧の `posthoc`/`control`)。一覧が未取得の間は不明として
  // バッジを出さず、対照の注記も付けない(推測で出さない)。
  const flags = ruleFlags(rulesQuery.data?.items);

  const chipStage: StageDetail | null = horse.chip_stage ?? horse.stages[chip] ?? null;
  const freshness = freshnessLevel(
    horse.current?.odds_observed_at,
    race.post_time,
    race.has_results,
    now,
  );
  const level = emphasisLevel(horse.levels, freshness.level, horse.chip_now, chipStage);

  const posthoc = flags ? horse.applicable.filter((id) => flags.posthoc.has(id)) : [];
  const unconfirmed = posthoc.some((id) => horse.stages[id]?.stage !== "passed");

  const listed = flags
    ? horse.applicable.filter((id) => !flags.control.has(id))
    : horse.applicable;
  const control = flags ? horse.applicable.filter((id) => flags.control.has(id)) : [];
  const judgedAt = formatJstDateTime(race.judged_at) ?? PLACEHOLDER;

  return (
    <div className="attn-panel" data-testid="attention-panel">
      <div className="attn-panel__head">
        <span className="attn-panel__title" data-testid="attention-panel-title">
          {chipStage ? chipLabel(chip, chipStage) : `注目条件 ${chip}`}
        </span>
        {horse.chip_s2 && <span className="attn-chip attn-chip--sub">S2</span>}
        {posthoc.length > 0 && (
          <span className="attn-badge" data-testid="attention-badge-posthoc">
            探索後固定
          </span>
        )}
        {posthoc.length > 0 && unconfirmed && (
          <span className="attn-badge" data-testid="attention-badge-unconfirmed">
            前向き未確認
          </span>
        )}
        <AttentionProgress level={level} testId="attention-progress" />
      </div>

      <dl className="attn-panel__rows">
        <dt>該当する条件</dt>
        <dd data-testid="attention-applicable">
          {listed.map((id, i) => (
            <span key={id}>
              {i > 0 && ", "}
              {id}
              {horse.stages[id] && isClosedStage(horse.stages[id].stage) && (
                <span className="attn-tag">({stageLabel(horse.stages[id])})</span>
              )}
            </span>
          ))}
          {control.length > 0 && `(対照 ${control.join(", ")} にも該当)`}
          {voided.length > 0 &&
            `・出走取消のため無効: ${voided.map((id) => ruleIdText(id, flags?.control)).join(", ")}`}
          ・判断時点 = このレースの最初の計算({judgedAt})
        </dd>

        <dt>過去検証</dt>
        <dd data-testid="attention-backtest">
          <RuleSection query={rulesQuery} rule={rule}>
            {(r) => <BacktestRows rule={r} level={horse.levels?.backtest ?? null} />}
          </RuleSection>
        </dd>

        <dt>{BUY_TIME_LABEL}</dt>
        <dd data-testid="attention-buy-time">
          <RuleSection query={rulesQuery} rule={rule}>
            {(r) => (
              <BuyTimeExpectationLine
                expectation={r.buy_time_expectation}
                testId="attention-buy-time-line"
              />
            )}
          </RuleSection>
        </dd>

        <dt>前向き検証</dt>
        <dd data-testid="attention-prospective">
          <RuleSection query={rulesQuery} rule={rule}>
            {(r) => <ProspectiveRows rule={r} stage={chipStage} />}
          </RuleSection>
        </dd>

        <dt>価格ずれ試験</dt>
        <dd data-testid="attention-price-noise">
          <RuleSection query={rulesQuery} rule={rule}>
            {(r) => <PriceNoiseRows rule={r} />}
          </RuleSection>
        </dd>

        <dt>価格鮮度</dt>
        <dd data-testid="attention-freshness">
          <div>
            <span data-testid="attention-freshness-label">{freshness.label}</span>
            {freshnessDetail(horse.current, race.post_time, freshness.reason, now)}
          </div>
          <div className="attn-panel__note">
            区切り: 10 分以内/60 分以内/それ以上。この間にオッズがどれだけ動くかは測っていません
          </div>
        </dd>

        <dt>期待回収率</dt>
        <dd data-testid="attention-ev">
          <EvLine testId="attention-judged" label="判断時点" snap={horse.judged} />
          {horse.current ? (
            <EvLine testId="attention-current" label="現在値" snap={horse.current}>
              {chipNowText(chip, horse.chip_now)}
            </EvLine>
          ) : (
            <div data-testid="attention-current">
              現在値: {CHIP_NOW_LABELS.unknown}(再計算待ち・オッズ欠落などで最新の計算がありません)
            </div>
          )}
          <div className="attn-panel__note">
            判断時点の値はこのレースの最初の計算で凍結したもの、現在値は最新の計算の値です
          </div>
          {horse.field_changed_after_pick && (
            <div data-testid="attention-field-changed">
              出走馬がその後変わっています(判断時点の値は変わる前の出走馬で計算したものです)
            </div>
          )}
        </dd>
      </dl>

      <p className="attn-panel__note" data-testid="attention-panel-disclaimer">
        注目条件は過去データで最も有望だった条件で、検証済みの条件ではありません。的中や利益を保証するものではありません。
      </p>
    </div>
  );
}

function chipNowText(chip: RuleId, chipNow: AttentionHorse["chip_now"]): string {
  if (chipNow === "matches") return `・${chip} の条件を現在値でも満たす`;
  if (chipNow === "no_longer") {
    return `・${CHIP_NOW_LABELS.no_longer}(現在値では ${chip} の条件を満たしません)`;
  }
  return `・${CHIP_NOW_LABELS.unknown}`;
}

/** 「最新の計算に使ったオッズの取得から 42 分・発走まで 3 時間 10 分」。発走後は経過を言わない。 */
function freshnessDetail(
  current: EvSnapshot | null,
  postTime: string | null,
  reason: ReturnType<typeof freshnessLevel>["reason"],
  now: Date | number,
): string {
  if (reason === "afterPost") return "";
  const nowMs = typeof now === "number" ? now : now.getTime();
  const observed = parseMs(current?.odds_observed_at);
  const post = parseMs(postTime);
  const parts: string[] = [];
  if (observed !== null) {
    parts.push(`最新の計算に使ったオッズの取得から ${formatDuration(nowMs - observed)}`);
  }
  if (post !== null && nowMs < post) parts.push(`発走まで ${formatDuration(post - nowMs)}`);
  return parts.length > 0 ? `(${parts.join("・")})` : "";
}

function EvLine({
  testId,
  label,
  snap,
  children,
}: {
  testId: string;
  label: string;
  snap: EvSnapshot | null;
  children?: string;
}) {
  if (!snap) {
    return (
      <div data-testid={testId}>
        {label}: {PLACEHOLDER}
      </div>
    );
  }
  const at = formatJstDateTime(snap.computed_at);
  return (
    <div data-testid={testId}>
      <span className="attn-panel__ev-label">{label}</span>
      {at ? `(${at})` : ""}: 15 seed 平均{" "}
      <EvValue ratio={snap.ens_expected_return} />
      {snap.single_expected_return != null && (
        <>
          ・単 seed <EvValue ratio={snap.single_expected_return} />
        </>
      )}
      ・単勝 {formatNum(snap.odds, 1)} 倍{children}
    </div>
  );
}

/** 期待回収率(推定値)。`PseudoValue` で推定バッジを付け、回収率(`data-kind="roi"`)とは別の
 *  種類の数値であることを `data-kind="expected_return"` で示す。 */
function EvValue({ ratio }: { ratio: number | null | undefined }) {
  return (
    <span data-kind="expected_return">
      <PseudoValue kind="expected_return">{formatPct(ratio, 1)}</PseudoValue>
    </span>
  );
}

function RuleSection({
  query,
  rule,
  children,
}: {
  query: ReturnType<typeof useAttentionRules>;
  rule: RuleSummary | undefined;
  children: (rule: RuleSummary) => ReactNode;
}) {
  // Neutral states: no alert role / error colour — the panel is supplementary to the table.
  if (query.isLoading) return <span className="attn-panel__note">読み込み中…</span>;
  if (query.error) {
    return (
      <span className="attn-panel__note" data-testid="attention-rules-error">
        注目条件の一覧を取得できませんでした(HTTP {query.error.status} {query.error.code})
      </span>
    );
  }
  if (!rule) return <span className="attn-panel__note">{PLACEHOLDER}</span>;
  return <>{children(rule)}</>;
}

function BacktestRows({ rule, level }: { rule: RuleSummary; level: Level | null }) {
  const b = rule.backtest;
  const stat = (s: RuleSummary["backtest"]["all"]) =>
    `${formatRoi(s.roi)}(${formatCi([s.ci_low, s.ci_high]) ?? "区間 —"}・${formatCount(s.n)} 点・` +
    `${formatCount(s.hits)} 的中・${formatP(s.p_one_sided)})`;
  return (
    <>
      {level != null && (
        <div className="attn-panel__axis" data-kind="criterion">
          {backtestLevelLabel(level)}
        </div>
      )}
      <div>
        {WINDOW_ALL} <RoiValue basis={b.valuation_basis}>{stat(b.all)}</RoiValue>
      </div>
      <div>
        {WINDOW_C} <RoiValue basis={b.valuation_basis}>{stat(b.c)}</RoiValue>
        ・p は片側・多重探索の補正なし
      </div>
      <div data-testid="attention-selected">
        選ばれた馬の期待回収率の平均{" "}
        <EvValue ratio={b.selected.all.mean_ev} />
        (実際の回収率{" "}
        <RoiValue basis={b.valuation_basis}>{formatRoi(b.selected.all.realized_roi)}</RoiValue>)
      </div>
    </>
  );
}

function ProspectiveRows({ rule, stage }: { rule: RuleSummary; stage: StageDetail | null }) {
  const p = rule.prospective;
  const officialCi = formatCi(p.official.ci);
  const awaitingPayout = p.counts.payout_race_missing;
  const inconsistent = p.counts.payout_inconsistent;
  return (
    <>
      <div>
        {`${stage ? stageLabel(stage) : PLACEHOLDER}(集計 ${formatCount(p.n_counted)} 点・` +
          `${formatCount(p.n_hits)} 的中・集計方針 ${p.policy_version}・` +
          `集計開始 ${p.start_date ?? "未設定"})`}
      </div>
      <div data-testid="attention-prospective-official">
        {PROSPECTIVE_BASIS_LABELS.official}{" "}
        <RoiValue basis={p.official.valuation_basis}>
          {formatRoi(p.official.roi)}
          {officialCi ? `(${officialCi})` : ""}
        </RoiValue>
      </div>
      <div data-testid="attention-prospective-reference">
        {PROSPECTIVE_BASIS_LABELS.frozen}{" "}
        <RoiValue basis={p.frozen.valuation_basis}>{formatRoi(p.frozen.roi)}</RoiValue>・
        {PROSPECTIVE_BASIS_LABELS.stored}{" "}
        <RoiValue basis={p.stored.valuation_basis}>{formatRoi(p.stored.roi)}</RoiValue>
      </div>
      {(awaitingPayout > 0 || inconsistent > 0) && (
        <div data-testid="attention-payout-exclusions">
          {`集計外: ${EXCLUSION_LABELS.payout_race_missing} ${formatCount(awaitingPayout)} 点・` +
            `${EXCLUSION_LABELS.payout_inconsistent} ${formatCount(inconsistent)} 点` +
            "(公式払戻の無いレースはレース単位で集計外にしています)"}
        </div>
      )}
      {p.decisions.map((d) => {
        const ci = formatCi(d.ci);
        const basis = decisionRoiBasis(d.valuation_basis);
        return (
          <div key={d.checkpoint} data-testid={`attention-decision-${d.checkpoint}`}>
            {d.checkpoint} 点の判定: {DECISION_LABELS[d.decision]}(
            {basis === null ? (
              "回収率は表示なし(精算の基準が記録にありません)"
            ) : (
              <RoiValue basis={basis}>
                {formatRoi(d.roi_frozen)}
                {ci ? `(${ci})` : ""}
              </RoiValue>
            )}
            ・{formatCount(d.n_hits)} 的中・判定 {formatJstDateTime(d.decided_at) ?? PLACEHOLDER})
          </div>
        );
      })}
      {stage?.stage === "failed" && (
        <div className="attn-panel__note" data-kind="criterion">
          区間の上限が 100% を下回ったため不通過と記録しました。表示は続け、pick の保存も続けます
        </div>
      )}
      {stage?.stage === "undecided" && (
        <div className="attn-panel__note" data-kind="criterion">
          600 点でも区間が 100% をまたいだため判定保留で終了しました(以後は自動で更新しません)
        </div>
      )}
      {stage?.checkpoint_pending && (
        <div data-testid="attention-pending">
          {"判定待ち: 集計はチェックポイントに達していますが、判定の記録がまだありません" +
            "(判定は発走から 3 日たった分を材料にします)"}
        </div>
      )}
      {p.next_checkpoint != null && p.remaining_to_next != null && (
        <div>
          次のチェックポイント({p.next_checkpoint} 点)まで {formatCount(p.remaining_to_next)} 点
        </div>
      )}
    </>
  );
}

function PriceNoiseRows({ rule }: { rule: RuleSummary }) {
  const base = rule.backtest.all.n;
  return (
    <>
      <div className="attn-panel__note">
        モデルの推定は固定し、選定に使うオッズだけをずらした代理実験(通算 2010〜26・10 反復の平均)
      </div>
      {rule.price_noise.map((pn) => (
        <div key={pn.sigma} data-testid="attention-price-noise-row">
          <span data-kind="sigma">{`ずれ ${Math.round(pn.sigma * 100)}%`}</span> で{" "}
          <RoiValue basis="closing_odds_approx">{formatRoi(pn.roi)}</RoiValue>
          (選ばれる馬は {base > 0 ? (pn.n / base).toFixed(1) : PLACEHOLDER} 倍・重なり{" "}
          <span data-kind="overlap">{`${Math.round(pn.overlap * 100)}%`}</span>)
        </div>
      ))}
    </>
  );
}
