# Data Model: 実 exotic オッズ取込と疑似→実 ROI 化

新テーブル `exotic_odds`(0001–0004 以降で初の新テーブル、006–011 はスキーマ変更なし)+ 既存 `recommendations`/`race_horses`/`race_results`/`ingestion_jobs` の
読み書き。以下はスキーマ・selection 形・上書き規律・不変条件・乖離レポート。

## 1. ExoticOdds(新テーブル `exotic_odds`)

| 列 | 型 | 説明 |
|---|---|---|
| `race_id` | text FK→races | JRA-VAN 12 桁(future は有効 ID のみ) |
| `bet_type` | text | place/quinella/exacta/wide/trio/trifecta(CHECK、win 除外) |
| `selection` | JSONB | 011 と同一の素の配列(順序券種=順序付き/無順序=昇順整列/複勝=`[i]`) |
| `odds` | numeric | **最新**配当オッズ(レース前=事前、確定後=最終配当) |
| `coverage_scope` | text | full(期待件数テストで完全グリッド証明)/ partial(CHECK) |
| `source` | text | netkeiba(将来他源を許容) |
| `created_at` / `updated_at` | timestamptz | TimestampMixin。**履歴なし、最新値で上書き**(憲法 V) |

- **主キー/一意**: `UNIQUE(race_id, bet_type, selection)` 複合 B-tree(JSONB 等価)。surrogate PK(uuid)+ UNIQUE 制約でも可。
- **不変**: 1 組み合わせ 1 行(履歴行を作らない)。`odds>0`(無効/0 は格納しない)。
- **上書き規律**: `ON CONFLICT (race_id, bet_type, selection) DO UPDATE SET odds=excluded.odds, updated_at=now()`。
  win オッズと違い**結果確定後も最新スクレイプ(=最終配当)で上書き**(netkeiba 単独源、JRA-VAN 保護対象なし)。

## 2. Selection(JSONB 安全形 — 011 と同一)

| bet_type | selection | 例 |
|---|---|---|
| place | 単一要素 `[i]` | `[5]` |
| quinella | 整列2(昇順) | `[3,7]` |
| exacta | 順序2(着順) | `[7,3]` |
| wide | 整列2(昇順) | `[3,7]` |
| trio | 整列3(昇順) | `[1,3,7]` |
| trifecta | 順序3(着順) | `[3,7,1]` |

- 生成は 011 の `to_selection` を**唯一の経路**として共有(scrape も同一規則、テストで一致保証)。突合キー = `(bet_type, tuple(selection))`。

## 3. 上書き規律(odds_phase なし・憲法 V)

| 状態 | exotic_odds.odds | 推奨 | バックテスト |
|---|---|---|---|
| レース前(result-pending) | 事前オッズ(最新で上書き) | 決定時値を recommendations.market_odds_used にスナップショット | — |
| 結果確定後 | 最終配当(最新で上書き) | — | 過去レースの最新値=最終配当を実払戻に使用 |

- 履歴は持たない。決定時の監査は recommendations 行(market_odds_used + computed_at + logic_version)が担保。
- **リーク・ガード(重要)**: ライブ推奨はレース前の `exotic_odds`(=事前オッズ)を EV 入力に使える。一方**バックテストの買い目決定は
  実オッズを入力にしない**(過去レースの `exotic_odds` は最終配当に上書き済み=後知恵)。バックテストは選定を 011 の推定 O_est
  (事前に得られる)で行い、**実最終配当は payout/採点のみ**に用いる(II リーク境界)。`generate_exotic_recommendations` の実オッズ
  利用は result-pending(レース前)を想定。

## 4. recommendations への実オッズ配線(既存テーブル・新規列なし)

| 列 | 実オッズヒット | 推定フォールバック(011) |
|---|---|---|
| market_odds_used | **実 exotic オッズ** | null |
| estimated_market_odds_used | null | O_est(010/011) |
| is_estimated_odds | **false** | true |
| pseudo_odds | 1/P_model | 1/P_model |
| pseudo_roi | EV−1(EV=P_model×実オッズ) | EV−1(EV=P_model×O_est、二重疑似) |

- 行単位で実/推定を区別(同一レースで混在可、各行は一方のみ)。突合は to_selection 同一配列で完全一致。

## 5. 採点(exotic_roi 拡張)

- `score_exotic` を拡張: 実 final オッズがある的中買い目は payout=stake×**実オッズ**(実 ROI、`pseudo=false`)、無ければ
  stake×O_est(疑似、`pseudo=true`)。**実払戻と疑似払戻をラベル分離**集計。
- **推奨後取消**: 推奨時に存在した馬が後で取消 → その買い目は **void/skip**(payout 計上せず監査)。011 の dead-heat/None 規律は継承。

## 6. 乖離レポート(exotic_divergence、非永続)

| エンティティ | フィールド |
|---|---|
| `DivergenceReport` | bet_type, coverage_rate(実が存在した組み合わせ割合), n_pairs, log_ratio_median, log_ratio_mae, log_ratio_p90, baseline="estimated(010/011)", pseudo_label |

- `log_ratio = log(実オッズ / 推定 O_est)`。推定= baseline、実=実測、推定側は二重疑似明示。カバレッジ率を必ず併記(部分カバーを
  全カバーと誤認しない)。

## 7. ingestion_jobs 監査(既存)

- `job_type='exotic_odds'`、`status`(succeeded/partial/failed)、`summary`(券種別の期待/観測/欠損組み合わせ数、unmapped 件数)。
- 部分取得 → `status=partial` + `coverage_scope=partial`。

## 8. 不変条件まとめ

1. selection は 011 と同一 JSONB 安全配列(`to_selection`)で突合完全一致(R1)。
2. `exotic_odds` は 1 組み合わせ 1 行・最新値上書き・履歴なし(憲法 V、R2)。
3. netkeiba ID は id_mappings 経由のみ(guess-join ゼロ、I)。
4. exotic オッズはモデル特徴に一切使わない(II)。
5. 実オッズ=実評価、推定=疑似評価をラベル分離(V、R6)。
6. 冪等 + ingestion_jobs 監査(R3)。決定論。
7. future race_id は有効 JRA-VAN 12 桁のみ書込(I)。
