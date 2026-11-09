# Implementation Plan: JRDB を主情報源にし、netkeiba を止められる状態にする

**Branch**: `178-jrdb-primary-source`(ブランチ未作成・main 上に spec/plan のみ。実装は別 worktree) | **Date**: 2026-11-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/178-jrdb-primary-source/spec.md`

## Summary

JRDB(有料の構造化配信・Shift_JIS 固定長・LZH)を **第 3 の writer** として `scrape/jrdb/` に追加し、176 が作った両 writer 共通の規則(`precedence.py`)と来歴 2 表をそのまま共用する。段 0(現構成の診断)→ 段 1(読み取り専用の parity 報告)→ 段 2(writer・出走行とマスタ行の生成責任を JRDB へ)→ 段 3(定義一致が取れた列だけ 2025〜2026 を修復)→ 段 3.5(netkeiba 非供給の隔離環境で運用成立試験)→ 段 4(policy で netkeiba 停止)。特徴・モデル・API・front は変えない。利益は約束しない。

**plan で実測して分かったこと**(research D1/D3): 馬 ID は 8→10 桁で 100% 一致するが、**騎手・調教師コードは JRDB 独自**(JRA コードと不一致)で `id_mappings` が要る。馬別前 3F は 1200m 以外で JRA-VAN と別定義、通過順・脚質・系統も定義が違う。利用者方針(2026-11-08)「JRDB で取れたデータでモデルを作る」を受け、段 3 を「3a 現行 DB の修復」と「3b JRDB 正本の別 DB を 1999〜現在で作る(学習窓は 2007〜)」に分けた(D11)。

技術的な芯は 4 つ。(1) JRDB ファイルは HTTP で取るが「スクレイピング」ではなく **会員 URL からのファイル取得+固定長の decode** なので、`HttpFetcher`/`JraFetcher` には触らず専用の `JrdbFetcher`(Basic 認証・1 ファイル 1 要求・LZH 展開)を置く。(2) 書込みは `upsert_jra.py` と同じ「薄い層」で、規則は `precedence.py` を**拡張**(第 3 の source を規則表に加える)して共用する。(3) ID は JRDB レースキー(8 文字)→ 12 桁 race_id、血統登録番号(8 桁)→ 10 桁 horse_id を**決定論**で写像し(`id_mappings` 不要)、騎手・調教師だけ KZA/CZA の名前+生年月日+所属+初免許年から `id_mappings`(source=jrdb)を作る(名前だけは conflict)。(4) 冪等性の単位はファイルの内容 sha256+レースで、新表 `jrdb_files`(`race_status` 付き)に記録する。異内容の新旧を自動で決めるのは 速報→正式 と 日次→バックナンバー だけ、それ以外は `held`(D9)。(5) 書込み先プロファイル `current|jrdb_canonical` を全経路の必須引数にし、現行 DB には別定義 4 列をどの経路でも書かない(D7a)。(6) 騎手・調教師の写像根拠は JRA 公式の騎手/調教師ページ(URL=正規 ID)の生年月日+名前で、約 1,500 人分を 176 の `JraFetcher` で 1 回だけ取る(D3)。

## Technical Context

**Language/Version**: Python 3.12(scrape/db パッケージ。uv 管理)

**Primary Dependencies**: httpx(既存)・SQLAlchemy 2.0・psycopg3・**LZH 展開**(新規: 純 Python の `lhafile` か外部 `lha`。research D4)・既存の `precedence.py` / `provenance.py` / `upsert.py`(`apply_final_odds`・`_insert_results`)・`jra/alias.py`(FR-011a の別名規則)

**Storage**: PostgreSQL 16。新表 `jrdb_files`(ファイル版・sha・配信時刻・取込時刻)。`Source` に `jrdb` を追加=CHECK 置換(0023 と同型)。`id_mappings.source` も `jrdb` を受理。現行 DB に JRDB 由来の新列は**作らない**。段 3b の JRDB 正本は**同じ migration 群を適用した別 DB**(`horseracing_jrdb`)。生ファイルは `artifacts/jrdb/<kind>/<yyyymmdd>/` に保存(gitignore)。

**Testing**: pytest(unit=固定長 fixture・testcontainers integration)。fixture は**公開サンプル**(`/program/<種別>/<種別>yymmdd.txt`・ログイン不要)と合成行だけ。会員配信物は repo に置かない(会員規約 第 5 条・research D5/D6)。

**Target Platform**: ローカル macOS(運用者の手動 CLI)。開催中は稼働プロセスが読む共通コードに触らない(FR-016)。

**Project Type**: 既存 monorepo の `scrape/` パッケージへのサブパッケージ追加+`db/` migration 1 本+`scripts/` の運用 wrapper。

**Performance Goals**: アーカイブ 2007〜2024(約 63,000 レース・約 90 万出走行)の parity 報告が 1 回 30 分以内。段 3b の全期間取込(1999〜現在)が 1 回 2 時間以内。日次ファイルの取込は 1 開催日 1 分以内。段 3.5 の再生は実到着時刻どおり(実時間)。

**Constraints**: FR-016(開催中は新規ファイルのみ)・FR-017(API/OpenAPI/front/admin 不変)・V(オッズ履歴を保存しない=JRDB の 10 時/確定オッズも別表を作らない)・II(事後ファイルは対象レースの特徴に使わない・指数類は勝率モデルの特徴にしない)。

**Scale/Scope**: 新規 ~15 モジュール(fetch/decode/layout/idmap/parity/upsert/cli/policy)+ migration 0025 + テスト。既存変更(すべて ⏸=開催終了後に R2 で 1 回反映・tasks の ⏸ 一覧と同期)は `precedence.py`(規則表に source=jrdb・T046)・`db/enums.py`+`constraints.py`(`Source.JRDB`・T015)・`db/models/__init__.py`(`JrdbFile` export・T016)・`scrape/cli.py`(登録 1 行・T036)・`jra/cli.py::cmd_entries`(緊急モードの policy 分岐・T050a)・`live/cli.py`(`source-probe`/`source-trial` 登録・T022/T065)・`live/near_post.py`(policy `near_post_source` の分岐・既定不変・T064a)・netkeiba fetcher の生成点 4 箇所(`scrape/cli._make_fetcher`・`ops/runner.py`・`live/chaos_politeness.py`・`live/near_post.py` に `netkeiba_enabled` ガード・T068)・`config/source-operations.json`+`scripts/source_operations_172.py` の `OPTIONAL_KEYS`(任意キー 6 つ・T069)・既存テストの migration head pin(features 9 本+live 1 本+`db/tests/integration/test_migration_176.py`・T018/T019・開催中でも可)。

**NEEDS CLARIFICATION → research.md で解決**: D1 列対応と不足の有無 / D2 レイアウトと版 / D3 ID 写像 / D4 LZH / D5 規約 / D6 fixture / D7 writer と規則表 / D8 生成責任と緊急モード / D9 冪等性と修正版 / D10 parity / D11 修復と再構築 / D12 計器 / D13 netkeiba 停止 / D14 配信時刻 / D15 codex。**会員ページで確認(利用者ログイン後)**: 実 URL・直近とバックナンバーの置き場・各種別の開始年・料金。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.1 に基づくゲート:

- [x] **I. データ契約**: race_id は JRDB レースキーから `YYYYVVKKDDRR` を決定論で組む(年 2 桁→4 桁・日 16 進)。現行 DB への書込みは 2007 年以降のみ。馬 ID は血統登録番号 8→10 桁で JRA 体系と同一(サンプル 100%)=176 と同じ解釈。**騎手・調教師は JRDB 独自コードなので `id_mappings` 経由**(名前だけの一致は conflict・推測結合しない)。段 3b の 1999〜2006 は取り込むが学習窓に入れる判断は別 spec。**PASS**
- [x] **II. リーク防止**: 本 feature は特徴を足さない。事後ファイル(SED/SRB/HJC)は `race_results`/払戻への書込みだけで、特徴経路は既存の strictly-before as-of のまま。指数類は DB に**取り込まない**(列を作らない=混入経路を作らない)。修復は 139 と同じく「値の修復」で、FEATURE_VERSION は据え置き・波及を測って記録。**PASS**(限界: 利用可能時刻基準の as-of は別 spec・research D11)
- [x] **III. 評価先行**: 現行 DB のモデル/特徴は変えない。段 3b の JRDB 正本に対する学習・評価・昇格は別 spec(walk-forward・baseline・ECE はそこで)。修復の波及は 139 の方法で記録。**N/A(本 feature は採用判定を伴わない)**
- [x] **IV. 確率整合性**: 確率の計算に触らない。**N/A**
- [x] **V. 再現性・監査**: 取込は ingestion_jobs に記録・冪等・来歴 2 表に `jrdb` を残す。オッズは単一最新値(10 時/確定オッズの別表を作らない・確定は 139 の `apply_final_odds` 規則)。判断記録は置き換えない。**PASS**
- [x] **VI. feature 分割規律**: UI なし。API/OpenAPI 不変。スキーマ変更は `jrdb_files` 1 表と CHECK 置換に限り、migration 0025 で先に契約を固定。**PASS**
- [x] **品質ゲート**: spec は codex 6 件全採用(spec レビュー)。plan も codex 6 件全採用(research D15: 別定義列の流入契約・人物写像の根拠・版の順序・レース単位の部分成功・土曜→日曜の供給・未充足セル検査)。**PASS**

## Project Structure

### Documentation (this feature)

```text
specs/178-jrdb-primary-source/
├── spec.md
├── plan.md              # 本ファイル
├── research.md          # D1〜D14
├── data-model.md        # jrdb_files・Source 拡張・規則表の JRDB 列・ID 写像
├── quickstart.md        # 段 0〜4 の検証手順
├── contracts/
│   ├── files-and-ids.md     # ファイル種別・レイアウト参照・キー写像・版と冪等性
│   ├── precedence-jrdb.md   # 176 規則表への JRDB 列の追加(差分だけ)
│   ├── parity.md            # 段 1 の報告の形(意味の一致/数値の一致/分母)
│   └── cli.md               # scrape jrdb-* と計器 CLI
└── tasks.md             # /speckit-tasks で生成
```

### Source Code (repository root)

```text
scrape/src/horseracing_scrape/
├── precedence.py                 # 変更: 規則表に source=jrdb の列(研究 D7)
├── jrdb/                         # 新規
│   ├── README.md
│   ├── persons.py                # KZA/CZA ↔ JRA 公式プロフィール(正規 ID・生年月日)→ id_mappings(source=jrdb)。名前だけは conflict
│   ├── profile.py                # 書込み先プロファイル current|jrdb_canonical(別定義 4 列の allowlist・D7a)
│   ├── rebuild.py                # 段 3b: 別 DB への全期間取込(同じ decode/upsert を別 DATABASE_URL で)・年別件数比較
│   ├── fetch.py                  # JrdbFetcher: 会員 URL・Basic 認証・1 ファイル 1 要求・LZH 展開・sha
│   ├── layout/                   # 種別ごとの固定長レイアウト(開始バイト・長さ・型)=仕様書の転記
│   ├── decode.py                 # Shift_JIS(cp932) bytes → 値オブジェクト。桁ずれは fail-closed
│   ├── models.py                 # JrdbEntry / JrdbResult / JrdbPayout … (DB を知らない)
│   ├── ids.py                    # レースキー→race_id・血統登録番号→horse_id・コード→ID(決定論)
│   ├── files.py                  # jrdb_files の版管理(sha・版の単調性)
│   ├── parity.py                 # 段 1: 列ごとの意味/数値の一致報告(READ ONLY)
│   ├── upsert_jrdb.py            # 薄い書込み層(行とマスタの生成・規則は precedence 共用)
│   ├── repair.py                 # 段 3: 列ごとの修復(apply_final_odds・first_3f・馬主 等の流用)
│   ├── policy.py                 # 起動時検証(規約確認済みフラグ・認証情報 env・緊急モード)
│   └── cli.py                    # scrape jrdb-fetch / jrdb-parity / jrdb-persons / jrdb-entries / jrdb-results /
│                                 #   jrdb-payouts / jrdb-repair / jrdb-rebuild / jrdb-audit
├── netkeiba_policy.py            # 新規: `netkeiba_enabled` の読み手(fetcher 生成点 4 箇所から呼ぶ・T067)
├── jra/
│   ├── emergency_entries.py      # 新規: 緊急モード=公式出馬表から出走行とマスタ行を作る(FR-008)
│   ├── person_profile.py         # 新規: 騎手/調教師ページ(pw04kmk0/pw05cmk0)の生年月日・名前を読む(D3・1 回限りの収集)
│   └── cli.py                    # 変更 ⏸: cmd_entries に policy jra_emergency_entries の分岐(T050a)
└── cli.py                        # 変更 ⏸: jrdb-* の登録 1 行・_make_fetcher に netkeiba_enabled ガード(T036/T068)

live/src/horseracing_live/
├── source_probe.py               # 新規: 段 0 の診断(公式 T−3 取得率・依存の棚卸し・実到着時刻)。DB には書かない
├── source_trial.py               # 新規: 段 3.5 の隔離試験(複製 DB・再生・締切時点採点・専用入口 capture_official)
├── cli.py                        # 変更 ⏸: source-probe / source-trial の登録(T022/T065)
├── near_post.py                  # 変更 ⏸: policy near_post_source(netkeiba|jra_official・既定不変・fallback 禁止)+ netkeiba_enabled ガード(T064a/T068)
└── chaos_politeness.py           # 変更 ⏸: netkeiba_enabled ガード(T068)

ops/src/horseracing_ops/runner.py # 変更 ⏸: netkeiba fetcher 生成点に netkeiba_enabled ガード(T068)

db/
├── src/horseracing_db/enums.py / constraints.py   # 変更 ⏸: Source.JRDB(T015)
├── src/horseracing_db/models/jrdb.py              # 新規: JrdbFile(T016)
├── src/horseracing_db/models/__init__.py          # 変更 ⏸: JrdbFile の export(T016)
├── migrations/versions/0025_jrdb_source.py        # 新規: CHECK 置換 7 本+jrdb_files(T017)
└── tests/integration/test_migration_176.py        # 変更: 除外表に jrdb_files(T018)
features/tests・live/tests の head 直書き 11 本        # 変更: 0024_→0025_(T019・開催中でも可=稼働プロセスは読まない)

scripts/
├── jrdb_ops.sh                   # 新規: wrapper(env・policy・日次の順序)
├── jrdb_repair_impact.py         # 新規: 修復の予測への波及(139 と同じ方法・T058)
└── source_operations_172.py      # 変更 ⏸: OPTIONAL_KEYS に任意キー 6 つ(T069)

config/source-operations.json     # 変更 ⏸: 任意キー jrdb_enabled / netkeiba_enabled / jra_emergency_entries / jrdb_interval_seconds / jrdb_archive_blackout(開催日 08:00〜19:00)/ near_post_source(T069・全部既定値)
```

**Structure Decision**: 176 の `jra/` と同じ形(fetch→parse(decode)→薄い upsert→CLI、規則は共通 `precedence.py`)。`ingest/` に置かない理由は、JRDB は第 3 の **writer** として 176 の規則と来歴を共用する必要があり、それらは `scrape/` にあるため(ingest は JRA-VAN 一括取込専用で precedence を知らない)。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| 新表 `jrdb_files`(憲法 VI) | 修正版の再配信と冪等性の単位(sha+版)を来歴から辿るため。ingestion_jobs の summary だけだと「どの版の値か」が行単位で追えない | ingestion_jobs の JSON に埋める案は、版の単調性(旧版が後から来ても巻き戻さない)を SQL で強制できない |
| `precedence.py` への変更(開催中禁止の共通コード) | 第 3 の source を規則表に足すには正本を変えるしかない(README の禁止事項「再実装しない」) | `jrdb/` 内で規則を複製すると 176 が排除した二重実装に戻る。反映は開催終了後に 1 回(FR-016) |
| JRA 公式の人物ページを約 1,500 件取る(176 の範囲外の取得) | 騎手・調教師の写像根拠を正規 ID に直接ひも付けるため(codex D15 #2)。名前だけでは憲法 I に反する | netkeiba プロフィールは正規 ID との対応証拠が無い。KZA/CZA だけでは DB 側に突き合わせる属性が無い |
