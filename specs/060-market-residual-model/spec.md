# Feature Specification: 市場残差型・精度最優先モデル (market-residual accuracy model)

**Feature Branch**: `060-market-residual-model`

**Created**: 2026-07-08

**Status**: Draft

**Input**: User description: "市場残差型の精度最優先モデル(058 で deferred された B2「今走オッズ」の実装)。今走オッズ由来の市場確率 q を race-softmax の offset として与え、特徴量側は市場からの残差だけを学習する。default モデル(p⊥q)は不変、非 active 併存登録のみ。"

## 背景と位置づけ

- 047 セグメント診断で「全セグメントで市場 q がモデル p に優位(win LogLoss: 市場 ≈0.202 vs モデル 0.216)」が確認済み。残ギャップは校正でなく**欠落情報**(市場は調教・馬体・直前気配を含む集合知)由来。
- 憲法 II は「市場オッズは初期方針では予測モデルの入力特徴量に使わない。特徴量化を試す場合は、**別 spec で**リーク防止・利用可能タイミング・評価方法を定義してから」と規定 — 本 spec がその定義。058(過去走市場評価)と同じ手続き型で、対象を「過去走の人気」から「今走オッズ」に進める第2弾。
- 057 のモデル切替基盤と 058 の「精度最優先モデルの非 active(candidate)併存登録」枠をそのまま利用。**意思決定支援の default モデル(現 active、p⊥q)には一切触れない**。
- untracked の `docs/market-aware-betting-policy-proposal.md`(回収率目的の買い目方策レイヤー、「オッズ入り着率モデルは作らない」と明記)とは**別軸で非競合**: あちらは既存 p を置き換えず下流方策に市場情報を使う提案、こちらは精度専用の併存モデル。どちらも default p⊥q を維持する。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 市場残差モデルの学習と事前登録ゲート評価 (Priority: P1)

オペレータが、今走オッズ由来の市場確率 q を土台(offset)にして「市場からのズレ(残差)」だけを既存特徴量で学習するモデルを、フル walk-forward で学習・評価できる。評価は事前登録ゲート(市場単体 baseline と既存精度最優先モデルの両方を上回ること)で機械判定され、通過した場合のみ非 active(candidate)として登録される。

**Why this priority**: 本 feature の価値のすべて。市場 q 単体(≈0.202)を上回れなければ「市場をそのまま見る」のと変わらず、モデルを登録する意味がない。

**Independent Test**: 実 DB でフル walk-forward 評価を実行し、win LogLoss が (a) 市場 q 単体 baseline、(b) lgbm-058-acc の両方を下回るかを機械判定できる。

**Acceptance Scenarios**:

1. **Given** 2007+ の学習データとオッズ, **When** 市場残差構成でフル walk-forward 評価を実行, **Then** win/top2/top3 LogLoss が市場 q 単体 baseline と並記され、事前登録ゲートの合否が機械判定される
2. **Given** ゲート全通過, **When** 学習済みモデルを登録, **Then** 非 active(candidate)として登録され、default モデルの active 状態・予測値は不変
3. **Given** ゲート不通過, **When** 評価完了, **Then** モデルは自動昇格せず、判定理由が記録される

---

### User Story 2 - 市場残差モデルでの予測 serving (Priority: P2)

オペレータが 057 のモデル切替基盤(model_version 指定)で市場残差モデルを選択し、オッズが取得済みのレースに対して予測を生成・永続化できる。オッズ未取得のレースはこのモデルでは予測不可として型付きスキップになる(黙って offset なしで予測しない)。

**Why this priority**: 学習・評価(US1)が通らなければ意味がないため P2。ただし serving 経路がなければ「評価専用の実験」で終わり、製品価値がない。

**Independent Test**: オッズありレースで model_version 指定予測が成功し、オッズなしレースで型付きスキップになることを実 DB で確認できる。

**Acceptance Scenarios**:

1. **Given** オッズ取得済みレースと登録済み市場残差モデル, **When** model_version 指定で予測実行, **Then** 予測が永続化され、監査情報に市場 offset の使用が記録される
2. **Given** オッズ未取得レース, **When** 同モデルで予測実行, **Then** 型付きスキップ(予測行を作らない・offset なし fallback もしない)
3. **Given** default モデル(active), **When** model_version 未指定の通常予測, **Then** 従来どおり default モデルが使われ、挙動・値ともに不変

---

### Edge Cases

- レース内の一部の馬だけオッズ欠損(取消馬以外で odds が null/0/不正): 欠損馬に市場情報を捏造しない。レース単位の扱い(学習から除外 or 中立化)は plan で fail-closed 方針として確定する
- 出走取消・除外馬: q の正規化母集団は started フィールドに揃える(009/010 の canonical field 規律と同じ)
- odds ≤ 1.0 や非数など不正値: 型付きで除外し、黙って q を歪めない
- 結果未確定レース(学習時): 既存 pl_topk と同じく学習中立化
- 市場 offset の土台が強いため、残差学習が退化(特徴が何も効かない)する可能性: その場合ゲート (b) で不合格になり、その事実自体が「市場に足せる情報が現状の特徴にない」という知見として記録される

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 市場確率 q は対象レース自身の単勝オッズのみから q_i=(1/odds_i)/Σ(1/odds_j)(010 の vote-share 定義を再利用)で計算しなければならない。他レース・過去・未来のオッズ、結果情報を q の計算に使ってはならない
- **FR-002**: モデルは residual/offset 型でなければならない: 最終スコアは「log q + 学習された残差スコア」で構成され、レース内 softmax で確率化される。この後処理(offset 加算 → softmax → 校正 → 正規化)は学習・評価・serving の全経路で同一でなければならない(039/042 の同一 postprocess 規律)
- **FR-003**: default(active)モデルの学習・serving・予測値はバイト不変でなければならない。特徴量列は変更せず FEATURE_VERSION は bump しない(offset は特徴列ではない)
- **FR-004**: 採用ゲートは事前登録し、結果を見てから変更してはならない: フル walk-forward で (a) win LogLoss が市場 q 単体 baseline を下回る(MUST)、(b) win LogLoss が既存精度最優先モデル lgbm-058-acc を下回る(MUST)、(c) top2/top3 LogLoss が市場 q 単体 baseline 比で非悪化(MUST)。全通過で非 active candidate 登録、不通過は登録せず結果のみ記録
- **FR-005**: リーク境界は挙動型テストで担保しなければならない: (i) 未来レース・他レースのオッズを変更しても対象レースの予測が不変、(ii) レース結果を変更しても予測が不変、(iii) 対象レース自身のオッズを変更すると予測が変化する(市場情報が実際に使われている正の対照)。オッズトークンの grep 型 leak-guard は本モデル専用経路には適用しない(058 前例)
- **FR-006**: 登録は非 active(candidate)のみとし、自動昇格してはならない。用途ラベル(057 の display_name/purpose)で「市場情報利用・精度最優先・意思決定支援には非使用」を明示する
- **FR-007**: serving はモデル切替基盤の model_version 明示指定時のみ本モデルを使用する。オッズ未取得レースは型付きスキップとし、offset なしの縮退予測を黙って返してはならない
- **FR-008**: 監査(憲法 V): 予測の logic_version に市場 offset の使用・q の定義・オッズソースを記録する。オッズが closing-leaning(確定人気寄り)である限界と「本モデルは retrospective 評価が主用途」である旨を spec/モデルメタデータに明示する
- **FR-009**: フル実装前に少数 fold の spike で go/no-go 判定を行う(041/042/058 前例): 市場残差構成が q 単体 baseline を spike 窓で上回らなければ、フル実装に進まず結果を記録して中断する

### Key Entities

- **市場確率 q / 市場 offset**: 対象レースの単勝オッズから導く vote-share とその対数。特徴量スナップショットには含まれない(特徴列ではない)。モデル入力の一部として学習・serving 双方で同一定義により再構成される
- **市場残差モデル(精度最優先モデル第2弾)**: pl_topk 系構成 + 市場 offset で学習された非 active モデル。model_versions 上で用途ラベルにより default と区別される
- **事前登録ゲート判定**: フル walk-forward の win/top2/top3 LogLoss を市場 baseline・lgbm-058-acc と比較した機械判定レコード

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: フル walk-forward OOS で、本モデルの win LogLoss が市場 q 単体 baseline を下回る(参考値: 市場 ≈0.202、現 default 0.21597、lgbm-058-acc 0.21579)
- **SC-002**: default モデルの予測が feature/serving 経路変更の前後でバイト完全一致(実 DB E2E で mismatch 0)
- **SC-003**: オッズ取得済みレースで model_version 指定予測が成功し、オッズ未取得レースは型付きスキップになる(黙った縮退なし)
- **SC-004**: 既存全パッケージのテストが緑のまま(default 経路の回帰ゼロ)
- **SC-005**: 予測レコードの監査情報だけで「市場 offset を使ったか・どの定義か」を後から判別できる

## Assumptions

- `race_horses.odds` は closing-leaning(結果確定人気寄り)であり、本モデルの過去評価は実運用(発走前オッズ)より有利に見える可能性がある(013 以来の既知の限界)。本モデルは retrospective な精度上限の探索と、将来の発走前オッズ取得時の土台として位置づける
- 057 のモデル切替基盤・058 の非 active 併存登録・per-model feature_hash 互換の仕組みは実装済みで再利用できる
- 特徴量セットは features-015 のまま(過去走市場評価 asof_mkt_* を含む 058 構成をベースに使える)
- 買い目・betting への本モデルの利用はスコープ外(deferred)。市場情報を使った買い目方策は `docs/market-aware-betting-policy-proposal.md` の別 feature として扱う
- 発走前オッズのスナップショット取得(netkeiba 追加 scrape)はスコープ外(方針: netkeiba 追加取得はしない)
