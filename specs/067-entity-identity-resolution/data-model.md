# Data Model: Entity Identity Resolution & Split Repair (067)

スキーマ変更ゼロ。既存テーブル/列のみを用いる。以下は「本 feature が読み書きする既存構造」と「状態遷移・分類」の記述。

## 1. id_mappings(操作の中心 / 既存, 変更なし)

`db/src/horseracing_db/models/ingestion.py`。列(既存): `id_mapping_id`, `entity_type`(horse/jockey/trainer), `source`(netkeiba), `source_id`, `canonical_id`(nullable), `mapping_status`(enum), `conflict_group_id`, `resolved_at`, `resolution_note`, timestamps。UNIQUE(entity_type, source, source_id)。

### mapping_status 状態遷移(本 feature が駆動)

```
unmapped ──[番号==canonical & 名前照合OK & (馬は生年一致)]───▶ mapped   (canonical_id, resolved_at, resolution_note を設定)
unmapped ──[番号一致 & 名前/生年が「矛盾」]─────────────────▶ conflict (resolution_note に理由; 自動 re-key しない)
unmapped ──[番号一致だが照合に必要な情報が「欠損」]──────────▶ unmapped (insufficient_evidence; 欠損≠矛盾, codex 是正)
unmapped ──[canonical 対応なし(番号不一致)]─────────────────▶ unmapped (真の未マッピング=地方/新馬)
```

**codex#5 の是正**: 「欠損」と「矛盾」を区別する。番号一致でも照合名/生年が**欠損**(例: result-only 取込で名前が無い)なら `conflict` にせず `unmapped`(insufficient_evidence)に留める。`conflict` は積極的な矛盾(名前・生年が食い違う)にのみ用いる。conflict/rejected は sticky(通常 ingest で mapped へ戻さない)。

- `mapped` のみが repair の re-key 対象。
- `conflict`/`unmapped` は re-key しない(手動フロー/対象外)。
- 冪等: 既に `mapped`/`conflict`/`rejected` の行は resolve で再評価しない(no-op、sticky)。
- `resolution_note` 例: `identity:horse;id==canonical;name=exact;birth=match` / `identity:jockey;id==canonical;name=prefix` / `conflict:jockey;name=prefix_fail(石神道≠石神深道)` / `conflict:horse;birth_year 2019≠2020` / `insufficient:horse;name_missing(result-only)`。

**騎手/調教師の初回 backfill(codex 推奨)**: prefix 一致は自動 `mapped` 候補として算出するが、番号名前空間の意味的同一(免許再利用・外国人短期免許・同姓 prefix の別人)を人手で確認するため、**dry-run の候補一覧を operator が承認してから repair 本実行**する(承認ゲート)。将来 ingest では番号名前空間検証後に prefix を用いる。

## 2. マスタ(統合の帰着先 / 既存, 行削除のみ)

`horses`(horse_id PK), `jockeys`(jockey_id PK), `trainers`(trainer_id PK)。
- canonical 行(`data_source=jra_van`)は不変。
- 解決済みサロゲート行(`nk:` prefix, `data_source=netkeiba`)は、参照を全 re-key 後に**削除**。
- canonical マスタが常に存在することが re-key の前提(identity 一致=番号一致=canonical 行あり、を保証)。

## 3. ID 保持明細(re-key 対象)

| テーブル | ID 列 | 分類 | nk: 行数(実測) | 処理 |
|---|---|---|---|---|
| `race_horses` | horse_id, jockey_id, trainer_id | source-of-truth | 37,334 | re-key(3 列すべて対象) |
| `race_results` | horse_id | source-of-truth | 37,003 | re-key |
| `race_predictions` | horse_id | derived(値 stale) | 72,998 | re-key(FK 整合)→ 旧 run は **legacy 監査**、後段の新 force run が最新=API 正値 |
| `feature_snapshots` | horse_id | derived(値 stale) | 72,998 | re-key(FK 整合)→ 旧 snapshot は legacy。**materialize は DB 非更新**、新 force run 実行時に canonical で新規 snapshot |
| `horses` | sire_id, dam_id, damsire_id(生ID文字列, 非FK) | logical ref | — | **re-key(codex#3)**: 他馬の血統親参照に残る `nk:` を canonical へ。放置すると論理 dangling |
| `recommendations` | selection(JSONB 内 horse_id/horse_number) | derived | — | **JSON 内 horse_id を物理 canonicalize(codex#1)**。backtest が JSON の horse_id を結果に直接照合するため「再生成のみ」では旧行が `nk:` のまま不一致 |

注1: `race_horses` は jockey_id/trainer_id も保持するため、馬・騎手・調教師 3 種の re-key がこのテーブルに集約される。

注2(codex#1 の是正): 既存 CLI の実挙動は **append-only** — `serving predict-backfill --force` は旧 run を上書きせず**新 prediction run を追加**、`features materialize` は parquet を書くだけで **DB `feature_snapshots` を更新しない**、`recommend-backfill` も新規行追加のみ。したがって「re-key してから materialize/force で上書き」は成立しない。設計上の帰結: (a) 派生表は FK 整合のため re-key、(b) 旧 run/snapshot は **legacy 監査記録**として保持(API の select_prediction_run は active→最新なので新 force run を返す)、(c) recommendations の JSON ID は物理 canonicalize が必須(監査互換のため)。

### re-key の不変条件(per surrogate→canonical ペア = 原子トランザクション, codex#6)

- **粒度**: **1 サロゲート S → 1 canonical C を 1 トランザクション**(entity 全体/全件 1 トランザクションは不採用=ロック時間・rollback・再開コスト過大)。
- **手順(原子)**: (1) 全対象表の衝突を**先に**検査 → (2) **1 件でも衝突があればペア全体を無変更で skip**(部分統合を作らない) → (3) 全 ID 参照を re-key → (4) 残存 S 参照ゼロを確認 → (5) マスタ孤児削除 → (6) commit。
- **衝突ガードの実 PK(codex#2 修正)**:
  - `race_horses` / `race_results`: `(race_id, C)` 非存在。
  - `race_predictions`: **`(prediction_run_id, C)`** 非存在(実 PK は `(prediction_run_id, horse_id)`、race_id は runs 側)。
  - `feature_snapshots`: **`(prediction_run_id, C)`** 非存在(feature_version は PK でない)。
  - 衝突時はペア skip し `collisions` に計上・報告(現状 0 だが防御的必須=INSERT-ONLY 保護)。
- **削除前ゲート(codex#3)**: canonical で欠損・surrogate で有値の属性列(sex/birth_year/pedigree/owner/breeder 等)が **0 件**であることを確認してから削除(属性 fill-null は pre-2025 parity を崩すため行わない=情報損失を検知したら該当ペアを保留)。
- **冪等**: S 行が既に存在しない(前回 re-key 済み)なら no-op。
- **jockey/trainer の re-key**: race_horses の jockey_id/trainer_id を対象。マスタ(jockeys/trainers)の nk: 行は参照消滅後に削除。
- **TOCTOU(codex#7)**: dry-run と本実行の間の競合を避けるため、repair 中は ingest/predict/profile-completion(writer)を停止(保守時間帯)または advisory lock。regen 完了まで serving cutover を制御。

## 4. Resolution(純関数の返り値 / 新規, in-memory のみ)

`scrape/identity.py` の `classify_identity(...) -> Resolution`。永続化しない値オブジェクト。

```
Resolution:
  status: mapped | conflict | unmapped
  canonical_id: str | None      # mapped のとき source_id(==既存 canonical)
  reason: str                   # resolution_note に転記する監査文字列
```

判定入力: `entity_type`, `source_id`, `candidate_name`(scrape 名), `candidate_birth_year`(馬のみ, nullable), `canonical_row`(番号一致する既存マスタ行 or None)。

判定入力の可用性(codex#4): `candidate_name`/`candidate_birth_year` は取込経路により欠損しうる — entries は名前+**年齢**(生年は race_date から導出、規則を明文化・検証)、results は ID のみ、血統親は ID のみ。identity 情報が不足する経路では**自動昇格しない**(insufficient_evidence=unmapped)。

判定規則(research R2):
- `canonical_row is None` → `unmapped`。
- 情報欠損(candidate_name が無い等) → `unmapped`(insufficient_evidence)。
- 馬: NFKC(candidate_name)==NFKC(canonical.name) かつ birth_year 一致 → `mapped`。名前一致+生年**不一致** → `conflict`。名前**不一致** → `conflict`(矛盾)。
- 騎手/調教師: strip_markers+NFKC 後に双方向 prefix 一致 → `mapped`(初回 backfill は operator 承認ゲート付き)。prefix **不一致** → `conflict`。

## 5. Repair 集計(CLI 返り値 + ingestion_jobs に永続化)

```
RepairReport:
  repair_run_id:   str            # ingestion_jobs に紐づく監査 ID
  resolved:        {horse, jockey, trainer} 別 mapped 化件数
  conflicts:       {...} 別 conflict / insufficient 件数(+ 例示)
  rekeyed_rows:    テーブル別 UPDATE 行数(race_horses/race_results/race_predictions/feature_snapshots/horses[血統ID]/recommendations[JSON])
  collisions:      テーブル別 skip 件数(+ 例示)
  orphans_deleted: マスタ別削除件数
  affected_from:   date           # 実際に re-key された最古 race_date(codex#7, 固定日でなく算出)
  dry_run:         bool
```

**監査永続化(codex#8, 憲法 V)**: スキーマ変更なしで、既存 `ingestion_jobs`(job_type=`repair_splits`)の `summary` に repair run ID・mapping 集合ハッシュ・対象期間(affected_from)・件数・衝突・ツール版を記録。RepairReport を非永続に留めない。

## リーク境界(憲法 II)

- `id_mappings`・Resolution・RepairReport はモデル特徴に流入しない。`features/` は `id_mappings`/`IdMapping` を import しない(既存の未 import 状態を維持=leak-guard)。
- 統合は「同一個体の履歴の 1 本化」であり as-of strictly-before を変えない。血統自馬除外の二重計上(分裂由来)は統合で解消=リーク低減。
