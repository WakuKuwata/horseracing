# Data Model: race dispersion & p/q divergence readout

Phase 1。**DB スキーマ変更ゼロ・migration なし**。以下は read-time 計算の応答エンティティ(非永続)と、境界 artifact(ファイル + logic_version、DB 書込なし)。

## 前提: 再利用する既存の read パス(改変しない)

| 既存 | 場所 | 用途 |
|---|---|---|
| `select_prediction_run(session, race_id[, model_version])` | `api/.../selection.py` | 予測 run 選択(014/057) |
| `canonical_win_probs(...)` | `api/.../selection.py` | started・正値の p ベクトル(canonical field) |
| `market_win_probs(...)` | `api/.../selection.py` | 同一 canonical field の q + `canonical_consistent` |
| `canonical_win_odds(...)` | `api/.../queries.py` | started 馬の win odds(010 入力母集団) |
| `divergence_band(p, q)` | `api/.../selection.py` | per-horse 乖離(market_higher/model_higher/similar)= **無改変で再利用** |
| 校正済み p(048 two_gamma) | 既存予測に反映済(表示 p はこれ) | 軸A の p差分の入力 |

## E1. RaceDispersion(軸A・read-time・非永続)

predictions 応答に純追加する nullable オブジェクト。

| フィールド | 型 | 説明 |
|---|---|---|
| `available` | bool | q が canonical field 全頭で有効なら true |
| `unavailable_reason` | str \| null | `available=false` の理由(`no_market_odds` / `partial_market_odds`)。021 `canonical_consistent` とは別 |
| `band` | enum \| null | `firm`/`somewhat_firm`/`standard`/`somewhat_open`/`open`(表示ラベル: 堅い/やや堅い/標準/やや波乱/波乱含み)。`available=false` で null |
| `normalized_entropy` | float \| null | `-Σ q·ln q / ln N`(N=canonical field 頭数)。バンドの根拠 |
| `favorite_win_prob` | float \| null | `max(q)`(本命勝率、生数値併記) |
| `top3_cumulative` | float \| null | q 降順上位3頭の累積(生数値併記) |
| `model_delta` | object \| null | 校正済み p 由来集中度と q 由来の**差分**。`{normalized_entropy_delta, direction: model_more_open/model_more_firm/similar}`。`canonical_consistent=false` で null |
| `odds_as_of` | datetime \| null | 021 と同源。使用オッズの時点 |
| `odds_source` | enum \| null | **021 の `PredictionResponse.odds_source` と同型 = `"final"` \| `"prerace"`**(closing-leaning=final の可能性を front で開示)。`netkeiba` 等の provenance 名は入れない(F1) |
| `is_pseudo` | bool | q 集計は market-derived 表示 → true(015/021 pseudo/source バッジ経路) |
| `boundary_version` | str \| null | 使用したバンド境界 artifact の version(監査)。artifact 不在時は null |

**検証ルール**:
- 取消・非 starter は q 正規化前に除外(canonical field で再正規化)。
- `available=false` の時 band/数値/model_delta は全て null(p フォールバック禁止)。
- `normalized_entropy` は N≥2 でのみ定義(N<2 は available=false)。
- band 割当は境界 artifact の 5分位 edges を厳密前フィットで得た値で行う(read-time は edges を読むだけ)。
- **境界 artifact 不在時(F8)**: 表示計器を落とさず `band=null`・`boundary_version=null` とし、q があれば生数値(entropy/max q/top3)は表示する(`available` は q 可用性のみで決まる=band とは独立)。API 起動失敗にしない。

## E2. RaceDivergence(軸B・read-time・非永続)

| フィールド | 型 | 説明 |
|---|---|---|
| `available` | bool | p/q 揃い かつ `canonical_consistent=true` |
| `summary` | str \| null | 中立文言(例「本命(1番人気)をモデルは低評価」)。事実のみ・買い/妙味/危険を言わない |
| `favorite_direction` | enum \| null | q1位馬に対する `model_higher`/`model_lower`/`similar`。既存 `divergence_band(p,q)` を q1位馬に適用し**次の写像で変換(F2)**: `market_higher→model_lower`(市場>モデル=モデルは本命を低評価)・`model_higher→model_higher`・`similar→similar` |
| `underrated_longshots` | array | モデル上位N頭(既定 top3 by p)に入る低人気(q下位)馬の**事実リスト**: `[{horse_number, popularity_rank, p, q}]`。「買い」と言わない |
| `rank_agreement` | float \| null | **top3 集合一致率に確定(F6)**: model top3(by p)と market top3(by q)の重なり頭数 / 3。Kendall τ は不採用。中立指標 |
| `model_version` | str | どの選択モデルの p か(057) |
| per-horse | 既存 | 各馬の `divergence_band` は既存 selection のまま(本 feature で改変しない) |

**検証ルール**:
- `canonical_consistent=false` で `available=false`(summary/direction/longshots/agreement を抑制)。
- q 欠損レースは available=false(乖離を出せない)。
- 文言テンプレートは固定・中立(損益色・edge/value 語なし)。
- `favorite_direction` の値集合は `model_higher/model_lower/similar` に統一(divergence_band の生語彙 `market_higher` は上記写像で変換してから返す)。

## E3. DispersionBoundary(境界 artifact・ファイル + logic_version、DB 書込なし)

バンド境界を再現・監査するための決定論的 artifact(055/064 同型、parquet/JSON)。

| フィールド | 型 | 説明 |
|---|---|---|
| `metric` | str | `normalized_entropy`(固定) |
| `field_size_buckets` | str | `global`(v1)。v2 は `le8/9-13/ge14` |
| `fit_window` | object | `{date_from, date_to}`(凍結窓、結果非参照) |
| `as_of` | date | 境界フィット時点 |
| `version` | str | 例 `dispbands-v1`。logic_version に記録 |
| `quintile_edges` | array[float] | 4 つの edge(5段の境界) |
| `n_races_fit` | int | フィットに使ったレース数 |

**検証ルール**:
- edges は fit_window 内の**予測子分布(正規化エントロピー)の5分位のみ**から算出(結果を見ない)。
- read-time は edges を読むだけ(再計算しない=in-sample 楽観回避、021/054 規律)。
- artifact は DB から決定論再生成可能(憲法 V)。

## E4. DispersionBandDiagnostic(US3・eval 出力・SECONDARY)

047 SegmentRow 同型の walk-forward OOS 集計(採否ゲートにしない)。

| フィールド | 型 | 説明 |
|---|---|---|
| `band` | enum | 5段のいずれか |
| `n` | int | OOS 窓のサンプル数(fit 窓内は除外) |
| `favorite_loss_rate` | float | 本命(q1位)敗北率(realized chaos の主指標) |
| `high_payout_rate` | float | **1着馬の実現単勝オッズ ≥ 10.0 のレース率(F5・事前登録閾値=10.0、結果を見る前に固定)** |
| `n_void` | int | 分母から除外した void/取消レース数(surface) |
| `ci_low` / `ci_high` | float | Wilson / race-cluster bootstrap CI |
| `separated_from_prev` | bool | 直前バンドと CI で区別可能か(不可なら開示、併合しない) |

**検証ルール(予約規則=結果を見る前に固定・F5/F9)**:
- fit_window と OOS 窓を分離(fit 窓内レースを OOS ラベルしない)。
- **high_payout 閾値 = 1着馬の実現単勝オッズ ≥ 10.0**(固定・データ後変更禁止=047/048 規律)。
- **同着(dead heat)**: q1位馬が勝ち馬の1頭に含まれれば `favorite_loss=False`(本命敗北に数えない)。high_payout は勝ち馬いずれかのオッズが ≥10.0 なら True。
- **cancellation/void**(結果行なし・全頭取消・1着不成立)は分母から**除外**し `n_void` に計上(favorite_loss/high_payout の母数に入れない)。
- `separated_from_prev=false` でも境界を再フィット・併合しない(FR-014)。

## 状態遷移

なし(全て read-time の純計算 + 事前フィット済み境界の参照)。永続状態は既存 predictions/odds のみ。
