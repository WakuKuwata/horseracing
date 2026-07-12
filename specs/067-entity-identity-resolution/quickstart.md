# Quickstart / Validation: Entity Identity Resolution & Split Repair (067)

repair は operator 実行の冪等 CLI。**必ず dry-run で影響範囲を確認してから本実行**する。**repair 中は writer(API の書き込み系・ops worker・ingest・predict)を停止**する(TOCTOU 回避、codex#7)。

前提: ローカル DB(`postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`)。

## 安全な実行順(全体)

```
0. baseline 保存(repair 前の pre-cutoff 特徴・影響対象・最古影響日・マスタ属性差分)
1. writer 停止(ops worker / ingest / predict / profile-completion)
2. resolve-identities --dry-run  → conflict/件数を承認(騎手/調教師は候補一覧を人手承認)
3. resolve-identities            → unmapped→mapped/conflict 昇格
4. repair-splits --dry-run       → 影響行数・衝突・affected_from 確認
5. repair-splits                 → per-pair 原子 re-key + 孤児削除(ingestion_jobs に監査)
6. FK・論理参照(sire/dam/damsire_id)・件数・旧 recommendation 整合を検証
7. features materialize          → parquet 再生成
8. predict-backfill --from affected_from --to 最新 --force  → 新 run(最新=正値)
9. prediction 完全性・確率整合・error 件数をゲート
10. recommend-backfill --from affected_from --to 最新
11. API・過去バックテスト・pre-cutoff parity を検証して cutover(writer 再開)
```

## 0. 現状確認(分裂の実在)

```bash
# サヴォーナ型の分裂例: 過去17走が正規ID・直近2走がサロゲート
#   horses に 2020100734(jra_van) と nk:2020100734(netkeiba) が併存
#   id_mappings で source_id=2020100734 が unmapped
```

## 1. identity 解決(dry-run → 本実行)

```bash
# まず判定だけ(DB 非変更): entity 別 resolved/conflict 件数を確認
uv run --project scrape scrape resolve-identities --entity all --dry-run
# 期待: 馬 resolved≈5,977 / conflict≈2、騎手 resolved≈152 / conflict≈11、調教師 resolved≈198 / conflict≈9

# 本実行(unmapped→mapped/conflict へ昇格)
uv run --project scrape scrape resolve-identities --entity all
```

**検証**: `id_mappings` で mapped 化された行に `canonical_id`/`resolved_at`/`resolution_note` が設定され、conflict 行が resolution_note で理由付き区別される。

## 2. split repair(dry-run → 本実行)

```bash
# 影響行数・衝突・孤児候補のみ集計(DB 非変更)
uv run --project scrape scrape repair-splits --entity all --dry-run
# 期待: rekeyed_rows{race_horses≈37,334, race_results≈37,003, race_predictions≈72,998, feature_snapshots≈72,998}
#       collisions=0, orphans_deleted{horse≈5,977, jockey≈152, trainer≈198}

# 本実行
uv run --project scrape scrape repair-splits --entity all
```

**検証**:
- サヴォーナ: `race_horses`/`race_results` の直近2走が `2020100734` に統合、`nk:2020100734` マスタ行が消え、`horses` に単一行。
- `select count(*) from horses where horse_id like 'nk:%'` が resolved 分だけ減少。
- 冪等: `repair-splits` を再実行して rekeyed_rows=0・orphans_deleted=0。

## 3. 派生値の再生成(既存 CLI, append-only)

`D_FROM` は固定でなく `RepairReport.affected_from`(実 re-key 最古 race_date)を使う。

```bash
D_FROM=<affected_from>; D_TO=$(date +%F)
uv run --project features features materialize                                       # serving用 parquet(DB feature_snapshots は不変)
uv run --project serving serving predict-backfill --from $D_FROM --to $D_TO --force  # 新 prediction run 追加=最新が正値
uv run --project betting betting recommend-backfill --from $D_FROM --to $D_TO        # 新 run に買い目(旧行 JSON ID は repair で canonical 化済み)
```

**検証(統合正当性 = SC-002/007)**:
- 統合された現役馬の 2026 レース予測特徴が「過去走ゼロ」でなく実キャリアを反映。
- 馬詳細 `/horses/2020100734` が 19 走を一体表示(旧 `/horses/nk:2020100734` は canonical へ誘導 or typed 404)。

## 4. parity 回帰(SC-003)

```bash
uv run --project features pytest features/tests/test_repair_parity.py -q
# pre-2025(JRA-VAN期)の全特徴列が統合前後で assert_frame_equal(check_exact=True)
```

## 5. 出血停止の確認(SC-006)

```bash
# ingest-time identity: 既存 canonical と一致する個体を再 scrape/ingest しても nk: 行が増えない
uv run --project scrape pytest scrape/tests -q   # resolve_entity 回帰(canonical 一致→nk:作らない / 新規→従来通り)
```

## 完了条件(Success Criteria 対応)

- SC-001: resolved 件数(馬5,977/騎手163/調教師207 近傍、conflict 除く)。
- SC-004: repair 2 回目 0 変更(冪等)。
- SC-005: conflict/未マッピングは自動統合されない(誤統合 0)。
- 全パッケージ緑(scrape/features)、migration head 不変、OpenAPI 不変。
