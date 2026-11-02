# Implementation Plan: JRA 公式サイトを第 2 の情報源として追加する

**Branch**: `176-jra-official-source` | **Date**: 2026-11-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/176-jra-official-source/spec.md`

**Status**: 実装済み 2026-11-02(main マージ済み 2026-11-02・本番 DB に 0023/0024 適用済み・JRA 取得は未有効・記録 [result-review.md](result-review.md)・残りは T050 と rollout T100〜T107)。以下は tasks 時点: tasks 済み 2026-11-02([tasks.md](tasks.md)・101 タスク+rollout 8 段(T100〜T107)・codex tasks レビュー Q1 8/Q2 8/SC 12 を全件反映・採否は research.md 末尾)。**利用者決定 2026-11-02**: US2 シナリオ 1/4 の単勝人気は保存せず監査に使う(別表 `jra_result_popularity` は作らない)。実装は開催中でも可のタスク(⏸ なし=新規ファイルのみ)から着手できる。**推奨は別 worktree で実装し、稼働 checkout への反映は開催終了後に 1 回**(ops の subprocess が稼働 checkout を毎ジョブ import するため)。

## Summary

netkeiba と並走する第 2 の情報源として JRA 公式サイト(`www.jra.go.jp/JRADB`)を `scrape/` に追加する。取得は索引から開催日・レースへ POST cname を辿る JRA 専用アダプタ(`JraFetcher`)で、共有スロット(093/172)・robots キャッシュ・捕捉時間帯(174)の規律は netkeiba と同じ機構を通す。馬・騎手・調教師の公式コードは既存の正規 ID と同一名前空間なのでそのまま使い、`nk:` を作らない。書込みは既存契約(`race_horses` / `races` / `race_results` / `race_laps` / `official_win_payouts` / `exotic_odds`)への純追加で、**二重情報源の規則(衝突検査・項目別遷移表・オッズの全馬一体契約・結果のレース単位初回挿入・払戻の券種集合)は netkeiba 側 writer にも共通に適用し、レース行ロック下で原子的に判定する**。来歴は馬単位・レース単位の 2 表(0023)、馬場情報は新表(0024・特徴量不使用)。導入は開催終了後の Phase 1(共通安全性)から段階的に行う。精度・回収率の改善は約束しない。

## Technical Context

**Language/Version**: Python 3.12(scrape / db / ops / features)。bash(wrapper)。

**Primary Dependencies**: httpx(POST・`follow_redirects=False`)、beautifulsoup4 + lxml(既存 parser と同じ)、SQLAlchemy 2.0 / Alembic / psycopg3。新規依存なし(Playwright 不要=調査で確定)。

**Storage**: PostgreSQL 16。migration `0023_jra_official_source`(CHECK 置換 5 + 来歴 2 表)、`0024_jra_track_conditions`(馬場表)。既存表の列は不変。

**Testing**: pytest + testcontainers(scrape / db / features / ops)。解析器はネットワーク遮断(`scrape/tests/conftest.py` の `_block_network`)で raw Shift_JIS fixture から。

**Target Platform**: 単一オペレータのローカル macOS(`scripts/stack.sh` のスタックと同じ DB)。手動 CLI のみ。

**Project Type**: 既存 monorepo の scrape パッケージ拡張 + db migration + ops の互換 adapter。

**Performance Goals**: 1 日あたりの公式要求数 ≤20(出馬表+変更情報+結果+払戻+馬場)・≤50(単複オッズ全レース込み)(SC-005・`requests_sent` で実測)。要求間隔 ≥ policy の `jra_official_interval_seconds`(下限 10 秒)。

**Constraints**: 共有スロット迂回不可(wrapper なし起動も fail-closed)・捕捉時間帯で停止・cname 非生成・Shift_JIS(cp932 strict)・HTTP 待ち中にレース行ロックを持たない・`decision_quotes` 不変・OpenAPI 不変・既存行の書換えなし。

**Scale/Scope**: 開催日 2〜3 場 × 12 レース。fixture 約 1.5MB。対象は当日と索引から到達できる日のみ(2026 年の backfill と `nk:` 再キーは別 spec=FR-024)。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.1 に基づくゲート:

- [x] **I. データ契約**: `raceId` は見出し(年/回/日次/場/R)から既存 `build_race_id` で構成し、構成できないレースは書かない(日付から推測しない)。2007 年以前は到達不能(索引は直近のみ)+ `race_date < 2025-10-11` は書かない。**公式コードは JRA 発行の正規 ID と同一名前空間**なので `id_mappings` を経由しない=憲法 I の「JRA-VAN と netkeiba の ID」規律の対象外と解釈(spec Assumptions・安全弁として名前照合と衝突検査)。`nk:` は作らない。承認済み mapping がある対も**書かず報告のみ**(spec FR-011 より狭い実装・research D6)。**PASS(解釈を plan に記録)**。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 特徴量は変えない。来歴 2 表・馬場表は features / training / serving / betting / api が読まない(静的 leak-guard + 禁止表だけ変えて parquet と予測がバイト一致する動的試験=research D19)。結果ページの馬体重は出走表契約(発走前公表と同じ事実)、人気は保存しない。市場オッズは `race_horses.odds`(非特徴)にのみ。**PASS**。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: モデル・特徴量を変更しないので walk-forward 評価は非該当。代わりに parity(SC-001/003)・予測バイト一致(SC-008)・契約テスト(SC-010〜012)を実装前に quickstart で固定。精度・回収率の改善は約束しない(spec 冒頭)。**PASS(N/A の理由を記録)**。
- [x] **IV. 確率整合性**: 確率は触らない。取消・除外は `entry_status` の一方向遷移で既存の除外・再正規化がそのまま働く。**N/A**。
- [x] **V. 再現性・監査**: オッズは単一最新値のまま、`official_at` / `observed_at`(140 の契約)で来歴。来歴 2 表は値と同一トランザクション。単複オッズページは archive しない(時系列を作らない)。`decision_quotes` 不変。全取込は `ingestion_jobs`(source=`jra_official`)。**PASS**。
- [x] **VI. feature 分割規律**: UI なし。DB 契約(0023/0024)と CLI 契約を contracts/ で先に固定。API / OpenAPI 不変(SC-009)。新表は VI の正当化を data-model に記録。**PASS**。
- [x] **品質ゲート**: codex second opinion を spec 段階(19 件・全採用)と plan 段階(29 件・不採用 0・部分採用 2)で取得し、暫定案との差分と採否を [research.md](research.md) 末尾に記録。**PASS**。

**Post-design re-check(Phase 1 設計後)**: data-model の INV-P1〜P5 / INV-B1〜B4、contracts/precedence.md のロック順序と遷移表、contracts/fetch-and-parse.md の archive 許可制が上記ゲートを維持する。違反なし。Complexity Tracking は空。

## Project Structure

### Documentation (this feature)

```text
specs/176-jra-official-source/
├── spec.md              # clarify 済み(plan 段階の表現是正を反映)
├── plan.md              # このファイル
├── research.md          # D1〜D21 + codex plan レビュー採否表
├── data-model.md        # Source 拡張・来歴 2 表・馬場表・値オブジェクト・状態遷移・名前空間契約
├── quickstart.md        # SC-001〜012 の検証手順
├── codex-review.md      # spec 段階 19 件 + plan 段階へのポインタ
├── checklists/requirements.md
├── contracts/
│   ├── cli.md           # jra-* コマンド・起動時検証・summary キー・終了コード
│   ├── precedence.md    # 両 writer 共通の書込み規則(ロック順序・衝突検査・遷移表・オッズ・結果・払戻)
│   └── fetch-and-parse.md  # JraFetcher の応答分類・復号・archive 許可制・navigator・解析器・fixture
└── tasks.md             # /speckit-tasks の出力(未作成)
```

### Source Code (repository root)

```text
db/
├── src/horseracing_db/enums.py                 # Source.JRA_OFFICIAL
├── src/horseracing_db/constraints.py           # OFFICIAL_WIN_PAYOUT_SOURCES
├── src/horseracing_db/models/market.py         # OfficialWinPayout の CHECK 定数
├── src/horseracing_db/models/provenance.py     # RaceFieldProvenance / RaceHorseFieldProvenance(新)
├── src/horseracing_db/models/track_conditions.py  # JraTrackCondition(新)
├── migrations/versions/0023_jra_official_source.py
└── migrations/versions/0024_jra_track_conditions.py

scrape/
├── src/horseracing_scrape/
│   ├── politeness.py        # _JRA_KEY・source_interval_s の JRA 必須化・wait_out_cooldown
│   ├── capture_window.py    # ops から移設(純粋計算 + 読込)+ CaptureWindowRefused
│   ├── precedence.py        # 新: 衝突検査・遷移表・オッズ判定(純関数)
│   ├── provenance.py        # 新: 来歴 2 表の書込み(同一 txn)
│   ├── upsert.py            # 共通修正: ロック順序・衝突検査・遷移表・全馬一体オッズ・_insert_results(mode)・source 引数
│   ├── pipeline.py          # _run_job(source=)・quarantined>0 → PARTIAL
│   ├── cli.py               # jra-* サブコマンドの登録
│   └── jra/
│       ├── __init__.py
│       ├── fetch.py         # JraFetcher(POST・cp932・応答分類・archive 許可制・requests_sent)
│       ├── navigate.py      # 索引 → 開催日 → レース(cname は run 内のみ)
│       ├── venues.py        # 競馬場名 → 2 桁コード
│       ├── identity.py      # official_id・jra_name_key
│       ├── models.py        # data-model §4 の値オブジェクト
│       ├── parse/{entries,changes,odds,results,payouts,baba}.py
│       ├── upsert_jra.py    # 公式経路の書込み(upsert.py の共通関数を呼ぶ)
│       ├── audit.py         # 照合(READ ONLY)
│       ├── policy.py        # policy / env / limiter の起動時検証
│       └── cli.py           # jra-entries / jra-odds / jra-results / jra-payouts / jra-baba / jra-fetch / jra-audit
└── tests/
    ├── fixtures/jra/        # raw Shift_JIS + manifest.json(+ parity/ の netkeiba JSON)
    ├── unit/test_jra_*.py   # 解析器・identity・精度・politeness・fixture manifest
    └── integration/test_jra_*.py, test_precedence_*.py  # 順序行列・衝突・来歴・並行・送信制御・parity

ops/
└── src/horseracing_ops/capture_window.py        # scrape からの再 export(互換 adapter)+ refusal_detail

features/
└── tests/unit/test_jra_leak_guard.py, tests/integration/test_forbidden_tables_do_not_move_features.py

scripts/
├── source_operations_172.py   # jra_official_interval_seconds を任意キーとして受理・env に写す
├── jra_ops.sh                 # 新: policy → env → scrape CLI
└── export_parity_fixture.py   # 新: 実 DB(読み取り専用)→ parity fixture JSON

config/source-operations.json   # jra_official_interval_seconds の追加(reader 配布後)
```

**Structure Decision**: 既存 monorepo の scrape パッケージに `jra/` サブパッケージを追加し、両経路に共通の判定(`precedence.py` / `provenance.py` / `upsert.py`)は scrape 直下に置く(research D1)。ops は `capture_window` の再 export だけ(業務機能は増やさない)。front / admin / api は変更しない。

## Complexity Tracking

Constitution Check に違反なし。記載事項なし。

## Phase 0 / Phase 1 の成果物

- Phase 0: [research.md](research.md)(D1〜D21・NEEDS CLARIFICATION 0・codex 採否表)。
- Phase 1: [data-model.md](data-model.md)、[contracts/cli.md](contracts/cli.md)、[contracts/precedence.md](contracts/precedence.md)、[contracts/fetch-and-parse.md](contracts/fetch-and-parse.md)、[quickstart.md](quickstart.md)。agent context(`CLAUDE.md` の SPECKIT 区間)は手動更新(agent-context フックは CLAUDE.md を壊すので実行しない)。

## 実装の段階(tasks の骨格・research D20)

| Phase | いつ | 内容 | 完了条件 |
|---|---|---|---|
| 0 | 開催中でも可 | 契約・fixture・解析器・純関数(`precedence.py` / `jra/identity.py` / 窓計算の純粋部)・一時 PostgreSQL での検証。**本番 DB・policy・稼働プロセスが読む共通コードは変えない** | 単体テスト緑・fixture manifest 一致 |
| 1 | 開催終了後 | migration 0023 / enums / `_run_job(source=)` / 衝突検査の両経路導入 / 遷移表(取消復帰防止・体重の対)/ ロック順序 / オッズ全馬一体 / 来歴 2 表 / `_insert_results(mode)` / 払戻 writer の source 引数。**JRA 書込みは無効のまま** netkeiba 経路を切替 | scrape / ops / live / db 全テスト緑・実 DB で 1 開催日の通常運用(ops 更新・174 捕捉)に異常なし・0023 を最終レース後に適用 |
| 2a | Phase 1 後 | `JraFetcher` / policy 任意キー reader → env → config → CLI 必須化 / `capture_window` 移設 / wrapper | 送信制御テスト緑・ops の既存テスト緑 |
| 2b | | `jra-fetch` / `jra-audit`(書込みなし)で公式取得と照合を実測 | `requests_sent` と一致率を記録 |
| 2c | | `jra-entries` / `jra-odds` を `--race-id` 限定で有効化 → 全レース | SC-001/002/004/010/011 |
| 2d | | `jra-results`(payouts 含む)/ `jra-payouts` | SC-003/010 |
| 3 | 2 の運用が安定後 | migration 0024 + `jra-baba` | INV-B1〜B4・SC-008 動的試験 |
| 4 | | SC-005/008 の実測を spec に転記・`result-review.md` | — |

## テスト計画(codex の 10 分類を採用)

1. **来歴の部分更新**: A 馬だけ体重変更・B 馬は旧値・騎手だけ変更・同値の公式採用・旧値拒否・NULL 保持 → 各値の source / observed_at / job が真実(INV-P1〜P5)。
2. **SC-010 順序行列**: netkeiba→JRA→netkeiba / 逆順 / 同一入力 / 古い入力 / 同時刻異値 / 時刻不明 × {出走状態・静的項目・騎手・体重・単勝・複勝・結果・券種別払戻}。
3. **実 PostgreSQL の並行実行**: 結果確定とオッズ更新・公式払戻と netkeiba 再投入・新規レース作成競合・逆順の複数レースロック・値更新後/来歴更新前の障害。
4. **ID 衝突の両方向**: JRA 先行後の未解決/REJECTED mapping・同馬番別 ID・同 ID 別馬番・既存重複・取消馬番不明・入力重複 → 二重行 0・隔離レース無変更。
5. **結果の例外と JRA-VAN 保護**: 部分保存済み結果・corner_orders NULL・running_style NULL・結果後馬体重・ラップ・人気(保存されない)・`race_date < 2025-10-11` の無書込み。
6. **払戻集合**: 同着・勝ち馬集合訂正・同額の公式採用・重複組番・元返し・返還・未発表 vs 発売なし・枠連・円→倍率・券種ごとの失敗分離。
7. **送信直前の取得制御**: robots・POST・リダイレクト・再試行の全要求、別プロセス競合、待機中の捕捉窓開始、DB 障害、共有 limiter 無効、policy 欠損/不正値。
8. **拒否と辿り直し**: robots 403・429・Retry-After 秒/HTTP-date・503・400・200 パラメータエラー・error013・リダイレクトループ、同じ不正 POST の再送なし。
9. **GET 互換と保存境界**: `HttpFetcher` 不変(既存テスト)、単複オッズが archive に残らない、cp932 拡張文字と壊れたバイトの区別。
10. **リーク・監査・移行**: 禁止表だけの変更で特徴/予測不変、fixture 監査は DB 無変更、未知 source が一致率に混入しない、旧 policy/新 reader と新 policy/旧 reader の失敗条件、拡張 DB と旧コードの共存、downgrade の中断。

## 残リスク

- 利用者決定(2026-11-02): US2 シナリオ 1/4 の人気は保存しない。別表の契約追加は tasks に無い。
- **research D18 の「head 直書きの修正は不要(確認済み)」は誤り**: features 9 本+live 1 本の unit テストが `0022_` を直書きしている(2026-11-02 grep)。tasks T020/T090 で 0023/0024 へ更新する(040/054 と同型)。
- Phase 1 は netkeiba 経路の挙動変更(取消復帰防止・衝突隔離・オッズ全馬一体・複勝の時刻規則)を含む。既存テストの一部は契約変更として更新する。切替は開催終了後・1 開催日の観察を挟む。
- 公式サイトの利用条件・構造は 2026-11-01 時点。構造変更は fail-closed で検知する(解析器が部分値を書かない)。
- `nk:` 5,545 頭の統合は本 feature では進まない(FR-024)。本 feature が出すのは候補の対と件数まで。
