# Feature Specification: exotic EV 推奨と疑似ROIバックテスト

**Feature Branch**: `011-exotic-ev-recommendation`

**Created**: 2026-06-23

**Status**: Draft

**Input**: User description: "exotic EV 推奨。EV(c)=モデル結合確率 P_model(009 on モデル p) × 推定市場オッズ O_est(010 on 市場 win オッズ)。EV≥閾値 上位 K を recommendations に保存(is_estimated_odds=true)。疑似ROIバックテスト(二重疑似)。007 を exotic に拡張。"

## 概要

Feature 009(結合確率エンジン)と 010(推定市場オッズ変換)を組み合わせ、exotic 券種
(複勝/馬連/馬単/ワイド/三連複/三連単)の期待値 EV を計算して買い目を推奨する。Feature 007 の単勝 EV を exotic に拡張。

`EV(c) = P_model(c) × O_est(c)`:
- **P_model(c)**: Feature 009 を**モデルの win 確率 p**(`race_predictions.win_prob`、006)に適用した各券種の的中確率。
- **O_est(c)**: Feature 010 を**市場の win オッズ**(`race_horses.odds`)に適用した各券種の推定市場オッズ。

`EV ≥ 閾値` かつ各(レース, 券種)で**EV 上位 K 点**を買い目として `recommendations` に保存。**推定オッズ使用のため二重に
疑似**(推定オッズ + Plackett-Luce 外挿)であることを明示し、`is_estimated_odds=true` で実オッズ由来と区別する(憲法 V)。
スキーマ変更なし。

**最重要(リーク境界 / p≠q)**: 的中確率は**モデル確率 p**(009)、推定オッズは**市場由来 q**(010、市場 win オッズから)。
両者を混同しない(`EV = P_model(p) × O_est(q)`)。買い目決定はレース結果(着順)を一切参照しない(結果は疑似ROI 採点のみ)。

「利用者」は人間ではなく、推奨を生成・評価するオペレーターと、将来の運用 UI。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - exotic EV 買い目を生成して保存できる (Priority: P1) 🎯 MVP

オペレーターが予測実行(prediction_run)/レースを指定すると、各 exotic 券種で EV≥閾値 上位 K の買い目が計算され
`recommendations` に保存される。

**Why this priority**: 本フィーチャーの中核。009×010 を「行動(買い目)」に変換。憲法 P0 の 2 つを結実させる最小価値。

**Independent Test**: ある prediction_run と win オッズについて exotic 推奨を生成し、各券種で EV≥閾値 上位 K の買い目だけが
`recommendations`(該当 bet_type、is_estimated_odds=true、estimated_market_odds_used=O_est、pseudo_odds=1/P_model、
pseudo_roi=EV−1、selection は JSONB 安全な配列)で保存されることを確認。

**Acceptance Scenarios**:

1. **Given** モデル予測(p)と市場 win オッズ, **When** EV 推奨を生成, **Then** **同一の出走母集団**(p と win オッズの
   両方が有効な馬)で 009(P_model)と 010(O_est)を計算し、`EV=P_model×O_est`、`EV≥閾値` の上位 K が保存される。
2. **Given** 片方(p または win オッズ)が欠損する馬, **When** 生成, **Then** その馬を母集団から除外して残存馬で
   再正規化し(または該当レース/券種をスキップして監査記録)、p と q を異なる母集団で掛け合わせない。
3. **Given** 推奨行, **When** 内容を検査, **Then** `bet_type`=券種・`selection`(順序券種=順序付き配列/無順序券種=整列
   配列、frozenset を保存しない)・`market_odds_used=null`・`estimated_market_odds_used=O_est`・`is_estimated_odds=true`・
   `pseudo_odds=1/P_model`・`pseudo_roi=EV−1`・logic_version が揃う。
4. **Given** 三連単等の組み合わせ爆発, **When** 生成, **Then** EV≥閾値 を `(-EV, 決定論的キー)` で整列し上位 K に制限する
   (1レース1券種あたり最大 K 行)。
5. **Given** 取消・除外馬, **When** 生成, **Then** 母集団から除外する(009/010 の入力で再正規化)。

---

### User Story 2 - exotic の疑似ROIバックテストで baseline と比較 (Priority: P1)

オペレーターが期間を指定すると、exotic EV 戦略の疑似ROI(払戻=stake×O_est=二重疑似)を、券種別 baseline と同一条件で
比較できる。

**Why this priority**: 憲法 III(評価先行)。推定オッズに依存する exotic 推奨は評価なしに採否を判断できない。

**Independent Test**: 合成データで EV 戦略と baseline を同一レース集合で走らせ、券種別の的中判定(順序/無順序/包含)、
複勝・ワイドの**複数当たり**(1レースで複数行的中)が正しく扱われ、回収率/的中率/見送り率/最大DD/最大連敗が算出され、
全出力が**二重疑似**として明示される。

**Acceptance Scenarios**:

1. **Given** 期間と確定結果のレース群, **When** バックテスト, **Then** 各券種で的中判定(馬単=確定1,2着順序一致/三連単=
   1,2,3着順序一致/馬連=上位2無順序/三連複=上位3無順序/ワイド=top3 内2頭/複勝=圏内)を行い、払戻=`stake×O_est`(推定)。
2. **Given** ワイド/複勝で 1 レースに複数の的中買い目, **When** 採点, **Then** 各的中行がそれぞれ `stake×O_est` を払戻
   (ベット単位の合計、レースでキャップしない)。
3. **Given** EV 戦略と baseline(券種別: 最低 O_est 組み合わせ=市場最有力、均等), **When** 同一レース集合で比較, **Then**
   各戦略の疑似ROI 指標が並ぶ。
4. **Given** 評価出力, **When** レポート, **Then** **二重疑似(モデル確率 × 推定市場オッズ、清算払戻も推定値)**と明示される。
5. **Given** 同着・推定不能(オッズ欠損)・未完走を含むレース, **When** 採点, **Then** 規則どおり扱う(順位が一意でない
   同着は該当レースをスキップして監査、DNF は外れ、推定不能は母集団から除外)。

---

### User Story 3 - CLI で exotic 推奨生成とバックテスト (Priority: P2)

オペレーターが CLI で、レース/予測実行を指定して exotic 推奨生成、期間を指定してバックテストを実行できる。EV 閾値・
上位 K・stake・対象券種を設定できる。

**Why this priority**: 運用効率。MVP(US1/US2)成立後の操作性。

**Independent Test**: CLI で exotic 推奨生成(レース指定)とバックテスト(期間指定)を実行し、サマリ(券種別推奨件数/
疑似ROI 指標/baseline 比較/二重疑似明示)が表示される。

**Acceptance Scenarios**:

1. **Given** prediction_run or race_id, **When** 推奨生成 CLI, **Then** 券種別の保存件数と各買い目の EV が表示される。
2. **Given** 期間, **When** バックテスト CLI, **Then** EV 戦略と baseline の券種別疑似ROI 指標が二重疑似明示付きで表示される。

---

### Edge Cases

- **p/q 母集団不一致**: p のみ/win オッズのみの馬は EV 計算から除外(同一母集団で 009/010 を実行)。除外で母集団が変われば
  残存馬で再正規化し開示、もしくはレース/券種をスキップして監査。
- **組み合わせ爆発**: 三連単 N(N−1)(N−2)。EV≥閾値 上位 K に制限(設定可能)。最大行数=Σ_券種 min(K, 有効組み合わせ数)。
- **selection の保存形**: 順序券種(馬単/三連単)=順序付き配列、無順序券種(馬連/ワイド/三連複)=整列配列、単一(複勝)=
  単一馬。**frozenset/tuple をそのまま保存しない**(JSONB 安全な配列)。
- **複勝・ワイドの複数当たり**: 1 レースで複数買い目が的中しうる。各的中をベット単位で払戻(レースでキャップしない)。
- **同着**: 必要な順位が一意に決まらない同着は該当レースをスキップして監査(規則確定まで)。複勝/ワイドの圏内同着は的中。
- **推定不能**: オッズ欠損で O_est が出せない券種/馬は推奨対象外。
- **EV の意味**: O_est=(1−控除率)/P_market のため、市場一致(P_model=P_market)で EV=1−控除率<1。EV>1 は「モデルが市場
  含意より的中を高く見る」value。閾値は設定可能(既定で value を選別)。
- **append-only**: 再生成は新しい推奨群(別 logic_version)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは各(レース, 券種, 組み合わせ c)で `EV(c)=P_model(c)×O_est(c)` を計算する MUST。P_model は 009 を
  **モデル p**(race_predictions.win_prob)に、O_est は 010 を**市場 win オッズ**(race_horses.odds)に適用して得る。
- **FR-002**: システムは P_model と O_est を**同一の出走母集団**(p と win オッズの両方が有効な馬)で計算する MUST。片方
  欠損の馬は母集団から除外し残存馬で再正規化(または該当レース/券種をスキップして監査)。**p と q を異なる母集団で
  掛け合わせない**。
- **FR-003**: システムは各(レース, 券種)で `EV≥閾値` を `(-EV, 決定論的キー)` で整列し**上位 K** に制限して買い目とする
  MUST(K 設定可能)。
- **FR-004**: システムは買い目の選択に**レース結果(着順)を一切参照しない** MUST(リーク境界)。結果は疑似ROI 採点のみ。
- **FR-005**: システムは各推奨を `recommendations` に append-only 保存する MUST: `bet_type`(券種)・`selection`(順序券種=
  順序付き配列/無順序券種=整列配列/単一馬、**JSONB 安全、frozenset を保存しない**)・`market_odds_used=null`・
  `estimated_market_odds_used=O_est`・`is_estimated_odds=true`・`pseudo_odds=1/P_model`・`pseudo_roi=EV−1`・`logic_version`・
  `computed_at`・`prediction_run_id`・`race_id`。
- **FR-006**: `logic_version` は EV 式・閾値・K・stake・控除率・q ソース・cap・母集団ポリシー・009/010 版を含む MUST。
- **FR-007**: システムは疑似ROIバックテストで券種別の的中判定(馬単/三連単=順序一致、馬連/三連複=無順序一致、ワイド=
  top3 内 2 頭、複勝=圏内)を行い、払戻=`stake×O_est`、外れ=0 として回収率/的中率/見送り率/最大DD/最大連敗を計測する MUST。
- **FR-008**: システムは**複勝・ワイドの複数当たり**(1 レースで複数の的中買い目)を、各的中行がそれぞれ `stake×O_est` を
  払戻するベット単位で扱う MUST(レースでキャップしない)。
- **FR-009**: システムは exotic 券種別の ROI baseline(**最低 O_est 組み合わせ**=市場最有力、**均等**(決定論シード))を提供し、
  EV 戦略と**同一レース集合・同一可用性スキップ・同一 stake・同一 K**で比較する MUST。成功=回収率が baseline を上回る
  (回収率>1.0 ではない)。
- **FR-010**: システムは全評価出力を**二重疑似**(モデル確率 × 推定市場オッズ、清算払戻も推定値)として明示する MUST。
  推定オッズは実 exotic 価格ではない(憲法 V)。
- **FR-011**: システムは同着(必要順位が一意でない場合)を該当レースのスキップ + 監査、DNF を外れ、推定不能を母集団除外で
  扱う MUST。取消・除外を母集団から除外する(憲法 IV)。
- **FR-012**: 推奨生成・採点は決定論的 MUST(同一入力・同一 logic_version で同一)。
- **FR-013**: システムは CLI で、レース/予測実行を指定した exotic 推奨生成と、期間を指定したバックテストを実行できる MUST。
  EV 閾値・K・stake・対象券種を設定できる。
- **FR-014**: MVP はスキーマ変更なし(Feature 001 の recommendations を使用)。実 exotic オッズ取得は将来。

### Key Entities *(include if feature involves data)*

- **CanonicalField**: p と win オッズの両方が有効な出走馬集合(EV 計算の共通母集団)。
- **ExoticBet**: 1 つの exotic 買い目。bet_type・selection(組み合わせ)・P_model・O_est・EV・stake。
- **EV 戦略**: EV=P_model×O_est で EV≥閾値 上位 K を選ぶ(閾値・K・stake・券種 設定可能)。
- **exotic ROI baseline**: 券種別 最低 O_est(市場最有力)/ 均等(決定論)。
- **Recommendation**(`recommendations`): exotic 買い目の永続化(is_estimated_odds=true、estimated_market_odds_used=O_est)。
- **疑似ROIレポート**: 戦略・券種ごとの回収率/的中率/見送り率/最大DD/最大連敗(二重疑似)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 任意の対象レースで、各券種の EV≥閾値 上位 K の買い目だけが `recommendations`(is_estimated_odds=true)に保存され、
  各行に監査情報(estimated_market_odds_used/pseudo_odds/pseudo_roi/selection/logic_version)が揃う。
- **SC-002**: P_model と O_est が同一母集団で計算され、p のみ/オッズのみの馬が EV から除外される(p と q を別母集団で
  掛け合わせない)。
- **SC-003**: selection が JSONB 安全な配列(順序券種=順序付き/無順序券種=整列)で保存され、frozenset を保存しない。
- **SC-004**: 疑似ROIバックテストが券種別の的中(順序/無順序/包含)と複勝・ワイドの複数当たりを正しく扱い、回収率/的中率/
  見送り率/最大DD/最大連敗を算出する。
- **SC-005**: EV 戦略と 2 つの券種別 baseline(最低 O_est/均等)が同一レース集合・同一条件で比較される。
- **SC-006**: 全評価・推奨出力が二重疑似として明示される(推定オッズ、実 exotic 価格ではない)。
- **SC-007**: 買い目決定が結果を参照せず、推奨生成・採点が決定論的。append-only。

## Assumptions

- Feature 006(予測)・009(結合確率)・010(推定市場オッズ)・007(単勝 EV/ROI 枠組み)が適用済み。betting を拡張し
  probability に依存する。
- win オッズは `race_horses.odds`(確定=closing-oracle 寄り)。前売り(008)を使えばより実運用寄りだが、O_est 自体が推定の
  ため評価は常に二重疑似。
- EV 閾値の既定は value(EV>1 近傍)を選別する値(設定可能)。K の既定は券種あたり少数(設定可能)。
- 複勝・ワイドの的中は包含(圏内)。複勝の払戻はプール分配依存だが O_est は 010 の近似(010 の前提を継承)。
- 同着の exotic 厳密処理は規則未確定のため該当レースをスキップ + 監査(将来、厳密化)。
- スキーマ変更なし。実 exotic オッズ取得・Kelly 等の資金管理・bias 補正は将来。日本語規約維持。
