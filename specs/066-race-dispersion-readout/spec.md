# Feature Specification: race dispersion & p/q divergence readout(レースの荒れ度・意見差の読み計器)

**Feature Branch**: `066-race-dispersion-readout`

**Created**: 2026-07-10

**Status**: Draft

**Input**: User description: "買い目を決めるために、このレースが荒れるか荒れないかを数値で出す。トレーニングや買い目生成には使わず、ユーザーが『買う/買わない』『人気馬/人気薄を買う』を自分で判断するための純表示計器。"

## 背景・動機 *(この feature の存在理由)*

製品目的は「市場を超える自動収益」ではなく **正直な意思決定支援**([[product-goal-decision-support]])。これまでの検証で、モデル p は市場 q に全セグメントで負け(047)・全オッズ帯で realized ROI <1.0(064/betting-roi-landscape)= **買い目を自動で当てるレバーは無い**ことが確定している。

一方、既に per-race で **モデル勝率 p**(校正込み)と **市場 vote-share q=(1/odds)/Σ(1/odds)** を canonical field で算出済み(021)。ユーザーが自分で判断する時に欲しいのは、その2つの**race-level 要約**:

1. **このレースはそもそも読めるのか(決着集中度=荒れる/荒れない)** → 「買う/見送り」の材料
2. **本命・人気薄についてモデルと市場の見方は割れているか** → 「人気馬/人気薄」の材料

本 feature は **新しい予測エッジではない**。既存 p/q の関数に過ぎない read-time 表示統計を、021/040/049 の表示規律(利益語・損益色・edge ソート禁止・pseudo は必ずラベル)に載せて提示する計器を作る。**トレーニング・買い目生成・採用ゲートには一切使わない**(憲法 II リーク境界)。

### 正直な限界(常時開示)

- 市場を超える波乱予測器ではない。q 由来は「市場の見方の要約」、p 由来は「モデルの見方」で、どちらが当たるかを主張しない。
- 荒れ度でレースをソートして「買うべきレース」を並べない。買いシグナルではない。
- retrospective のオッズは closing-leaning(010-017 で開示済)。計器は odds_as_of/odds_source と「発走前でない可能性」を常に表示する。

## Clarifications

### Session 2026-07-10

- Q: 軸A(決着集中度)を p と q どちらから計算するか → A: **q由来を本体**(実際の荒れの予測は q が優る=047 実測。市場は特に本命帯でよく校正)。**校正済み p(048 two_gamma)由来は q との差分としてのみ提示**し、対等な2つの数値として並べて選ばせない(生 p は本命 tail 圧縮=047 で荒れを過大評価するため単独使用しない)。
- Q: 軸A の主指標は max(q) か正規化エントロピーか → A: **バンドの見出しは正規化エントロピー**(頭数 ≤5 と 16+ を跨いで比較可能)、**生数値として max(q)(本命勝率)を併記**。競合する2つのバンドラベルは作らない(codex 3)。
- Q: q が欠損・部分欠損のレースの軸A → A: **軸A は unavailable/null**(明示理由付き)。**p由来へフォールバックしない**(設計契約=q本体を反転させるため)。q 欠損は 021 `canonical_consistent`(p/q 母集団不一致)とは別のデータ可用性失敗として別理由で表現し、p 差分も抑制する(codex 1)。
- Q: 5段バンドの境界の決め方 → A: **凍結した過去窓での正規化エントロピーの5分位**。metric/頭数バケット/窓/as-of/version を artifact に記録(憲法 V)。境界フィットは表示対象レースより厳密前(`(race_date, race_id)` タイブレーク、013/017 規律)のレースのみで行い、後続レースは realized-chaos の OOS 診断にのみ使う。**結果(荒れたか)は境界決定に一切使わない**(047/048 事前登録規律)(codex 2)。
- Q: 隣接バンドが統計的に区別できない場合 → A: **結果を見てからバンドを併合しない**。Wilson / race-cluster bootstrap CI で区別不能なら「隣接バンドは有意差なし」と**正直に開示**し、記述的ラベルは維持。段数を減らしたければ **v2 を training 窓で事前登録**し later OOS 窓で検証(codex 6)。
- Q: 軸B(p vs q 意見差)の見せ方 → A: **3層のプログレッシブ開示**。(1)race-level 一言サマリ、(2)既存 040 per-horse `divergence_band` バッジ(**変更しない**)、(3)全馬 p/q テーブル展開。`canonical_consistent=false` は軸B と校正済み p 差分を抑制。057 複数モデル時は**どの選択モデルの p か**を明示(codex 4/7)。
- Q: q の race-level 集計は真の確率か → A: **market-derived の pseudo/表示データ**。既存 015/021 の pseudo/source バッジ経路を使い、`pseudo_roi` を流用しない・q を真の確率と示唆しない(codex 7)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - レースの荒れ度(決着集中度)を5段で読む (Priority: P1)

利用者として、レース詳細で「このレースはそもそも読めるのか(堅い/荒れる)」を、市場の見方を主とした5段バンドと生数値で一目で把握したい。モデルの見方が市場とどれだけズレて読んでいるかも差分で分かる。

**Why this priority**: 計器の中核=「買う/見送り」の第一材料。軸A が無ければ計器が成立しない。

**Independent Test**: 発走前オッズと結果の揃った(or ペンディングの)レースで軸A を計算 → q 由来の5段バンド(堅い/やや堅い/標準/やや波乱/波乱含み)+ 生数値(本命勝率 max(q)・上位3頭累積・正規化エントロピー)+ 校正済み p 由来の差分が表示される。q 欠損レースでは軸A が unavailable と正直に出る。

**Acceptance Scenarios**:

1. **Given** started 馬全頭に有効な win odds のあるレース, **When** 軸A を見る, **Then** q 由来の5段バンド(記述ラベル)+ 生数値(max(q)・top3累積・正規化エントロピー)が表示され、バンド境界は凍結窓由来で結果非参照。
2. **Given** 同レース, **When** 校正済み p 由来の集中度を見る, **Then** q 由来との**差分**として提示される(例「市場より読みが開いている/締まっている」)。対等な2値の並列選択にはしない。
3. **Given** win odds が欠損 or 一部の started 馬に無いレース, **When** 軸A を見る, **Then** 軸A は unavailable(明示理由)で、**p由来へフォールバックせず**、p 差分も抑制される。
4. **Given** 取消馬を含むレース, **When** q を集計, **Then** 取消馬は正規化前に除外され、canonical field 上で再正規化される(010/021 規律)。
5. **Given** 任意のレース, **When** バンドと生数値を見る, **Then** バンド横に必ず生数値が併記され(偽精度の緩和)、odds_as_of/odds_source と「発走前でない可能性」が表示される。

---

### User Story 2 - モデル p と市場 q の意見差を読む(人気馬/人気薄の材料) (Priority: P2)

利用者として、「本命(人気)をモデルは市場より高く見ているか低く見ているか」「モデルが市場より高く評価する人気薄がいるか」を、事実として(買いシグナルでなく)確認し、人気馬を買うか穴を狙うか自分で判断したい。

**Why this priority**: 計器のもう一方の判断軸=「人気馬/人気薄」。モデル独自の付加情報はここ(と軸A の p差分)に出る。

**Independent Test**: p と q の揃ったレースで、(1)race-level 一言サマリ(本命の向き・モデル上位に入る人気薄の有無・順位一致度)、(2)既存 040 バッジ、(3)全馬 p/q 展開、の3層が中立文言で表示される。`canonical_consistent=false` で軸B が抑制される。

**Acceptance Scenarios**:

1. **Given** p/q の揃ったレース, **When** 一言サマリを見る, **Then** 「本命(q1位)をモデルは 低評価/市場並み/高評価」「モデル上位3頭に N番(M人気)が入る」等の**事実**が中立文言で出る(「買い」と言わない)。
2. **Given** 同レース, **When** 馬表を見る, **Then** 既存 040 per-horse `divergence_band` バッジ(market_higher/model_higher/similar)がそのまま出る(本 feature は変更しない)。
3. **Given** 同レース, **When** 展開する, **Then** 全馬の p/q が並ぶ(040 の row-expand 同型・オンデマンド)。
4. **Given** p/q の母集団が不一致(`canonical_consistent=false`), **When** 軸B を見る, **Then** 軸B と校正済み p 差分が抑制される。
5. **Given** 複数モデルが選択可能(057), **When** 軸B を見る, **Then** どの選択モデルの p を比較しているか明示される。
6. **Given** 任意のレース, **When** 軸B を見る, **Then** 損益色・妙味/危険/edge 語・乖離ソートが無い(021/040 規律)。q 集計は pseudo/source バッジ付き。

---

### User Story 3 - バンドの荒れ度が実際に効いているかを OOS 診断する (Priority: P3)

運用者として、5段バンドが飾りでないことを、walk-forward OOS で「堅い→…→波乱で実際の本命敗北率/高配当決着率が単調に上がるか」を検証したい(採否ゲートでなく診断のみ)。

**Why this priority**: 計器の健全性の裏取り。SECONDARY(047 継承=採否・閾値調整に使わない)。無くても US1/US2 は成立するので P3。

**Independent Test**: 047 segment_edge 同型で、凍結境界で割り当てたバンド別に realized chaos(本命敗北率等)を walk-forward OOS 集計 → 単調性と隣接バンドの CI を出力。非単調・区別不能でも境界を再フィットせず正直に報告。

**Acceptance Scenarios**:

1. **Given** 凍結境界と OOS 窓(境界フィット窓より後), **When** バンド別 realized chaos を集計, **Then** バンド別 n・本命敗北率・高配当率・Wilson/cluster-bootstrap CI が出る。
2. **Given** 隣接2バンドの CI が重なる, **When** 診断を見る, **Then** 「隣接バンドは有意差なし」と開示し、**結果を見て併合しない**。
3. **Given** 境界フィット窓内の過去レース, **When** 診断集計, **Then** それらは OOS とラベルしない(フィット窓と OOS 窓を分離)。
4. **Given** 同着/取消/void レース, **When** chaos-rate を計算, **Then** それらの扱いは評価前に事前定義済み(dead heat/cancellation/void の予約規則)。

### Edge Cases

- **q 全欠損レース**: 軸A unavailable・軸B は p のみ(乖離出せず)→ 軸B も抑制、または「市場データ無し」と表示。
- **少頭数(≤5)/多頭数(16+)**: 正規化エントロピーで跨いで比較。診断で field-size 残留依存をチェックし、必要なら v2 を field-size バケット内5分位で**事前登録**(codex 2)。
- **同着**: chaos-rate 評価の予約規則で定義(surface count)。
- **未確定(結果ペンディング)レース**: 軸A/軸B は表示可(発走前でも p/q は出る)、US3 診断は確定分のみ。
- **odds が closing 上書き済み**: retrospective 表示として odds_as_of/source で開示(prospective 凍結は 065 の領分、混同しない)。

## Requirements *(mandatory)*

### Functional Requirements

**軸A(決着集中度)**

- **FR-001**: システムは canonical field(started・有効オッズ)の市場 q から race-level 集中度指標を計算する: 正規化エントロピー `H = -Σ q·ln q / ln N`、本命勝率 `max(q)`、上位3頭累積。取消馬は正規化前に除外(010/021)。
- **FR-002**: 5段バンド(堅い/やや堅い/標準/やや波乱/波乱含み)を**正規化エントロピーの凍結窓5分位**で割り当てる。バンドの向き(小エントロピー=堅い)を記述ラベルにし、損得を示唆しない。
- **FR-003**: バンド横に必ず生数値(max(q)・top3累積・正規化エントロピー)を併記する。
- **FR-004**: 校正済み p(048 two_gamma 経路)由来の集中度は **q 由来との差分**としてのみ提示する。生 p 単独の集中度は表示しない。
- **FR-005**: q が欠損 or started 馬に部分欠損なら軸A を **unavailable(明示理由)** とし、p 由来へフォールバックしない・p 差分も抑制する。この unavailable は 021 `canonical_consistent` とは別のデータ可用性理由で表す。
- **FR-006**: 軸A は odds_as_of・odds_source と「発走前でない可能性」を常に surface する。q 集計は market-derived の pseudo/表示として 015/021 の pseudo/source バッジ経路を使う(`pseudo_roi` 流用禁止・真確率示唆禁止)。

**軸B(p vs q 意見差)**

- **FR-007**: システムは race-level の中立サマリを出す(本命=q1位 に対するモデルの向き〔低評価/市場並み/高評価〕・モデル上位N頭に入る人気薄の有無・p順位とq順位の一致度)。全て事実文言で、買い/売り・妙味・危険を言わない。
- **FR-008**: 既存 040 per-horse `divergence_band(p,q)` を**変更せず**再利用し、馬表バッジとして表示する。
- **FR-009**: 全馬 p/q テーブルをオンデマンド展開で提供する(040 row-expand 同型)。
- **FR-010**: `canonical_consistent=false` の時は軸B と校正済み p 差分を抑制する。
- **FR-011**: 057 でモデルが選択可能な場合、軸B はどの選択モデルの p を比較しているか明示する。

**診断(SECONDARY)**

- **FR-012**: eval に 047 segment_edge 同型の walk-forward OOS 収集を追加し、凍結境界で割り当てたバンド別 realized chaos(本命敗北率・高配当率)を n・Wilson/cluster-bootstrap CI 付きで集計する。予測器非依存(eval は training 非依存)。
- **FR-013**: 境界フィット窓と OOS 窓を分離し、フィット窓内レースを OOS とラベルしない。dead heat/cancellation/void の扱いは評価前に事前定義する。
- **FR-014**: 隣接バンドが CI で区別不能でも境界を再フィット・併合しない。「有意差なし」を開示する。段数変更は training 窓での事前登録 v2 として扱う。
- **FR-015**: 境界 artifact に metric・頭数バケット・フィット窓・as-of・version を記録する(憲法 V 監査)。

**境界・リーク・契約**

- **FR-016**: バンド境界は表示対象レースより厳密前(`(race_date, race_id)` タイブレーク)のレースのみでフィットする。結果(荒れたか)は境界決定に使わない。
- **FR-017 (リーク境界・NON-NEGOTIABLE)**: 軸A/軸B の全表示派生値(集中度指標・バンド・q 集計・乖離サマリ)は**モデル入力特徴・training 経路に流入しない**。トークン禁止(registry・materialized columns)+ import-graph ガード + **behavioral 不変テスト**(表示軸の計算を変えても model input features と decision-support 経路の選択 p がバイト不変)で機械固定する。**「全 odds 変更が全モデルを不変にする」とは主張しない**(060 の market-offset candidate があるため)——主張は「本 feature の新 display 集計が feature/training 経路に入らない」に限定する(codex 4)。
- **FR-018 (read-only・純追加)**: API は GET のみ・純追加。既存 040 `divergence_band` を改変しない。race-level の nullable オブジェクト + 展開テーブルを足すのみ。スキーマ変更ゼロ・migration なし。OpenAPI は純追加で drift-check 緑・betting/training を import しない。

### Key Entities *(データ関与)*

- **RaceDispersion(軸A、read-time 計算・非永続)**: `band`(5段記述ラベル or null)・`band_unavailable_reason`(q 欠損等)・`normalized_entropy`・`favorite_win_prob`(max q)・`top3_cumulative`・`model_delta`(校正 p 由来との差分)・`odds_as_of`・`odds_source`・`is_pseudo`(market-derived 表示)。
- **RaceDivergence(軸B、read-time 計算・非永続)**: `summary`(中立文言 or null)・`favorite_direction`(model_higher/model_lower/similar)・`underrated_longshots`(モデル上位の人気薄リスト=事実)・`rank_agreement`・`model_version`(057 どの p か)・per-horse は既存 040 selection を再利用。
- **DispersionBoundary(境界 artifact、憲法 V)**: `metric`・`field_size_buckets`・`fit_window`・`as_of`・`version`・quintile edges。DB スキーマ変更なし(logic_version / artifact ファイルに記録、055/064 同型)。
- **DispersionBandDiagnostic(US3、eval 出力)**: バンド別 n・realized_chaos_rate・CI・separated フラグ。047 の SegmentRow 同型。

### 非目標 / Out of Scope

- 単一の合成「荒れ指数」数値(分解した事実を出す方針=偽精度回避)。
- バンド/荒れ度を採用ゲート・閾値調整・買い目生成・Kelly に使うこと(SECONDARY・純表示)。
- 発走前オッズ凍結による prospective 精算(065 の領分)。
- 「モデルが正しく市場が誤り」という判定・買い推奨・edge ランキング。
- スキーマ変更・新テーブル・migration。

## Success Criteria *(mandatory)*

- **SC-001**: started 全頭に有効オッズのあるレースで、軸A の5段バンド+生数値が表示され、境界が凍結窓・結果非参照であることをテストで確認できる。
- **SC-002**: q 欠損/部分欠損レースで軸A が unavailable(理由付き)になり、p フォールバックが起きない(テスト固定)。
- **SC-003**: 軸B の3層が中立文言で表示され、既存 040 `divergence_band` が未変更・`canonical_consistent=false` で軸B 抑制がテストで確認できる。
- **SC-004**: 表示軸の計算変更が model input features と decision-support 経路の選択 p を変えない(behavioral leak-guard 緑)。
- **SC-005**: API GET-only・OpenAPI 純追加・drift-check 緑・betting/training 非 import(境界テスト緑)。
- **SC-006**: US3 診断が walk-forward OOS でバンド別 realized chaos を CI 付きで出し、隣接バンド区別不能を併合せず開示する。
- **SC-007**: front に損益色・妙味/危険/edge 語・乖離ソートが無く、q 集計に pseudo/source バッジが付く(不変テスト緑)。

## 憲法との整合

- **II リーク境界(NON-NEGOTIABLE)**: 全表示派生値をモデル特徴/training に戻さない(FR-017 behavioral guard)。p≠q を保持し q を確率として混ぜない。q・結果を特徴化しない。
- **III 評価先行**: US3 診断は eval OOS 由来・独自指標を作らない(047 継承)。バンドは採否ゲートにしない。
- **IV 確率整合**: p・q は同一 canonical field(取消除外・再正規化)。021 の canonical field をそのまま使う。
- **V 監査**: 境界 artifact に metric/窓/as-of/version 記録。odds_as_of/source 表示。pseudo は単一バッジ経路。
- **VI 契約先行**: API 契約(OpenAPI 純追加)を front 実装前に確定。read-only 厳守(全 path GET)。スキーマ不変。

## Required Tests *(codex レビュー反映)*

- `axis_a_q_missing_returns_unavailable_no_p_fallback`
- `partial_q_or_canonical_inconsistent_suppresses_axis_b`
- `frozen_boundaries_strictly_before_date_race_id`
- `boundary_fit_invariant_to_target_and_future_results`
- `field_size_bucket_or_entropy_small_large_synthetic_case`
- `display_axis_tokens_absent_from_model_input_features_registry_materialized_columns`
- `display_axis_mutation_does_not_change_decision_support_p`
- `api_get_only_no_training_betting_write_imports`
- `openapi_pure_additive_drift_check`
- `front_pseudo_badge_required_for_q_aggregate`
- `front_no_profit_edge_value_copy_no_red_green_sorting`

## Deferred

- 合成荒れ指数・時系列スナップショット永続化・複数レース横断の荒れ度ダッシュボード。
- field-size バケット内5分位への v2 移行(診断で残留依存が出た場合に training 窓で事前登録)。
- prospective(発走前凍結)オッズでの荒れ度(065 データが貯まった後)。
- 軸A/軸B を組み合わせた「見送り推奨」表示(買いシグナル化リスクのため慎重に別 spec)。
