# Implementation Plan: Entity Identity Resolution & Split Repair

**Branch**: `067-entity-identity-resolution` | **Date**: 2026-07-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/067-entity-identity-resolution/spec.md`

## Summary

netkeiba スクレイプが `nk:{netkeiba_id}` サロゲートで馬・騎手・調教師を別レコード取り込みし、`id_mappings` を `unmapped→mapped` に昇格させる production コードが存在しないため、同一個体が 2 つのIDに分裂している(馬 5,977・騎手 163・調教師 207)。features/serving は生のID文字列で as-of 集約するため、2026 年(=100% netkeiba)の現役馬の予測が「過去走ゼロ=デビュー扱い」の誤特徴で計算される silent degradation を起こしている。

**技術アプローチ**: (1) `id_mappings` を **identity 照合**(source_id が既存 canonical ID と一致 + entity 別の名前照合)で `mapped` へ解決する純関数 resolver を追加、(2) ingest 時に同 resolver を通し今後サロゲートを作らない、(3) 解決済みマッピングに紐づく ID 保持行(race_horses / race_results / race_predictions / feature_snapshots)を canonical ID へ**物理 re-key** する冪等・衝突ガード・dry-run 付き backfill CLI、(4) repair 後に既存 CLI(features materialize → serving predict-backfill --force → betting recommend-backfill)で派生値を再生成。スキーマ変更ゼロ・migration なし・FEATURE_VERSION 不変(データ修復であって特徴変更でない)。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: SQLAlchemy 2.0 / psycopg3(DB re-key)、既存 `scrape`(idmap/upsert)、`db`(共有 ORM モデル)、後処理は既存 `features materialize` / `serving predict-backfill` / `betting recommend-backfill` / `live refresh` CLI を再利用(新規オーケストレーション最小)。

**Storage**: PostgreSQL 16。対象テーブル=`id_mappings`(解決状態)、`horses`/`jockeys`/`trainers`(マスタ)、`race_horses`/`race_results`/`race_predictions`/`feature_snapshots`(ID保持明細)、`recommendations`(JSON内ID=再生成対象)。

**Testing**: pytest + testcontainers(実 Postgres)。resolver は純関数の unit、repair は testcontainer integration(冪等・衝突ガード・re-key 正当性)、parity は features の byte-parity 回帰。

**Target Platform**: operator 実行の CLI(Linux/macOS)。migration/常駐サービス変更なし。

**Project Type**: データ修復 + ingest ロジック改修(バックエンドのみ、UI 変更は最小=旧サロゲートURLの解決のみ任意)。

**Performance Goals**: repair は operator バッチ。re-key 対象 ≈ facts 74k 行(race_horses 37,334 + race_results 37,003)+ derived 146k 行(race_predictions/feature_snapshots 各 72,998)。単発完了想定、逐次トランザクション + per-entity 冪等。

**Constraints**: 憲法 II(as-of strictly-before / 自馬除外 / id_mappings を特徴に流入させない)、JRA-VAN 結果 INSERT-ONLY(re-key 先 PK 既存なら上書き禁止=衝突スキップ)、feature_hash/parquet bit-parity・採用済み lgbm-061(features-016)予測不変、冪等 ingest、id_mappings 監査。

**Scale/Scope**: 統合対象=馬 5,977 / 騎手 163 / 調教師 207。conflict 送り見込み=馬 2(生年不一致)+ 騎手 ~11 + 調教師 ~9(名前 prefix 不一致)。真の未マッピング(番号不一致 38%)は不変。

## Constitution Check

*GATE: Phase 0 前に PASS 必須。Phase 1 後に再確認。*

- [x] **I. データ契約**: `raceId` 12桁・2007+ は不変。**本 feature の中核が「JRA-VAN/netkeiba ID を `id_mappings` 経由で結合」の原則の未実装部分を埋めるもの**。identity 照合(source_id==canonical ID + 名前照合)は「推測結合(名前だけ/番号だけ)」ではなく**公式ID(馬=血統登録番号 / 騎手・調教師=免許番号)の構造的同一 + 裏取り**であり、対応が取れない曖昧ケースは自動結合せず conflict(手動フロー)に載せる=憲法 I の「推測で結合してはならない/手動修正フロー」に準拠。**PASS**。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: identity 解決・re-key はデータ修復でありモデル特徴を変更しない。`id_mappings`・解決結果は特徴に流入しない(既存 leak-guard 型を踏襲、features は id_mappings を import しない)。as-of strictly-before は不変で、統合は「同一馬の履歴を正しく 1 本化」=リーク低減(血統自馬除外の二重計上を解消)。**PASS**。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: モデル/特徴ロジックは不変のため新規採用ゲートは不要。代わりに **parity 回帰(pre-2025 の特徴が統合前後でバイト一致)** と **統合正当性(サヴォーナ型で直近レースの過去走が復活)** を検証ゲートとする。FEATURE_VERSION bump なし=採用済みモデルの再評価不要。**PASS(該当外を明記)**。
- [x] **IV. 確率整合性**: 予測値の計算式は不変。re-key 後の再予測(predict-backfill --force)は既存 009 経路をそのまま通すため Σ整合・取消除外・Unknown/0 区別は不変。**PASS**。
- [x] **V. 再現性・監査**: 各解決に根拠(identity 種別 + 照合結果)と解決時刻を `id_mappings.resolution_note`/`resolved_at` に記録。repair は dry-run で影響範囲を事前提示、logic 版を記録。**PASS**。
- [x] **VI. feature 分割規律**: スキーマ変更ゼロ(`id_mappings` の canonical_id/resolved_at/resolution_note/conflict_group_id 列は既存)。UI 変更は最小(旧サロゲートURL解決=任意)で API/DB 契約先行の対象なし。**PASS**。
- [x] **品質ゲート(codex second opinion)**: **取得済み**(volta で CLI 0.144.1 に更新して復旧、read-only で成果物+実コード読解)。P0 指摘 8 件を全採用(research R6 の C1–C8): 派生の append-only 是正・衝突ガード実キー修正・血統 ID re-key・ingest 照合入力の可用性・SC 件数・トランザクション粒度・writer 停止・監査永続化。物理 re-key の採用と馬 exact 照合・as-of 波及範囲は妥当と確認。**PASS**。

**Gate 結果**: I–VI + 品質ゲート すべて PASS(codex 指摘反映済み)。

## Project Structure

### Documentation (this feature)

```text
specs/067-entity-identity-resolution/
├── plan.md              # This file
├── research.md          # Phase 0: identity 照合規則・re-key vs 仮想統合・parity 論拠・codex 代替レビュー
├── data-model.md        # Phase 1: id_mappings 状態遷移・ID保持テーブル一覧・re-key/regen 分類
├── quickstart.md        # Phase 1: dry-run→resolve→repair→regen→検証の実行手順
├── contracts/
│   └── cli-contracts.md  # Phase 1: resolver 純関数 + CLI サブコマンドの入出力契約
└── tasks.md             # Phase 2(/speckit-tasks で生成)
```

### Source Code (repository root)

```text
scrape/src/horseracing_scrape/
├── idmap.py             # [変更] resolve_entity に identity 照合を注入(ingest-time, FR-006/007)
├── identity.py          # [新規] 純関数: classify_identity(entity_type, source_id, name, birth_year, canonical_row) -> Resolution
├── repair.py            # [新規] resolve_identities() / repair_splits() コア(冪等・衝突ガード・dry-run)
└── cli.py               # [変更] サブコマンド resolve-identities / repair-splits を追加

scrape/tests/
├── unit/test_identity.py        # [新規] 照合規則(exact馬/prefix騎手/生年conflict/未一致unmapped)
└── integration/test_repair.py   # [新規] re-key 正当性・冪等・衝突スキップ・孤児削除・FK 整合

features/tests/
└── test_repair_parity.py        # [新規] pre-2025 特徴が統合前後でバイト一致(strictly-before 論拠の実証)
```

**Structure Decision**: 新規パッケージは作らない。identity 照合と repair は `id_mappings`/upsert を所有する `scrape/` に置く(既存境界どおり=`scrape` は `db` 共有モデルを import 可、ML パッケージは import しない)。repair が触る `race_predictions`/`feature_snapshots` は `db` の共有 ORM モデル経由で read/write するのみ(ML ロジック非依存)。後処理の再生成は **既存 CLI の逐次実行**で賄い、新規オーケストレーション層は作らない(必要なら `live refresh`(050)を範囲指定で流用)。

## 主要設計判断(詳細は research.md)

1. **repair 方式 = 物理 re-key**(仮想統合=全読み経路で id_mappings を辿る、は不採用)。実 DB で PK 衝突 0・netkeiba レースは純 netkeiba(混在 0)を確認済み。物理 re-key は features/serving/api を無改修で自然統合でき、read 経路の取りこぼしによる silent 不整合を避けられる。
2. **identity 照合は entity 別**:
   - **馬**: NFKC 正規化後の**名前 exact 一致**(実測 5,977/5,977)+ 生年一致。生年不一致(2 頭)は `conflict`。
   - **騎手/調教師**: netkeiba 名は短縮形 + 見習いマーカー(△▲☆)付きのため exact 不可(騎手 exact 15/163)。**マーカー除去 + NFKC 後の双方向 prefix 一致**を裏取りに(騎手 152/163・調教師 198/207 が救済)。prefix 不一致(騎手 ~11・調教師 ~9=略記スキーム差)は `conflict`(手動)。番号は JRA 免許番号=構造的同一の主根拠、名前 prefix は裏取り。
3. **re-key と regenerate の分離(codex#1 是正)**: source-of-truth(race_horses / race_results)は re-key。derived(race_predictions / feature_snapshots)も FK 整合のため re-key するが、**既存 CLI は append-only**(predict-backfill --force=新 run 追加・materialize は DB 非更新)なので「re-key→上書き」は不成立 → 旧 run/snapshot は **legacy 監査**として残し、後段の新 force run が最新=API 正値。**血統 ID(sire/dam/damsire_id)と recommendations の JSON 内 horse_id も物理 canonicalize**(backtest が JSON ID を直接照合するため)。衝突ガードの実 PK は `(prediction_run_id, horse_id)`。re-key は **1 サロゲート→1 canonical のペア単位原子トランザクション**(部分統合防止)。repair 中は writer 停止 or advisory lock。
4. **FEATURE_VERSION bump なし**: 特徴計算ロジック不変=これはデータ修復。feature_hash 不変で lgbm-061(features-016)の serving 互換維持。変わるのは 2025H2+/2026 レースの特徴値(=修正目的)。**pre-2025 は strictly-before によりバイト不変**(parity 回帰テストで実証)。現行モデルの再学習は不要(学習窓 pre-2025)。
5. **段取り**: migration ではなく operator 実行の冪等 CLI(feature materialize / live refresh 同型)。resolve(出血停止=ingest-time + 既存解決)→ dry-run → repair → 派生再生成 → 検証。

## 実装完了サマリ(2026-07-12)

**実装済み(branch `067-entity-identity-resolution`)**: `scrape/identity.py`(純関数 normalize_name/strip_markers/classify_identity)・`scrape/repair.py`(resolve_identities/repair_splits/監査)・`scrape/idmap.py` に ingest-time identity 注入・`scrape/upsert.py` entries に evidence 供給・`scrape/cli.py` に resolve-identities/repair-splits サブコマンド。テスト: identity unit 12・repair integration 7・ingest-identity 6・features parity 1・leak-guard 1。**scrape 88 / features 225 緑・ruff クリーン・migration head 0011 不変・スキーマ/OpenAPI 不変**。

**実データ検証(ローカル DB, 全 rollback=非永続)**:
- resolve: horse **5,975** / jockey **156** / trainer **199** mapped、conflict 0/7/8、insufficient(birth_year 欠損サロゲート)5,383/0/0=**安全に未マップ**(誤統合 0)。
- repair: 6,330 ペア・**衝突 0・held 0**・rekeyed(race_horses/results/predictions/snapshots + recommendations JSON)・affected_from **2025-10-11**・errors 0。POST-ROLLBACK で nk 馬 9,647 不変=DB 無改変。
- parity: pre-2025 特徴が re-key 前後でバイト一致・直近レース履歴が復活(career_starts 0→実キャリア)を features 統合テストで実証。

**実装中に実データが暴いた設計是正(codex 指摘の上に追加)**: 削除前属性ゲートに `sire_id/dam_id/damsire_id`(生 ID 列)を含めると、canonical(JRA-VAN)がこれら ID 列を ~0% しか持たず surrogate 側は `nk:` 値を持つため **全 5,975 頭が held** になった。これらは**特徴ではない**(026 は sire_NAME を使用)・surrogate 値自体が `nk:` の被修復対象 → **ゲートから除外**(feature 相当の静的属性 sex/birth_year/各 name/line/owner/breeder のみを gate)。除外後 held 0。

**残作業**: 実際の永続 apply(operator 作業・ユーザー承認前提)= writer 停止 → resolve/repair 本実行 → materialize → predict-backfill --force(affected_from 2025-10-11〜)→ recommend-backfill → cutover。T018(旧 nk: URL の api 誘導)は任意で未実装。conflict(jockey 7・trainer 8)は手動レビュー。

## Complexity Tracking

> 憲法 Check に未正当化の違反なし。品質ゲート(codex)のみ環境要因で未取得=残リスクとして許容し、実装前の復旧を推奨。

| Item | Why | Mitigation |
|------|-----|------------|
| 物理 re-key の非可逆性 | 仮想統合より下流改修面が小さく保守的 | dry-run 必須 + id_mappings/ingestion_jobs に監査記録 + 衝突ガード + per-pair 原子トランザクション + testcontainer 冪等テスト |
| 騎手/調教師の名前照合が fuzzy | netkeiba 短縮名 + マーカー | prefix 裏取り + 免許番号同一を主根拠、初回は dry-run 候補の operator 承認ゲート、失敗は conflict |
| 派生 CLI が append-only | predict-backfill --force=新 run・materialize は DB 非更新(codex#1) | 派生は re-key(FK 整合)+ 旧 run legacy 監査 + 新 force run が最新、recommendations JSON は物理 canonicalize |
| repair 中の writer 競合(TOCTOU) | ingest/predict が同時に走ると不整合(codex#7) | 保守時間帯に writer 停止 or advisory lock、regen 完了まで cutover 制御 |
