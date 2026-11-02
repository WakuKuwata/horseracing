# Data Model: JRA 公式サイトを第 2 の情報源として追加する (176)

**Date**: 2026-11-01 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

スキーマ変更は 2 本の migration(研究 D18): `0023_jra_official_source`(CHECK 置換+来歴 2 表・Phase 1)と `0024_jra_track_conditions`(馬場表・Phase 3)。既存行は 1 行も書き換えない。

## 1. 変更する既存契約

### 1.1 `Source` 列挙(`db/enums.py`)と CHECK 置換(0023)

| 値 | 意味 | 使う表 |
|---|---|---|
| `jra_van` | 年次 CSV(既存) | ingestion_jobs / id_mappings / exotic_odds / race_laps |
| `netkeiba` | netkeiba 取得(既存) | 同上 |
| **`jra_official`** | **公式サイト取得(新規)** | ingestion_jobs(全 jra_* job)/ exotic_odds(公式払戻)/ race_laps(公式ハロンタイム)/ 来歴 2 表。id_mappings には**書かない**が CHECK は `Source.ALL` 由来なので同時に広がる(意図して許容) |

置換する CHECK: `ck_ingestion_jobs_source` / `ck_id_mappings_source` / `ck_exotic_odds_source` / `ck_race_laps_source`(`JOB_SOURCE`)。`official_win_payouts.source` は別定義なので `ck_official_win_payouts_source` を `('netkeiba_result', 'jra_official_result')` に置換し、定数を `OFFICIAL_WIN_PAYOUT_SOURCES = ("netkeiba_result", "jra_official_result")` に広げる(既定値 `netkeiba_result` は不変)。downgrade は `jra_official` / `jra_official_result` の行が 1 行でもあれば中断する。

### 1.2 `ingestion_jobs`(列不変・値の追加)

- `source`: `jra_official`
- `job_type`: `jra_entries` / `jra_changes` / `jra_odds` / `jra_results` / `jra_payouts` / `jra_baba`
- `scope` / `scope_value`: `date` / `2026-11-01`、`race_ids` / `202605041111,...`、`venue_date` / `05:2026-11-01`
- `summary`(JSONB): `parser_version` + contracts/cli.md の名前つき件数。隔離・不一致の**内容**は `error_message`(50 行上限)と標準出力 JSON。
- status: 隔離 1 件以上で errors=0 でも `partial`(研究 D16)。

### 1.3 `race_horses` / `races` / `race_results` / `race_laps` / `official_win_payouts` / `exotic_odds`

列は不変。書く値と規則は contracts/precedence.md。新しい値: `official_win_payouts.source = 'jra_official_result'`、`exotic_odds.source = 'jra_official'`、`race_laps.source = 'jra_official'`。`race_horses.popularity` には公式経路は**書かない**(研究 D9)。

### 1.4 `horses` / `jockeys` / `trainers`(列不変)

公式経路で新規に作る馬は `horse_name` / `sex` / `birth_year`(レース年 − 馬齢)/ `data_source='jra_official'`。騎手・調教師は ID と名前。既存行は名前が違っても触らない(`master_name_mismatch` を報告)。

## 2. 新テーブル(0023)— 来歴

`source` の意味は両表とも「**その項目群の現在値を採用した経路**」(研究 D7)。同値を優先権のある経路が確認したら採用元は移る。拒否された入力は更新しない。`observed_at` は HTTP 応答の取得時刻(呼び出し側が渡す)。`ingestion_job_id` は書いたジョブ(dry-run・repair は NULL)。既存行は作らない(backfill なし)。

### 2.1 `race_horse_field_provenance` — 馬単位の項目群

| 列 | 型 | 制約 |
|---|---|---|
| `race_id` | text | PK・FK races |
| `horse_id` | text | PK・FK horses(race_horses への複合 FK) |
| `field_group` | text | PK・CHECK IN (`entry_static`, `jockey`, `trainer`, `body_weight`, `entry_status`) |
| `source` | text | NOT NULL・CHECK `JOB_SOURCE` |
| `observed_at` | timestamptz | NOT NULL |
| `ingestion_job_id` | uuid | NULL 可・FK ingestion_jobs |
| `created_at` / `updated_at` | timestamptz | TimestampMixin |

| field_group | 書く列(race_horses) | 契機 |
|---|---|---|
| `entry_static` | frame, horse_number, jockey_weight, sex, age | 出馬表(両経路) |
| `jockey` | jockey_id | 出馬表・変更情報(騎手変更) |
| `trainer` | trainer_id | 出馬表 |
| `body_weight` | weight, weight_diff(**対**) | 出馬表(体重公表後)・オッズページ・結果ページ |
| `entry_status` | entry_status | 出馬表・変更情報・結果ページ |

### 2.2 `race_field_provenance` — レース単位の項目群

| 列 | 型 | 制約 |
|---|---|---|
| `race_id` | text | PK・FK races |
| `field_group` | text | PK・CHECK IN (`race_static`, `post_time`, `win_odds`, `place_odds`, `results`, `laps`, `win_payouts`, `exotic_payouts`) |
| `source` | text | NOT NULL・CHECK `JOB_SOURCE` |
| `official_at` | timestamptz | NULL 可(`win_odds` / `place_odds` のみ・140/0014 の races 列と一致) |
| `observed_at` | timestamptz | NOT NULL |
| `ingestion_job_id` | uuid | NULL 可・FK ingestion_jobs |
| `created_at` / `updated_at` | timestamptz | TimestampMixin |

| field_group | 書く列 | 契機 |
|---|---|---|
| `race_static` | races.distance, track_type, race_name, grade, race_class, prize_money, weather, going | 出馬表・結果 |
| `post_time` | races.post_time | 出馬表・変更情報 |
| `win_odds` | race_horses.odds, popularity(全馬)+ races.win_odds_official_at / observed_at | オッズ(両経路)・apply_final_odds(netkeiba) |
| `place_odds` | race_horses.place_odds_low/high, place_popularity(全馬)+ races.place_odds_* | オッズ(両経路) |
| `results` | race_results(初回挿入経路) | 結果 |
| `laps` | race_laps | 結果(公式は行が無いときだけ) |
| `win_payouts` | official_win_payouts(勝ち馬集合) | 結果・払戻 |
| `exotic_payouts` | exotic_odds(dividend 行の集合) | 結果・払戻 |

不変条件:
- INV-P1: 来歴は値と同一トランザクション(savepoint 内)で書く。値だけ・来歴だけが残る状態を作らない。
- INV-P2: `win_odds.official_at == races.win_odds_official_at`、`place_odds.official_at == races.place_odds_official_at`(テストで固定)。読む側は races 側を正とする。
- INV-P3: 既存の由来不明行は作らない。由来不明を netkeiba と決めつけない。
- INV-P4: features / training / serving / betting / api / ops は読まない(静的 leak-guard + 動的試験)。
- INV-P5: 拒否された入力(逆行・低優先・欠落保持・隔離)は来歴を更新しない。

## 3. 新テーブル(0024)— `jra_track_conditions`

| 列 | 型 | 制約 | 意味 |
|---|---|---|---|
| `venue_code` | text | PK・CHECK `^[0-9]{2}$` | JRA 競馬場コード(東京=05) |
| `measured_at` | timestamptz | PK | 測定時刻(JST)。`time_basis='fetched'` の項目は取得時刻 |
| `item` | text | PK・CHECK IN (`moisture_turf_goal`, `moisture_turf_corner4`, `moisture_dirt_goal`, `moisture_dirt_corner4`, `cushion_turf`, `going_turf`, `going_dirt`, `weather`) | 項目コードに測定対象を含める(研究 D10) |
| `time_basis` | text | NOT NULL・CHECK IN (`measured`, `fetched`) | 含水率・クッション=measured(断片の時刻+年補正)、馬場状態・天候(accessJ)=fetched |
| `value_num` | numeric | NULL 可 | 含水率(%)・クッション値 |
| `value_text` | text | NULL 可 | 馬場状態・天候・`data-condition`(hard/wet …) |
| `published_at` | timestamptz | NULL 可 | 公表時刻(v1 の断片には無い=NULL) |
| `fetched_at` | timestamptz | NOT NULL | その値を**採用した最新の取得時刻**(値不変の再取込では動かない) |
| `raw_sha256` | text | NOT NULL | 断片 raw バイトの sha256 |
| `source` | text | NOT NULL・既定 `jra_official` | |
| `created_at` / `updated_at` | timestamptz | TimestampMixin | |

不変条件:
- INV-B1: 同じ断片の再取込で行数不変。値が変われば上書きし `value_changed` を報告、`fetched_at` を進める。
- INV-B2: 年補正=取得日の年を当て、断片の月が取得月より大きければ前年。`measured_at > fetched_at` は `rejected_future` として書かない。天候・馬場状態にクッションの測定時刻を流用しない。
- INV-B3: features が読まない(静的 leak-guard: `jra_track_conditions` / `JraTrackCondition` / `cushion` / `moisture`・列名禁止)+ 動的試験(この表だけに行を入れて parquet と予測がバイト一致)。`source_fingerprint` の対象にも入れない。
- INV-B4: 予測・買い目・API 応答に現れない(SC-008/009)。

## 4. 解析結果の値オブジェクト(永続化しない・`jra/models.py`)

| 型 | 主フィールド | 備考 |
|---|---|---|
| `JraPageRef` | `url`, `cname`, `label`, `kind` | run 内のみ・保存しない(FR-001) |
| `JraResponse` | `url`, `cname_sha256`, `status`, `raw: bytes`, `text: str`, `fetched_at`, `kind` | 復号済み。`raw_sha256` を来歴・fixture に |
| `JraDayPage[T]` | `race_date`, `venue_code`, `kai`, `nichime`, `races: tuple[JraRaceBlock[T], ...]` | 開催日 1 ページ |
| `JraRaceBlock[T]` | `race_id`, `race_no`, `state` ∈ {`published`,`not_yet`,`cancelled`,`parse_failed`}, `payload: T | None`, `error: str | None` | レースごとの判定(FR-008) |
| `JraEntry` | `race: JraRaceHeader`, `horses: tuple[JraEntryHorse, ...]` | 出馬表 1 レース |
| `JraEntryHorse` | `horse_id`(10 桁 or None), `horse_name`, `frame`, `horse_number`(取消は None), `jockey_id`, `jockey_name`, `trainer_id`, `trainer_name`, `weight`, `weight_diff`, `jockey_weight`, `sex`, `age`, `entry_status`, `unknown_id_kinds: tuple[str, ...]` | 未知 ID は None |
| `JraChange` | `race_id`, `kind` ∈ {`cancel`,`exclude`,`post_time`,`jockey`}, `horse_number`, `horse_name`, `new_post_time`, `new_jockey_name` | 変更情報 1 件 |
| `JraWinOdds` | `race_id`, `kind` ∈ {`current`,`prev_day_final`}, `official_at`(JST aware), `rows` | `prev_day_final` は取り込まない |
| `JraWinOddsRow` | `horse_number`, `horse_id`, `win_odds`, `place_low`, `place_high`, `weight`, `weight_diff`, `jockey_weight` | 複勝は範囲 |
| `JraResultRow` | `horse_id`, `horse_number`, `finish_order`, `result_status`, `finish_time`, `margin`, `corner_orders`, `last_3f`, `weight`, `weight_diff`, `popularity` | 人気は監査のみ |
| `JraRaceResult` | `race: JraRaceHeader`, `rows`, `lap_times`, `pace_last_4f`, `pace_last_3f`, `weather`, `going`, `payouts: JraPayoutSet` | |
| `JraPayoutSet` | `by_bet_type: dict[str, JraPayoutBlock]` | 券種ごとの**集合と状態** |
| `JraPayoutBlock` | `bet_type`, `state` ∈ {`not_published`,`complete`,`not_sold`,`refund_only`,`parse_failed`}, `lines: tuple[JraPayoutLine, ...]`, `refunds: tuple[int, ...]`, `error` | 枠連は `bet_type='bracket_quinella'` で解析だけ(保存しない) |
| `JraPayoutLine` | `numbers`, `payout_yen`(円/100), `popularity` | |
| `JraTrackReading` | `venue_code`, `measured_at`, `time_basis`, `item`, `value_num`, `value_text` | |
| `ResolvedResultRow` | `horse_id`(canonical), 結果列 | `_insert_results` の共通入力 |
| `CollisionReport` | `collisions: tuple[Collision, ...]`, `merge_candidates`, `approved_mapping_pending_rekey` | 純関数の出力(両経路) |

## 5. 状態遷移

### 5.1 `race_horses.entry_status`(両 writer 共通・研究 D8)

```
started ──(出馬表/変更情報/結果: 取消)──▶ cancelled
started ──(出馬表/変更情報/結果: 除外)──▶ excluded
cancelled ◀──(変更情報/結果ページのみ)──▶ excluded     # 訂正。出馬表からは不可
cancelled / excluded ──(どの経路の started)──▶ 変化なし    # 古い出馬表の再投入
None(欠落) → 保持
```

### 5.2 オッズ(単勝・複勝それぞれ・両経路・研究 D8)

```
レースに race_results がある(ロック下で再確認)           → netkeiba: settled_fill(odds NULL のみ・来歴不変) / 公式: skip
入力が started 全頭を覆わない                                → skip 全体(odds_partial_field)
incoming.official_at is None and stored.official_at is not None → skip
incoming.official_at <  stored.official_at                   → skip(逆行)
incoming.official_at >  stored.official_at                   → write + 来歴
incoming.official_at == stored.official_at:
    全馬の値が同じ                                            → write(観測の更新)+ 来歴(採用元が公式なら公式へ)
    値が違う & incoming=jra_official                          → write + 来歴 + odds_conflicts
    値が違う & incoming=netkeiba & stored.source=jra_official → skip + odds_conflicts
```

### 5.3 レースの取込状態(ジョブ内・savepoint 単位)

```
page → race block: published | not_yet | cancelled | parse_failed
published → era check(race_date >= 2025-10-11)        : ok | skipped_pre_netkeiba_era
ok → id check                                           : ok | quarantined_unknown_id
ok → races 行確保 → FOR UPDATE → 再読込
   → collision check(両経路共通)                        : ok | quarantined_collision | quarantined_cancel_unresolved
ok → 遷移表・優先規則で値を決定 → 値 + 来歴を書く → savepoint release
```

隔離は書込みゼロ(マスタも作らない)で summary に理由別件数、`error_message` に race_id と対。

## 6. 名前空間契約(FR-009・Key Entities)

| エンティティ | リンク形式 | 抜く桁 | 正規 ID との関係 |
|---|---|---|---|
| 馬 | `accessU.html?CNAME=pw01dud00(\d{10})/XX` | 10 桁・先頭ゼロ保持・文字列 | `horses.horse_id` と同一(血統登録番号) |
| 騎手 | `pw04kmk0(\d{5})` | 5 桁 | `jockeys.jockey_id` と同一(例 `05339`) |
| 調教師 | `pw05cmk0(\d{5})` | 5 桁 | `trainers.trainer_id` と同一(例 `01165`) |

境界つき正規表現で抜く。桁数不一致・非数字・prefix 不一致 → 未知 ID → レース隔離。騎手と調教師の同じ数値は別エンティティ。`nk:` は作らない。`id_mappings` は報告用に読むだけで書かない。名前照合 key = NFKC + 全空白除去 + 067 マーカー除去(異常検知専用・結合キーにしない)。
