# Data Model: JRA-VAN 生データ未使用カラムの活用 (055)

## Migration 0010(head=0009 から、nullable 追加のみ)

| テーブル | 新列 | 型 | 源(生 CSV index) | 備考 |
|---|---|---|---|---|
| race_results | first_3f | NUMERIC | 54 | テン3F 秒。last_3f の対。結果由来=学習ラベル/as-of のみ |
| races | prize_money | INTEGER | 23 | 1着賞金(万円)。レース内定数を検証済み。事前公開のレース条件 |
| horses | owner_name | TEXT | 64 | last-write-wins(馬主変更は稀、026 sire_name 前例) |
| horses | breeder_name | TEXT | 65 | 不変 |
| horses | sire_line | TEXT | 69 | 父系統(〜20 値) |
| horses | damsire_line | TEXT | 70 | 母父系統 |

制約・index 追加なし。既存列・既存 CHECK は不変。downgrade は列 drop。

## ingest layout 追加(layout.py)

`FIRST_3F = 54` / `PRIZE_MONEY = 23` / `OWNER_NAME = 64` / `BREEDER_NAME = 65` / `SIRE_LINE = 69` / `DAMSIRE_LINE = 70`。parser は既存の欠損規律(空文字→None)に従う。EXPECTED_COLUMNS=73 不変。

## features-013 新特徴(FEATURE_VERSION features-012→013)

| 群 | 列 | 種別 | 定義 |
|---|---|---|---|
| pace_first3f | asof_rel_first3f_avg | as-of float64 | 過去走の (first_3f − レース finisher 平均) の expanding 平均。小=先行力 |
| pace_first3f | asof_rel_first3f_best | as-of float64 | 同 cummin(最良=最速前半) |
| pace_first3f | asof_pace_balance_avg | as-of float64 | 過去走の (rel_last3f − rel_first3f) 平均。正=前傾型 |
| owner_breeder | asof_owner_win_rate | as-of float64 | 現馬主の全馬過去勝率(daily cumsum−当日、min_starts=20 未満 NaN) |
| owner_breeder | asof_owner_place_rate | as-of float64 | 同・複勝率(3着内) |
| owner_breeder | asof_breeder_win_rate | as-of float64 | 生産者の全馬過去勝率(同上) |
| race_level | prize_money_log | static float64 | log1p(今走 prize_money)。事前公開情報 |
| race_level | asof_prize_avg | as-of float64 | 過去走レースの log1p(prize) expanding 平均=賞金クラス |
| race_level | prize_rel | 導出 float64 | prize_money_log − asof_prize_avg(昇降級度合い。asof NaN→NaN) |
| sire_line | sire_line | static categorical | 父系統名(NFKC 正規化) |
| sire_line | damsire_line | static categorical | 母父系統名 |

- registry: `FEATURE_GROUPS` に 4 群 11 列を登録。static(prize_money_log/sire_line/damsire_line)は `STATIC_COLUMNS` に追加(materialize 対象外)。prize_rel は static×as-of の導出で builder 内合成(materialize は asof_prize_avg のみ)。
- 025 連携: `build_asof_features` に pace_first3f / owner_breeder / asof_prize_avg のブロックを追加(単一 as-of 源)。`source_fingerprint` に新ソース列を追加(fail-closed)。
- リーク境界: 全 as-of は strictly-before(merge_asof allow_exact_matches=False)+ 跨エンティティ統計は daily cumsum−当日(020 human_form 規律)。first_3f の**今走値は特徴にしない**(結果由来)。

## 監査

- `ingestion_jobs` に再 ingest が既存 job_type で記録される(新 job_type 不要)。
- 採用時は lgbm-055 の metadata に feature_version=features-013・feature_hash が記録される(既存 V 規律)。

## contracts/ について

外部契約(API/openapi/front 型)は一切変更しないため contracts/ は作成しない(FR-007)。本ファイルの migration 表と特徴表が内部契約の正。
