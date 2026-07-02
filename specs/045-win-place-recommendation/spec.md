# Feature Specification: 単勝(win)推奨の製品結線 (Win Recommendation Wiring)

**Feature Branch**: `045-win-place-recommendation`

**Created**: 2026-07-02

**Status**: Draft

**Input**: 「単勝・複勝推奨を製品に結線」— 実査の結果、**複勝(place)は EXOTIC 群として既に生成・表示済み**(238 行)。真の欠落は **win(単勝)のみ 0 行**であり、スコープを「単勝推奨の製品結線」に絞る。win は real 単勝オッズ(race_horses.odds)を使う **is_estimated_odds=False の唯一の券種**で、推定オッズ(double-pseudo)の exotic より EV の信頼度が高い。最も広く買われる券種が製品に出ていない穴を埋める。

## 背景と目的

043 で推奨(exotic + Kelly)は製品に結線されたが、win は 3 点で漏れている:
1. **生成**: `betting recommend-serve` は `generate_kelly_recommendations`(ALL_EXOTIC)のみで、007 の win 生成 `generate_recommendations` を呼ばない
2. **読み出し**: api の `exotic_recommendations` は `BetType.EXOTIC` フィルタで win を除外。win の selection は dict `{"horse_id","horse_number"}`(exotic は list[int])で list[int] 契約に載らない
3. **表示**: 返らないので出ない(win の日本語ラベルは front に既存)

007 の EV ロジック(win_prob × real odds ≥ threshold)は実装・テスト済み。本 feature は結線と整形のみで、新しい EV/確率ロジックを足さない。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 単勝推奨が製品に表示される(読み出し+生成結線) (Priority: P1)

予測+単勝オッズがあるレースで推奨を生成すると、単勝の EV 推奨(real オッズ・EV・pseudo-ROI)が exotic と並んで RecommendationPanel に表示される。win 行は real オッズとして表示され(推定バッジなし)、pseudo-ROI は疑似ラベル付き(p はモデル推定のため)。

**Why this priority**: 本 feature の核。最も実用的な券種を製品に出す。

**Independent Test**: recommend-serve 実行 → win 行が recommendations に永続化 → API が win 行を selection=[馬番] で返す → 画面に単勝行(realオッズ・EVラベル)が表示。

**Acceptance Scenarios**:

1. **Given** 予測+単勝オッズがあるレース, **When** recommend-serve を実行, **Then** win 推奨(EV≥閾値)が永続化され、API/画面が exotic と並べて表示する
2. **Given** win 行, **When** 表示, **Then** 使用オッズは real(推定バッジなし・realソースバッジ)、pseudo-ROI は疑似ラベル付き
3. **Given** EV≥閾値の馬がいないレース, **When** 生成, **Then** win 行 0 は正常(エラーでない)
4. **Given** 既に exotic のみ生成済みの run(043 で生成済みの既存データ), **When** recommend-serve 再実行, **Then** win セットだけ追補され、exotic は重複しない(冪等の bet_type 群単位化)

---

### User Story 2 - 既存 populate 済みレースへの win 追補(backfill) (Priority: P2)

recommend-backfill が win を含む生成に対応し、043/044 で populate 済みのレース群にも win 推奨を追補できる。

**Independent Test**: populate 済み日付で recommend-backfill 再実行 → win のみ追補・exotic は skip、件数集計に反映。

**Acceptance Scenarios**:

1. **Given** exotic 生成済みレース群, **When** recommend-backfill 再実行, **Then** win のみ追補され exotic は重複しない

---

### Edge Cases

- **selection 形式**: win は歴史的に dict、exotic は list[int]。読み出しで [horse_number] に正規化して単一契約(list[int])で返す。horse_number 欠損の win 行は応答から除外(表示不能)。
- **オッズ無し馬**: 007 既存挙動(null odds は EV 対象外)。
- **冪等の細分化**: run 単位の existence チェックを「win 群」「exotic 群」に分け、無い群のみ生成。部分状態(win だけ・exotic だけ)から再実行しても重複しない。
- **Kelly**: win への Kelly stake 付与(016 純関数再利用)は本 feature の判断点。含めない場合 stake_fraction NULL=「—」表示(既存規律)。
- **place との関係**: place は既存 exotic 経路のまま(変更しない)。

## Requirements *(mandatory)*

- **FR-001**: 推奨の読み出しは win 行を含めて返さなければならない。win の selection(dict)は馬番のみの配列に読み出し時正規化し、既存の selection 契約(list[int])を維持する。horse_number を持たない win 行は返さない。
- **FR-002**: win 行は real オッズ(market_odds_used・is_estimated_odds=False)として返り、front で推定バッジなし・real ソース表示になる。pseudo_odds/pseudo_roi は引き続き疑似ラベル付き。
- **FR-003**: `recommend-serve` は単一の一貫セットとして「win 推奨(007)」と「exotic+Kelly 推奨(016)」の両方を生成しなければならない。冪等は bet_type 群単位(win 群 / exotic 群)で判定し、無い群のみ生成する(既存 run への win 追補を可能にし、重複は作らない)。
- **FR-004**: `recommend-backfill` も同じ群単位冪等で win を含めて生成し、生成/スキップ件数の reconciliation を維持する。
- **FR-005**: 新しい EV/確率ロジックを追加しない(007/016 の既存ロジックのみ)。スキーマ変更なし。read-only 014 不変(読み出し整形のみ)。リーク境界不変(結果を選定に読まない・推奨値は特徴に戻さない)。
- **FR-006**: 表示対象 prediction_run 絞り(043)は win 行にも適用される(重複 run の win が混ざらない)。

## Success Criteria *(mandatory)*

- **SC-001**: recommend-serve 後、win 推奨が API から selection=[馬番] で返り、画面に単勝行が real オッズ表示で出る(実 DB 1 レース以上)。
- **SC-002**: exotic のみ生成済みの run に対する再実行で win のみ追補され、exotic 行数は不変(群単位冪等)。
- **SC-003**: backfill 再実行で populate 済み範囲に win が追補され、件数集計が一致する。
- **SC-004**: 既存の exotic/place の表示・生成が回帰しない(全スイート緑・pseudo バッジ不変条件維持)。
- **SC-005**: migration head 不変・014 全 path GET のみ・リーク境界テスト緑。

## Assumptions

- 007 `generate_recommendations` は prediction_run 指定で win EV 推奨を永続化できる(既存・テスト済み)。selection の書式は既存(dict)のまま書き、読み出しで正規化する(win 行は現在 0 のため書式変更も可能だが、007 の契約・既存テストを不変に保つ読み出し正規化を既定とする。plan で最終確定)。
- win への Kelly stake は plan で判断(016 純関数の薄い再利用 or flat で MVP)。
- ops recommend job / front ボタンは recommend-serve を呼ぶだけなので自動的に win 込みになる(変更不要見込み)。

## Deferred（スコープ外）

- win 推奨の的中/回収バックテスト表示(007 backtest は既存・表示は別)
- 複勝の real オッズ(exotic_odds、netkeiba 依存)
- selection 書式の DB 側統一マイグレーション
