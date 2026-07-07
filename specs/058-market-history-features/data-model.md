# Data Model: 過去走の市場評価 as-of 特徴(058)

Phase 1。特徴列とリーク境界・登録・パリティ。スキーマ変更なし・migration なし。

## 新特徴群 `past_market`(features-015、4 列)

すべて per (race_id, horse_id)、float64、Unknown=NaN、timing=PRE_ENTRY、missing=NULL。値は**対象レースより厳密に前の出走のみ**を集約(同日除外)。

| 列 | 定義 | 集約 |
|---|---|---|
| `asof_mkt_rank_avg` | 過去走の人気ランク(人気=オッズ由来)の recent-N 平均。低いほど支持厚い | rolling mean |
| `asof_mkt_rank_norm_avg` | 過去走の (人気ランク / field_size) の recent-N 平均(頭数不変の支持度) | rolling mean |
| `asof_mkt_rank_best` | 過去走の人気ランクの recent-N 最小(最も支持された走) | rolling min |
| `asof_beat_mkt_avg` | 過去走の (人気ランク − 確定着順) の recent-N 平均。正=市場予想を上回る | rolling mean |

- `_RECENT_N = 5`。母集団 = 過去の**出走(started)かつ人気あり**の走。
- beat_mkt は着順(結果)を使うが**過去走の結果**(as-of 安全、023 の finish_diff と同型)。着順欠損(DNF)はその走を beat_mkt から除外。

## リーク境界(FR-001/002/003/004)

- **strictly-before + 同日除外**: `merge_asof(direction="backward", allow_exact_matches=False)`(023 idiom)。今走・同日・未来の人気は特徴に流入しない。
- **今走の人気/オッズは非特徴**: model_input_features に `popularity`/`odds` 名は含まれない。past_market の 4 名は禁止トークン非含有(グローバル名検査通過)。
- **Unknown≠0**: デビュー馬・過去人気欠損は NaN。
- **挙動不変(テストで固定)**:
  - INV-L1: 今走の人気を変えても past_market 不変。
  - INV-L2: 同日他レースを足しても不変。
  - INV-L3: 未来レースを足しても過去行不変(pool-end 非依存)。
  - INV-P1(positive): 過去走の人気を変えると past_market が変わる(実際に過去人気を使用)。

## 登録(registry.py、配線済)

- REGISTRY に 4 列: `FeatureMeta("market_history", PRE_ENTRY, NULL)`。
- FEATURE_GROUPS: 4 列 → group `"past_market"`。
- FEATURE_VERSION: `features-014` → **`features-015`**。
- materialized_columns(): past_market 4 列は STATIC でない → 自動的に materialize 対象。

## パリティ / fingerprint(FR-010、憲法 V)

- build_asof_features 単一源に結線(materialize == in-memory bit 一致)。
- `source_fingerprint` は race_horses 全列ハッシュ → **popularity 自動包含**(backfill fail-closed)。明示変更不要。
- 既存 materialize parity テストが features-015・新 4 列で緑になることを確認(実 DB bit 一致)。

## モデル運用(FR-007/008/009、057 基盤)

- **default(意思決定支援)モデル**: `drop_features=past_market columns` で学習/serving。past_market 非含有=予測不変(SC-005)。
- **精度最優先モデル**: past_market 含む全特徴 + production 構成(pl_topk+TE+isotonic)で学習 → model_versions に**非 active** 登録。057 `set-model-label` で用途「精度最優先(過去市場評価含む)」付与、`predict-backfill --model-version` で予測生成 → 057 切替 UI で閲覧。
- 既存 active(意思決定支援)は不変(eval 合格 ≠ 自動昇格、057 FR-009)。

## 不変(leak / probability / contract)

- past_market はモデル特徴のみ。予測 p→joint(009)不変(IV)。
- スキーマ/API/openapi/migration 変更なし(057 の切替基盤・model_versions メタを利用)。
- default モデルの独立性(p⊥q)維持。
