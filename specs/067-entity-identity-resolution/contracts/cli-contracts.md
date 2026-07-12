# Contracts: Entity Identity Resolution & Split Repair (067)

外部インターフェース = CLI サブコマンド + 純関数。API/OpenAPI/DB スキーマの変更なし。

## 純関数(scrape/identity.py)

### `normalize_name(name: str) -> str`
NFKC 正規化 + 前後空白除去。馬名照合に使用(026 `_normalize_name` と同型)。

### `strip_markers(name: str) -> str`
騎手/調教師名の先頭見習い/斤量マーカー `△▲☆★◇◆*` を除去。その後 `normalize_name`。

### `classify_identity(entity_type, source_id, candidate_name, candidate_birth_year, canonical_row) -> Resolution`
- 副作用なし・決定論。DB を読まない(canonical_row は呼び出し側が供給)。
- 返り値 `Resolution(status, canonical_id, reason)`(data-model §4)。
- 規則: 馬=名前 exact + 生年一致→mapped / 生年 or 名前不一致→conflict。騎手・調教師=マーカー除去後 prefix 一致→mapped / 不一致→conflict。canonical_row=None→unmapped。

## resolve_entity(scrape/idmap.py, 変更)

**シグネチャ拡張**: 既存 `(session, entity_type, netkeiba_id) -> canonical_or_surrogate` に **候補名・候補生年**を追加(呼び出し側 upsert が供給)。

**新挙動(mapped 行が無い場合)**:
1. 従来どおり mapped+canonical_id があれば canonical を返す(不変)。
2. **新**: `source_id`(==netkeiba_id)と同値の canonical マスタ行(nk: 以外)を検索。存在すれば `classify_identity` を評価。
   - `mapped` → id_mappings に mapped 行を upsert(resolved_at/resolution_note 記録)し canonical を返す。**サロゲート行を作らない**(FR-006)。
   - `conflict` → conflict 行を記録しサロゲートを返す(誤統合しない)。
   - `unmapped` / canonical 不在 → 従来どおりサロゲート + unmapped 行 insert(FR-007, 後方互換)。

**不変条件**: canonical 対応が無い個体はバイト同等の従来動作(既存 scrape テストが緑のまま)。

## CLI(scrape/cli.py, 追加)

### `scrape resolve-identities [--entity horse|jockey|trainer|all] [--dry-run]`
既存 DB の `unmapped` を走査し identity 照合で `mapped`/`conflict` へ昇格。
- 出力: entity 別 resolved / conflict 件数(+ conflict 例示)。
- `--dry-run`: DB 非変更、判定結果のみ表示。
- 冪等: 既 mapped は再評価しない。

### `scrape repair-splits [--entity ...] [--dry-run] [--limit N]`
`mapped` かつサロゲート行が残る個体を対象に、**1 ペア(S→C)= 1 原子トランザクション**で re-key + 孤児削除。
- 前提: 先に resolve-identities 実行(mapped が存在)。**repair 中は writer(ingest/predict/profile-completion)を停止** or advisory lock(TOCTOU 回避)。
- ペア内処理順(原子): (1) 全対象表の衝突を先に検査 → (2) 1 件でも衝突ならペア全体 skip → (3) re-key(race_horses / race_results / race_predictions / feature_snapshots / horses の sire・dam・damsire_id / recommendations の selection JSON horse_id)→ (4) 残存 S 参照ゼロ確認 →(5) 削除前ゲート(canonical 欠損・surrogate 有値の属性列=0)→ (6) マスタ孤児削除 → (7) commit。
- **衝突ガードの実 PK**: race_horses/race_results=`(race_id, C)`、race_predictions/feature_snapshots=`(prediction_run_id, C)`。
- 出力: `RepairReport`(rekeyed_rows / collisions / orphans_deleted / affected_from、data-model §5)。`ingestion_jobs`(job_type=repair_splits)に永続化。
- `--dry-run`: 影響行数・衝突・孤児候補・affected_from のみ集計、**タイムスタンプ含め DB 完全不変**(必須の事前確認、FR-011)。
- 冪等: サロゲート行が無ければ no-op(FR-009)。`--limit` で分割実行可・中断後再実行で残りのみ。
- 衝突: ペア skip + 計上(FR-010, INSERT-ONLY 保護)。

### 後処理(既存 CLI を逐次実行 — 新規実装なし。**append-only 前提**)
`RepairReport.affected_from`(実際に re-key された最古 race_date、固定日にしない)〜最新に対し:
1. `features materialize`(serving 用 parquet 再生成, FR-015。**DB feature_snapshots は更新しない**点に注意)
2. `serving predict-backfill --from <affected_from> --to <D> --force`(該当レースに**新 prediction run を追加**=最新が正値、旧 run は legacy 監査, FR-014)
3. `betting recommend-backfill --from <affected_from> --to <D>`(新 run に買い目再生成, FR-013)。旧買い目の JSON ID は repair の re-key で既に canonical 化済み。

## テスト契約(codex P0 反映)

- **unit(test_identity)**: 馬 exact→mapped / 馬 生年不一致→conflict / 馬 名前欠損→unmapped(insufficient) / 騎手 prefix(江田照↔江田照男)→mapped / 騎手 略記差(石神道↔石神深道)→conflict / canonical 不在→unmapped / マーカー除去(△長浜)/ prefix 境界(空文字・1文字・先頭空白・複数マーカー・Unicode 空白)。
- **integration(test_repair)**:
  - **実 PK `(prediction_run_id, horse_id)` の衝突テスト**、合成衝突でペア全体が無変更(原子性)。
  - サヴォーナ型 re-key 正当性 / 冪等(2 回目 0 変更)/ 孤児削除後 FK・論理参照(sire/dam/damsire_id に dangling 0)整合。
  - **旧単勝 recommendation を含む過去バックテストが repair 前後で同じ着否**(JSON ID canonicalize 検証)。
  - 削除前ゲート: canonical 欠損・surrogate 有値の属性=0 を要求(情報損失ゼロ)。
  - dry-run が timestamps 含め DB 完全不変 / 中断後再実行 / `--limit` 分割 / 同時 ingest 排他。
  - reconciliation: --force 後に最新 run が全出走馬を持ち、error_days=0・recommendation error=0。
- **ingest 回帰**: canonical + 名前一致個体の scrape で nk: 行を作らない / 情報欠損経路(result-only・血統親)は自動昇格しない / conflict・rejected が通常 ingest で mapped へ戻らない(sticky) / 既存呼び出しは情報省略で従来動作。
- **parity(features/test_repair_parity)**: 最古影響日より前の全特徴列が repair 前 baseline と `assert_frame_equal(check_exact=True)`。加えて統合馬の過去走復活・同レース他馬の within-race 特徴変化・統合騎手/調教師の後続別馬への波及・同日結果非混入・血統 self-exclusion が canonical 履歴全体を除外。
- **leak-guard**: `features/` が `id_mappings`/`IdMapping` を import しないことを機械固定。
