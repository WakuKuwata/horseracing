# Feature Specification: 単勝 EV 推奨と疑似ROIバックテスト

**Feature Branch**: `007-win-bet-recommendation`

**Created**: 2026-06-23

**Status**: Draft

**Input**: User description: "単勝 EV 推奨と疑似ROIバックテスト。Feature 006 の予測(win 確率)から EV=win_prob×単勝オッズ を計算し EV>=閾値 を単勝買い目として recommendations に保存。期間バックテストで回収率/的中率/見送り率/最大DD/最大連敗を baseline(人気1番/全頭均等)と比較。複勝・馬連・三連複(結合確率)は将来。"

## 概要

Feature 006 が保存した予測(`prediction_runs` / `race_predictions` の win 確率)を入力に、出走各馬の
**期待値 EV = win_prob × 単勝オッズ** を計算し、EV が閾値以上の馬を**単勝(win)の買い目**として
`recommendations` に保存する。さらに評価先行(憲法 III)として、期間に対する**疑似ROIバックテスト**で
回収率・的中率・見送り率・最大ドローダウン・最大連敗数を計測し、ROI baseline(人気1番常時単勝・全頭均等)と
同一条件で比較する。

**疑似評価の明示(重要)**: 単勝オッズ(`race_horses.odds`)は**結果確定時点**の情報で、未来レースの賭け締切時には
存在しない。本フィーチャーはこの確定オッズを EV 入力と払戻の双方に使う **closing-oracle な簡略化**であり、
実運用 ROI とは乖離する。したがって全評価を **「疑似評価(pseudo evaluation)」** と明示する(憲法 V)。
推定オッズ変換(未来レース用)は将来フィーチャー。結合確率を要する複勝・馬連・三連複も将来(憲法 P0)。

「利用者」は人間ではなく、推奨を生成・評価するオペレーターと、将来の運用 UI。スキーマ変更なし
(Feature 001 の `recommendations` を使用)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 予測から単勝 EV 買い目を生成して保存できる (Priority: P1) 🎯 MVP

オペレーターが予測実行(prediction_run)またはレースを指定すると、出走各馬の EV が計算され、EV>=閾値 の馬が
単勝買い目として `recommendations` に保存される。

**Why this priority**: 本フィーチャーの中核。Feature 006 の確率を「行動(買い目)」に変換する最小価値。

**Independent Test**: ある prediction_run について推奨生成を実行し、EV>=閾値 の馬だけが `recommendations` に
`bet_type='win'` で保存され、各行に market_odds_used・pseudo_odds・pseudo_roi・selection(horse_id/horse_number)・
logic_version が揃うことを確認。

**Acceptance Scenarios**:

1. **Given** win 確率と単勝オッズのある出走馬, **When** EV 推奨を生成, **Then** `EV=win_prob×odds>=閾値` の馬だけが
   `recommendations`(`bet_type='win'`)に保存される。
2. **Given** 推奨行, **When** 内容を検査, **Then** `market_odds_used=odds`・`is_estimated_odds=false`・
   `pseudo_odds=1/win_prob`・`pseudo_roi=win_prob×odds-1`・`selection={horse_id,horse_number}`・logic_version が揃う。
3. **Given** オッズ欠損(null)/ <=0 の馬, **When** 生成, **Then** その馬には推奨を出さない(EV 計算からスキップ)。
4. **Given** 取消・除外(`entry_status!='started'`)の馬, **When** 生成, **Then** 母集団から除外し、残存馬の win 確率を
   再正規化してから EV を計算する(憲法 IV)。

---

### User Story 2 - 期間の疑似ROIバックテストで baseline と比較できる (Priority: P1)

オペレーターが期間を指定すると、その期間の対象レースについて EV 戦略の推奨を集計し、回収率・的中率・見送り率・
最大ドローダウン・最大連敗数を、ROI baseline(人気1番常時単勝・全頭均等)と同一条件で比較できる。

**Why this priority**: 憲法 III(評価先行)。推奨ロジックは評価ハーネスなしに採否を判断できない。控除率下で
「儲かるか」ではなく「baseline を一貫して上回るか」を検証する基盤。

**Independent Test**: 合成データで EV 戦略と 2 つの baseline を同一レース集合で走らせ、疑似ROI 指標が定義どおり
(勝ち/負け/DNF/取消/同着を正しく扱い)計算され、戦略と baseline が同一条件で比較されることを確認。

**Acceptance Scenarios**:

1. **Given** 期間と確定結果のあるレース群, **When** バックテスト, **Then** 回収率(総払戻/総賭金)・的中率
   (的中ベット/全ベット)・見送り率・最大DD・最大連敗が計測される。
2. **Given** EV 戦略と baseline(人気1番/均等), **When** 同一レース集合で比較, **Then** 各戦略の疑似ROI 指標が
   同一評価条件で並ぶ。
3. **Given** 的中の定義, **When** 採点, **Then** 的中=`finished かつ finish_order==1`、払戻=`stake×odds`、外れ=0。
   DNF(出走したが未完走/非1着)は負け、取消・除外は母集団から除外(負けに数えない)。同着 1 着は的中。
4. **Given** 見送りレース(EV>=閾値 の馬が無い), **When** 集計, **Then** 見送りとして記録し、最大連敗に見送りを
   含めない(ベットしたレースのみで連敗・DD をカウント)。

---

### User Story 3 - CLI で推奨生成とバックテストを実行できる (Priority: P2)

オペレーターが CLI で、レース/予測実行を指定して推奨生成、期間を指定してバックテストを実行できる。EV 閾値・
固定賭け金(flat stake)を設定できる。

**Why this priority**: 運用効率。MVP(US1/US2)成立後の操作性。

**Independent Test**: CLI で推奨生成(レース指定)とバックテスト(期間指定)を実行し、サマリ(推奨件数/疑似ROI 指標/
baseline 比較)が表示され、閾値・stake を変えると結果が変わることを確認。

**Acceptance Scenarios**:

1. **Given** prediction_run or race_id, **When** 推奨生成 CLI, **Then** 保存件数と各推奨の EV が表示される。
2. **Given** 期間, **When** バックテスト CLI, **Then** EV 戦略と baseline の疑似ROI 指標が表で表示される。

---

### Edge Cases

- **オッズ欠損 / 0 以下**: 推奨を生成しない(EV 計算からスキップ)。micro-fill しない。
- **取消・除外**: 母集団・正規化から除外。推奨対象外。負けにも数えない。
- **DNF(出走・未完走/非1着)**: 負け(払戻 0)として計上。
- **同着 1 着**: 的中(払戻は確定オッズが同着控除済みである前提)。
- **EV>=閾値 の馬が複数**: 同一レースで複数の単勝買い目を許す(ポートフォリオ)。
- **見送り(該当馬なし)**: 賭けなし。見送り率に計上、連敗・DD には含めない。
- **prediction_run が無い / race_predictions 不在**: 推奨生成できない旨を明確に通知。
- **win_prob=0 の馬**: EV=0 で閾値未満、推奨対象外(pseudo_odds 無限大を避ける)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは対象レースの win 確率(`race_predictions`)と単勝オッズ(`race_horses.odds`)から
  `EV = win_prob × odds` を計算する MUST。
- **FR-002**: システムは母集団を出走(`entry_status='started'`)に限定し、取消・除外を除外する MUST。除外後、残存馬の
  win 確率を**再正規化**してから EV を計算する MUST(憲法 IV)。
- **FR-003**: システムは `EV >= 閾値` の馬のみを単勝(`bet_type='win'`)の買い目として `recommendations` に保存する MUST。
- **FR-004**: システムは買い目の選択に**レース結果(`race_results`/着順)を一切参照しない** MUST(リーク境界)。結果は
  疑似ROI 採点にのみ使う。
- **FR-005**: システムは各推奨に `selection={horse_id,horse_number}`・`market_odds_used=odds`・
  `is_estimated_odds=false`・`estimated_market_odds_used=null`・`pseudo_odds=1/win_prob`・
  `pseudo_roi=win_prob×odds-1`・`logic_version`・`computed_at`・`prediction_run_id`・`race_id` を保存する MUST。
- **FR-006**: システムはオッズが null または `<=0`、あるいは `win_prob<=0` の馬には推奨を生成しない MUST。
- **FR-007**: システムは疑似ROIバックテストで、的中=`result_status='finished' かつ finish_order==1`、払戻=`stake×odds`、
  外れ=`0` として、固定賭け金(flat stake)で回収率(総払戻/総賭金)・的中率・見送り率・最大ドローダウン・
  最大連敗数を計測する MUST。DNF は負け、取消・除外は除外、同着 1 着は的中。
- **FR-008**: システムは ROI baseline として**人気1番(最低オッズ)を常に単勝**で買う戦略と**全頭均等**(全出走馬を
  単勝で均等に買う)を提供し、EV 戦略と**同一レース集合・同一条件**で比較する MUST。
- **FR-009**: システムは最大ドローダウン・最大連敗を、**実際に賭けたレースのみ**で計算する MUST(見送りレースを
  連敗に含めない)。見送り率は別途計測する。
- **FR-010**: システムは推奨を **append-only** で保存する MUST(再生成は新しい推奨群、`logic_version` で区別)。`logic_version`
  には EV 式・閾値・stake・オッズ/取消の除外ポリシーを含める。
- **FR-011**: システムは全評価出力を**疑似評価(pseudo evaluation)**として明示する MUST(確定オッズ使用の simplification、
  実運用 ROI ではない)。
- **FR-012**: システムは CLI で、レース/予測実行を指定した推奨生成と、期間を指定したバックテストを実行できる MUST。
  EV 閾値・stake を設定できる(既定 `threshold=1.0`・`stake=100`、これにより SC-005 の再現が決定論的になる)。
- **FR-013**: システムは確率の前提として、`race_predictions.win_prob` が出走母集団で正規化済みであることを利用しつつ、
  生成時点で除外が判明した馬を 0 にして残存馬で再正規化する MUST(FR-002 と整合)。

### Key Entities *(include if feature involves data)*

- **Recommendation**(`recommendations`): 1 つの単勝買い目。bet_type='win'・selection(horse_id/horse_number)・
  market_odds_used・is_estimated_odds・pseudo_odds・pseudo_roi・logic_version・computed_at・prediction_run_id・race_id。
- **EV 戦略**: win_prob×odds で買い目を選ぶロジック(閾値・stake 設定可能)。
- **ROI baseline**: FavoriteROIBaseline(人気1番常時単勝)/ UniformROIBaseline(全頭均等)。確率品質 baseline
  (Feature 003 の market/uniform)とは別物。
- **疑似ROIレポート**: 戦略ごとの回収率・的中率・見送り率・最大DD・最大連敗(疑似評価)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 任意の対象レースで、EV>=閾値 の馬だけが `recommendations`(bet_type='win')に保存され、各行に監査情報
  (market_odds_used/pseudo_odds/pseudo_roi/selection/logic_version)が揃う。
- **SC-002**: オッズ欠損/0/取消・除外/win_prob=0 の馬に推奨が生成されず、除外後に残存馬で再正規化される。
- **SC-003**: 疑似ROIバックテストが勝ち/負け/DNF/取消/同着を定義どおり扱い、回収率・的中率・見送り率・最大DD・
  最大連敗を算出する。
- **SC-004**: EV 戦略と 2 つの ROI baseline(人気1番/均等)が同一レース集合・同一条件で比較される。
- **SC-005**: 推奨が append-only で保存され、同一レースの再生成が新しい推奨群(別 logic_version)になる。
- **SC-006**: 全評価出力が疑似評価として明示され、確定オッズ使用の前提がレポート/監査に記録される。
- **SC-007**(展開判定の参考バー、必須ではない): EV 戦略の回収率が両 baseline を上回る。`回収率>1.0` は控除率を
  超える展開候補の目安として別途記録する(本フィーチャーの合格条件ではない)。

## Assumptions

- Feature 001(recommendations/prediction_runs/race_predictions スキーマ)、006(予測保存)が適用済み。少なくとも
  1 つの prediction_run と対応する race_predictions が存在する。
- `race_horses.odds` は Feature 002 で取り込み済み(確定単勝オッズ)。null/0 はスキップ。
- 同着の確定オッズは同着控除済みとみなす(JRA-VAN 取込値をそのまま使用)。
- EV 戦略は同一レースで複数の単勝買い目を許す(1レース1点に限定しない)。
- flat stake(固定賭け金)を既定とする。Kelly 等の資金管理は将来。
- バックテストは確定オッズを使う closing-oracle 簡略化であり**疑似評価**。実運用 ROI・推定オッズ変換は将来。
- スキーマ変更なし。複勝・馬連・三連複(結合確率)・推定オッズは将来フィーチャー。
- 確率品質 baseline(Feature 003 の market/uniform)は ROI 比較に流用せず、ROI 専用 baseline を新設する。
