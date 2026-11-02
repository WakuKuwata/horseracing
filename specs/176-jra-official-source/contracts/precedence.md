# Contract: 二重情報源の書込み規則(両 writer 共通・FR-016)

正本は `scrape/src/horseracing_scrape/precedence.py`(純関数)と `upsert.py`(DB 書込み)。netkeiba 経路(`upsert_entries` / `update_odds` / `update_place_quote` / `backfill_results` / `apply_result_page_settlement` / `upsert_exotic_odds` / `upsert_laps`)と公式経路(`jra/upsert_jra.py`)は**同じ関数**を通る。ops / live は scrape のこれらを呼ぶだけなので自動的に従う。

## 1. トランザクションとロックの順序(全 writer)

```
(トランザクション外) fetch → parse → 検証
BEGIN / SAVEPOINT (レース 1 件・race_id 昇順)
  1. INSERT INTO races (race_id, ...) ON CONFLICT DO NOTHING       -- 行の確保(新規レースの作成競合)
  2. SELECT ... FROM races WHERE race_id = :rid FOR UPDATE          -- ロック
  3. 再読込: race_results の有無・started 集合(race_horses)・既存出走行・来歴 2 表
  4. 判定: 衝突検査 → 遷移表 → 優先規則(純関数)
  5. 値と来歴を書く(同一 savepoint)
RELEASE SAVEPOINT
```

- HTTP 待ちの間はロックを持たない(174 の T−3 捕捉を待たせない)。
- 「結果の有無」は 3 で読む(現行 `update_odds` のロック前読みは廃止)。
- 失敗はその savepoint だけ戻し、job は PARTIAL。

## 2. 衝突検査 `check_entry_collisions(existing, incoming) -> CollisionReport`

入力: 既存出走行 {(horse_id, horse_number, entry_status)}、入力行 {(horse_id, horse_number, entry_status)}。1 件でも該当すれば**レース全体を隔離**(マスタ行も作らない)。

| 検査 | 隔離理由 |
|---|---|
| 同じ馬番に別の horse_id(既存 vs 入力) | `collision:number` |
| 同じ horse_id に別の馬番(取消で None になった馬番は除く) | `collision:id` |
| 既存行の中に重複馬番(started 同士) | `collision:existing_dup` |
| 入力の中に重複馬番・重複 horse_id | `collision:input_dup` |
| 馬番 NULL の既存 started 行 | `collision:null_number` |
| 入力に無い既存 started 行(余剰) | `collision:surplus` |

隔離時の報告: `horses` の名前 key(NFKC+全空白除去+マーカー除去)+生年が一致する (nk: id, 公式 id) の対を `merge_candidates`、`id_mappings` に承認済み(mapped)の対があれば `approved_mapping_pending_rekey`。**どちらも書かない**(再キーは別 spec)。取消馬の馬番未解決は別理由 `quarantined_cancel_unresolved`。

## 3. 項目別遷移表(`field_transition(group, stored, incoming, ctx) -> Decision`)

`Decision` ∈ {`write`, `keep`, `reject`}。`ctx` = {incoming_source, incoming_observed_at, incoming_page_kind, stored_source, stored_observed_at}。

| 項目群 | incoming None | 明示値 | 訂正・逆行 |
|---|---|---|---|
| `entry_status` | keep | `started→cancelled/excluded`: write。`cancelled↔excluded`: page_kind ∈ {changes, results} なら write、entries なら keep | `cancelled/excluded→started`: **reject**(全経路) |
| `jockey` | keep | write。ただし `incoming_observed_at < stored_observed_at` なら reject | 同左 |
| `trainer` / `entry_static` / `race_static` | keep | `stored_source == jra_official and incoming_source == netkeiba` → keep + `static_mismatch`(値が違えば)。それ以外 write(公式は既存値との不一致を `static_mismatch` で報告して write) | — |
| `body_weight` | 対で keep(weight だけ・diff だけの COALESCE はしない) | 対で write(jockey と同じ時刻規則) | — |
| `post_time` | keep | `stored_source == jra_official and incoming_source == netkeiba` → keep。それ以外 write | — |

既存の `never_blank` は「None は keep」の実装として残り、体重は**対**として扱う拡張が入る。

## 4. オッズの共通契約(`odds_decision(kind, stored, incoming, started) -> Decision`)

`kind` ∈ {win, place}。両経路・両券種で同じ。

| 条件(上から評価) | 判定 |
|---|---|
| レースに `race_results` がある(ロック下) | netkeiba win: `settled_fill`(odds NULL の行だけ埋める・来歴不変・既存どおり)/ 公式・place: `skip` |
| 入力が started 全頭を覆わない | `skip`(`odds_partial_field`)— 一部の馬だけ更新しない |
| `incoming.official_at is None and stored.official_at is not None` | `skip` |
| `incoming.official_at < stored.official_at` | `skip`(逆行・`odds_skipped_stale`) |
| `incoming.official_at > stored.official_at`(または stored None) | `write` |
| 同時刻・全馬同値 | `write`(観測の更新。来歴の採用元は優先権のある経路へ) |
| 同時刻・異値・incoming=jra_official | `write` + `odds_conflicts` |
| 同時刻・異値・incoming=netkeiba・stored.source=jra_official | `skip` + `odds_conflicts` |
| 同時刻・異値・両方 netkeiba | `write`(既存どおり) |

`write` は全馬の値 + `races.{win,place}_odds_official_at/observed_at` + 来歴を同じ savepoint で。複勝の現行「時刻不明なら保存 official_at を NULL にする」は廃止(上表に従う)。

## 5. 結果(`_insert_results(session, race_id, rows, *, mode, source, observed_at, job_id)`)

| mode | 使う経路 | 規則 |
|---|---|---|
| `race_atomic` | 公式 | `race_results` に当該レースの行が 1 行でもあれば**挿入しない**(`results_existing_races`・不一致は `results_mismatch`)。全行を先に検証し 1 行でも不正なら挿入 0(parse_failed)。来歴 `results` を書く |
| `per_horse_legacy` | netkeiba | 現行どおり: 馬単位 INSERT … ON CONFLICT DO NOTHING、`corner_orders` の NULL 補完、`running_style` の NULL 補完(列挙された例外・FR-014)。初回挿入時のみ来歴 `results` を書く |

`apply_final_odds` は公式経路から呼ばない。結果ページの馬体重は体重の遷移表で、人気は**書かない**。ハロンタイムは `upsert_laps(source=)` で、公式は行が無いときだけ(`laps_existing` を報告)。

## 6. 払戻(券種ごとの集合)

| 券種ブロックの状態 | 書込み |
|---|---|
| `complete` | (race_id, bet_type) の集合として置換: 公式集合に無い既存行を削除、値が違う行を更新、同値の行は値を触らず `source` と来歴だけ公式へ |
| `not_published` | 何もしない(既存行を消さない) |
| `not_sold` / `refund_only` | 行を作らず件数を報告。既存行があれば**削除しない**(訂正は `complete` でだけ起きる) |
| `parse_failed` | その券種だけ無書込み・PARTIAL |

対象は `BetType` の 7 券種(単勝は `official_win_payouts`、他 6 は `exotic_odds`)。枠連は `not_mapped_bet_types` に件数のみ。来歴 `win_payouts` / `exotic_payouts` が `jra_official` のレースでは netkeiba の払戻 writer は `skip`(`skipped_official_payouts`)。

## 7. JRA-VAN 行の保護

公式経路は `races.race_date < 2025-10-11` のレースに一切書かない(`skipped_pre_netkeiba_era`)。netkeiba 経路の既存保護(結果 INSERT-ONLY・確定オッズの fill-if-null・`NETKEIBA_FINAL_ODDS_FROM`)は不変。
