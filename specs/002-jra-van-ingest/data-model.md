# Data Model: JRA-VAN 取込マッピング

新テーブルは作らない (Feature 001 の契約に書き込む)。本書は CSV 列 → コアテーブル列の写像と変換規則
を正本化する。列番号は research.md R1 (1-indexed) に対応。

## races (race_id ごとに 1 行、出走馬行から dedup)

| 列 | 由来 | 変換 |
|---|---|---|
| race_id | col4(year)+col6(venue)+col5(kai)+col7(nichime)+col8(race_no) | R2 の導出、`^[0-9]{12}$` 検証 |
| race_date | col4 | `2007.8.11` → date(2007,8,11) |
| venue_code | col6 | R3 の表 (未知はエラー) |
| race_number | col8 | int (1–12) |
| race_name | col10 (空白除去) or col9 | trim、空なら col9 から |
| race_name_short | col9 | そのまま (記号 `*` 含む) |
| race_class | col11 | そのまま |
| grade | col13 | 空 → null |
| track_type | col14 | 芝/ダ |
| distance | col18 | int |
| going | col20 | 良/稍/重/不 |
| weather | col21 | そのまま |
| post_time | — | データに無し → null |

## horses (horse_id ごとに upsert、複数レース・年で 1 行)

| 列 | 由来 | 変換 |
|---|---|---|
| horse_id | col62 血統登録番号 | そのまま (canonical) |
| horse_name | col34 | trim |
| sex | col35 | 牡/牝/セ |
| birth_year | col73 先頭4桁 | `20050419` → 2005 |
| sire_name / dam_name / damsire_name | col67 / col68 / col69 | trim |
| sire_id / dam_id / damsire_id | — | データに ID 無し → null |
| data_source | 固定 | `jra_van` |

## jockeys / trainers (コードごとに upsert)

| テーブル | id | name |
|---|---|---|
| jockeys | col63 騎手コード | col37 騎手名 |
| trainers | col64 調教師コード | col59 調教師名 |

## race_horses (発走前/結果確定時の出走情報、(race_id, horse_id) で upsert)

| 列 | 由来 | 備考 |
|---|---|---|
| race_id / horse_id | 導出 / col62 | 複合 PK |
| sex / age | col35 / col36 | |
| frame / horse_number | col32 / col33 | |
| jockey_id / trainer_id | col63 / col64 | |
| weight / weight_diff | col57 / col58 | 馬体重・増減。未計量は null (Unknown ≠ 0) |
| odds | col43 | **結果確定時オッズ** (発走前特徴量に使用不可, R5) |
| popularity | col42 | **結果確定時人気** (同上) |
| running_style | col52 | 脚質 |
| jockey_weight | col38 | 斤量 |
| entry_status | col40 + 走行データ | R4 (started/cancelled/excluded) |

## race_results (完走・非完走の結果、(race_id, horse_id) で upsert)

**作成条件**: `entry_status='started'` の馬のみ。`cancelled`/`excluded` (DNS) は行を作らない (INV-1)。

| 列 | 由来 | 備考 |
|---|---|---|
| race_id / horse_id | 導出 / col62 | 複合 PK |
| finish_order | col40 | `>=1` で設定。非完走 (0) は **null** (疑似着順にしない) |
| finish_time | col45 | `1.29.9` → interval。非完走は null |
| finish_time_diff | col41 | `----` は null |
| corner_orders | col48-51 | 0 を除いた通過順配列。空なら null |
| last_3f | col53 | numeric。非完走は null |
| result_status | col40 + 失格指標 | R4 (finished/stopped/disqualified) |

## 状態正規化規則 (R4 の要約、enums にマップ)

| JRA-VAN 状況 | 判定 | entry_status | race_results 行 | result_status | finish_order |
|---|---|---|---|---|---|
| 完走 | col40 >= 1 | started | あり | finished | col40 |
| 競走中止 (DNF, 走行あり) | col40=0 かつ 通過順/タイム/オッズあり | started | あり | stopped | null |
| 失格 (DNF) | 上記 + 失格指標 [要 fixture 確定] | started | あり | disqualified | null |
| 出走取消 (DNS, 走行なし) | col40=0 かつ 走行データなし | cancelled | なし | — | — |
| 競走除外 (DNS) | 上記 + 除外指標 [要 fixture 確定] | excluded | なし | — | — |
| 同着 | 複数馬が同一 col40 | started | あり | finished | 共有値 |
| 未知 | 上記いずれにも一意に該当しない | — | — | — | **エラー記録 (FR-012)** |

## 欠損・正規化の原則

- 欠損数値 (馬体重・増減の未計量、ID なしの血統列) は `null` (Unknown)。`0` を代入しない。
- 全角空白パディング (col10 等) は trim。空文字は null 化。
- Shift_JIS は `cp932` でデコード (errors='strict')。デコード不能・列数≠73 は行番号付きエラー。

## ingestion_jobs 監査 (Feature 001 のテーブル + 非破壊な件数列追加)

既存列を使用しつつ、ジョブ時点の件数を後から再現するため**非破壊な列を追加する** (migration 0004、
憲法 VI)。core テーブル集計では再取込後にジョブ時点の件数を復元できず (取消/除外/skip/不正行は core
に残らない)、憲法 V の監査を満たせないため。

migration 0004 は同時に、`<2007` ファイルのスキップを記録できるよう **`JobStatus` に `skipped` を
非破壊追加** する (`ck_ingestion_jobs_status` の CHECK 差し替え + `enums.JobStatus`)。既存 enum は
`queued/running/succeeded/failed/partial` のみで `skipped` が無く、そのままでは CHECK 違反になるため。

既存列:

| 列 | 値 |
|---|---|
| source | `jra_van` |
| job_type | `historical_year` |
| scope / scope_value | `year` / 対象年 (例 `2007`) |
| status | queued→running→succeeded/failed/partial、`<2007` は `skipped` |
| checkpoint | 処理済み行番号 (再開用) |
| error_message | 行番号付きエラー要約 (列数不正・デコード不能・raceId 不正・未知状態) |

追加列 (migration 0004、すべて nullable で非破壊):

| 列 | 型 | 値 |
|---|---|---|
| processed_rows | integer | 取込試行した行数 |
| skipped_rows | integer | スキップ行数 |
| error_count | integer | エラー行数 |
| summary | jsonb | テーブル別件数 `{races, race_horses, race_results}` 等 (ソース差分を吸収) |

将来の netkeiba 取込でも同じ件数列・`skipped` ステータスを共通監査に使い回す。
