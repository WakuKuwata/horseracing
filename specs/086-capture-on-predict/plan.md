# Implementation Plan: 予測実行時の荒れ度スナップショット捕捉 (capture-on-predict)

**Branch**: `086-capture-on-predict` | **Date**: 2026-07-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/086-capture-on-predict/spec.md`

## Summary

084 の前向き検証は**捕捉が始まらない限り永久にゼロ**。捕捉を利用者が既に行っている予測実行に
相乗りさせ、同時に codex レビューの BLOCKER 2 件・MAJOR 6 件を潰す。

**計画中に実データで判明し、設計を決定づけた 3 点**:

1. **予測実行の中央値は発走 6.7 時間前**(T−10 分以内は 158 件中 4 件のみ)。
   → 主 horizon を狭く取ると予測を実行しても 95% で荒れ度が出ず、要望が満たされない。
2. **開催日の発走時刻は 8.8 時間に分散**(36 レース)。
   → **「1 日 1 コマンドで T−30 分」は達成不可能**(084 に書いた運用手順の誤り)。
   1 回の実行で**発走直前**に入るのは 1〜2 レースだけ
   (採用した広い窓なら当日の未発走レースはほぼ全部拾えるが、成熟度は揃わない)。全レースを近接捕捉するには 1 日 20〜27 回の実行が
   必要で、それは憲法が先送りしているスケジューラそのもの。
3. したがって**スケジューラ無しで near-post 捕捉を量産する手段は存在しない**。
   現実的な捕捉時刻は「人が見たとき」= 発走の数時間前。

**ユーザー決定**: (a) 主 horizon は**広い窓 [600, 86400] 秒(発走 10 分前〜24 時間前)**
(発走前クリックの **95.6%** が適格。最小陽性 100 まで推定 0.4〜0.8 年、
構成変化による減衰を織り込むと **0.45〜0.9 年**・research D1) (b) 窓外は**捕捉して表示専用**にする(確認用の観測群には入れない)。

**技術的アプローチ**: 既存の分離パターンを維持した薄い結線 + 084 の欠陥是正。

1. `db/` — migration 0013 で捕捉の出所と取得抑制の状態を追加
2. `probability/` — 主 horizon の必須化と適格性判定(表示適格 / 確認適格の分離)
3. `live/` — 捕捉の順序是正(判定 → 排他 → 取得)と単一レースの機械可読出力
4. `ops/` — 予測処理の直前に捕捉を subprocess 実行(境界は既存どおり)
5. `api/` + `admin/` — 実行記録に捕捉結果を露出
6. `training/` — 前向き報告に契機別内訳と horizon 層別を追加
7. `artifacts/` — 主 horizon を含む新 version を発行(create-only)

## Technical Context

**Language/Version**: Python 3.12 (db / probability / eval / training / live / ops / api)、
TypeScript + React 19 (admin / front)

**Primary Dependencies**: 既存のみ。`scrape/fetch.py`(robots 遵守・per-domain 制限・backoff)、
`live/guards.py`、084 の `chaos_capture` / `chaos_artifact` / `chaos_bands`、
`ops/runner.py` の subprocess 境界、FastAPI / pydantic / SQLAlchemy 2.0 / Alembic。
**新規外部依存なし**。

**Storage**: **migration 0013**(追加のみ)。追加は 2 要素 —
`chaos_snapshots` に捕捉の出所 2 列 + **無条件 `UNIQUE(race_id)`**、
取得抑制の状態を保持する小テーブル。既存テーブル・列は不変。
重複行がある環境では 0013 は**中断する**。退避表 `chaos_snapshots_quarantine` は
**復旧 CLI が migration の外で作る**(Alembic の DDL は 1 トランザクションなので、
0013 が作った表は中断とともに巻き戻り、退避先が消えてしまう)。

**Testing**: pytest(db / probability / eval / training / live / ops / api)、Vitest(admin / front)。
**取得回数を数えるテスト**を必須にする(「理由が正しい」だけでは無駄な取得を見逃す)。

**Target Platform**: ローカル運用。deploy の**構成そのもの**は変えないが、
artifact の参照先(`CHAOS_BANDS_ARTIFACT_PATH`)は新 digest に張り替える必要がある
(張り替えないと fail-closed ローダが窓なし v1 を拒否して荒れ度が出なくなる)。

**Performance Goals**: 捕捉が予測を体感的に遅らせないこと。予測は実測 55-113 秒。
捕捉は**単一の実時間の期限・試行 1 回**(1 レースあたり)。
**期限は契機で決まる**: `predict_manual` / `predict_auto` は内側 10 秒 / 外側 18 秒(SC-011 の 20 秒から起動分を引く)、
**`daily_operational` / `explicit_command` は内側 30 秒で外側は無い**
(日次は FR-012 の主たる観測源で遅延制約が無く、予測の体感を根拠にした 10 秒は不適)。
FR-018 の「内側 < 外側」は**外側が存在する予測経路にのみ適用**する。
最悪でも予測実測 55〜113 秒に対して **+16〜33%**(外側 18 秒)の上乗せに収まる。
SC-011 の許容上限は起動と終了処理を含めた **20 秒 = +18〜36%**(45 秒案は最大 +82% だった)。
現状は `max_retries=3` × `timeout=20.0` + backoff で最悪 63 秒超 = どんな外側も食い破る。
足し算の見積もりは使わない(robots.txt の追加往復と段階別 timeout が漏れるため)。

**Constraints**:
- `ops` は `live` / `serving` / `betting` / `api` / `training` / `eval` / `features` を import 不可
  (`ops/tests/integration/test_boundary.py` で機械固定)→ subprocess 境界を維持
- API は全 path GET・書き込み経路ゼロ
- 084 の既存出力は主 horizon 必須化以外**不変**(SC-010)
- 自動スケジューラは導入しない(憲法)

**Scale/Scope**: 予測ジョブ実績 286 件(うち発走前 158 件)。開催日は 36 レース / 8.8 時間分散。
2025 実績 109 開催日 / 3,455 レース。

## Constitution Check

- [x] **I. データ契約**: `raceId` 契約・ID 結合規約・ラベル名すべて不変。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 捕捉が保存するのは荒れ度の凍結観測のみ。
  モデル特徴 / 校正 / 買い目に流入しない(FR-024)。ops は ML スタックを import しない。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: **主 horizon を事前登録し暗黙の全時刻受理を廃止**
  (FR-005..008)= 084 の欠陥是正。確認適格は窓内に限り、窓外は表示専用。
  契機を記録し**選択バイアスを報告に出す**(FR-009..012)。**PASS(むしろ III の穴を塞ぐ)**
- [x] **IV. 確率整合性**: 荒れ度の算出ロジック・バンド境界・λ は変更しない。**PASS**
- [x] **V. 再現性と監査**: **1 レースにつきオッズを含む行は生涯ちょうど 1 行**。
  再捕捉は理由を問わず行わない。捕捉後に出走構成が変わったら、その行を
  その場で無効化する(撮り直さない・そのレースは荒れ度なしになる)。
  同一馬のオッズが複数時点で保存されることが**構造的にあり得ない** —
  migration 0013 で `chaos_snapshots` に**無条件の `UNIQUE(race_id)`** を入れるので、
  規約ではなく**DB 制約**が担保する(0012 の部分 index は `WHERE status='active'` なので
  void + active の 2 行を許してしまう。既存行 0 件の今なら無コストで入る)。**PASS**

  > **計画レビューで撤回した設計**: 当初は「構成が変わるたびに差し替える」+
  > 「窓外→窓内は 1 回昇格」とし、「各行は異なる構成に 1 対 1 対応するので
  > 時系列ではない」と論じていた。これは**成立しない** — 構成 A→B→C でも
  > 生き残った馬のオッズは 3 時点分保存される。さらに昇格案は
  > **構成が変わっていないレースを 2 回撮る**操作であり、自らの不変条件に反していた。
  > 決定打は `training/chaos_bands.py:1896` — 1 レースに複数行あると
  > `not_one_row_per_race` で確認コホートから丸ごと除外されるため、
  > 差し替えはそのレースを捕捉した意味を消してしまう。詳細は research D0。
- [x] **VI. 契約先行・スキーマ変更の正当化**: **migration 0013 を追加**(追加のみ・2 要素:
  出所 2 列 + `UNIQUE(race_id)` / `fetch_throttle_state`。
  退避表は復旧 CLI が必要時に作るので常設のスキーマではない)。
  012(0005)/040(0008)/054(0009)/057(0011)/084(0012)と同じ前例。
  ops / API / admin の契約を実装前に確定する(Phase 1 の contracts/)。**PASS(正当化済み)**
- [x] **技術・スコープ制約(初期は全て手動実行)**: 予測ボタンは**利用者の明示操作**であり
  スケジューラではない。**自動追随の予測(`predict_auto`)も同様に正当化される** —
  `run_one` が取込後に予測を積む挙動は 024/028 で既に存在し、086 はそこに相乗りするだけで
  新しい起動契機を作らない。取得元への負荷はプロセス跨ぎの頻度制限とクールダウン
  (FR-016..019)で保護され、時刻を決めて自律的に起動する仕組みは一切導入しない。
  **ただし「1 日 1 コマンドで全レース T−30 分」は達成不可能と判明した**ので、
  084 の運用手順の記述を是正し、near-post 捕捉の量産にはスケジューラが要る事実を
  Complexity Tracking に明記する。**PASS(制約と限界を明示)**
- [x] **品質ゲート**: codex 設計レビュー 1 回・**BLOCKER 2 / MAJOR 6 を全採用・不採用ゼロ**。
  全指摘を実コードと実データで裏取り済み。採否表は下記(spec「レビュー記録」の写し)。**PASS**

| codex 指摘 | 採否 | 反映先 |
|---|---|---|
| 「存在しなければ捕捉」は最初の任意のクリックを研究観測にする | 採用 | US2 / FR-005..008 / D1 |
| 「存在しない」判定は取消差し替えを止める | 採用 | FR-002 / D5 |
| in-job・予測の前が正しいが再実行の記録が要る | 採用 | US6 / FR-013・FR-015 / D3 |
| ジョブ summary は 052 に露出していない | 採用 | US4 / FR-020 / D3 |
| 発走後レースがフェッチしてから拒否される | 採用 | FR-001 / D5・D6 |
| プロセス跨ぎの礼儀とサーキットブレーカが無い | 採用 | US5 / FR-016..019 / D4・D7 |
| 第二の捕捉方針の追加は選択バイアスを生む | 採用 | US3 / FR-009..012 / D2 |
| 誤った理由で通るテストがある | 採用 | 全判定テストで取得回数を assert(tasks) |

**codex 設計レビュー 2 回目(plan + tasks・2026-07-27 実施)**。
1 回目以降に決めた設計(migration の形・ロックとトランザクション・予算・
ブートストラップ経路・void 行の上限)を対象にした。**全指摘を採用・不採用ゼロ**。

| # | 指摘 | 採否 | 反映先 |
|---|---|---|---|
| 1 | **FR-008b(削除済み) の憲法 V 論法は合理化である**。構成 A→B→C でも生き残った馬のオッズは 3 時点分保存される | **採用(設計撤回)** | FR-002 / FR-002a / research D0 |
| 2 | **昇格案が自らの不変条件を破っている**。窓外→窓内は構成が同じレースを 2 回撮る | **採用(FR-008a(削除済み) 削除)** | FR-008 |
| 3 | **差し替えは確認コホートからレースを消す**(`chaos_bands.py:1895-1896` の `not_one_row_per_race`) | 採用 | research D0 / data-model |
| 4 | 出走構成は単調に縮むとは限らない(取込の upsert で A→B→A) | 採用 | capture-eligibility §2 |
| 5 | **`down_revision = "0012"` は誤り**。実際の識別子は `0012_chaos_readout` | 採用 | T004 |
| 6 | migration が作者環境の行数に依存している。**常に nullable→backfill→NOT NULL** にすべき | 採用 | data-model / T004 |
| 7 | **遡及ラベル `daily_operational` は嘘**。084 は `--date` と `--race-id` を両方許すので区別情報が無い | 採用(`legacy_unknown` に) | data-model / research D2a |
| 8 | **列を書く実装が Phase 7 なのに Phase 5 で SC-001 を主張している**(NOT NULL で全捕捉が失敗する) | 採用(Phase 5 に前倒し) | tasks |
| 9 | **取得中の出走取消で古い構成を凍結しうる**。排他は出走表の更新を守らない | 採用 | FR-003a |
| 10 | **予約して待っているプロセスが新しい抑制を見ない** | 採用 | FR-017a |
| 11 | **抑制行の初回作成が競合する**(`FOR UPDATE` は不在行をロックしない) | 採用 | fetch-politeness §2 |
| 12 | **予約待ちが実行数に比例して伸びる**(n 番目が約 n 秒) | 採用 | FR-017b |
| 13 | **25 秒は算術であって期限ではない**(robots.txt の往復・段階別 timeout が漏れる) | 採用 | FR-018(単一の実時間の期限) |
| 14 | **既存の 20 秒クライアントを実際に張り替える必要がある**(定数追加だけでは変わらない。**解決は `make_capture_fetcher` 新設(`live/chaos_politeness.py`) — `_make_fetcher` は 5 つの取込 CLI が共有しており不変**) | 採用 | fetch-politeness §4 |
| 15 | **45 秒は「体感的に遅らせない」と矛盾**(55 秒の予測に最大 82% 増) | 採用(10s / 20s・1 回試行に) | FR-018 / D7 |
| 16 | **外側打ち切りが `uv` しか殺さない**(捕捉プロセスが生き残り排他を握る) | 採用 | FR-018a |
| 17 | **汎用の生読みは安全境界にならない**(未承認 artifact を `add-horizon` に入れられる) | 採用(単目的関数に) | horizon-artifact §2 |
| 18 | **`approved[-1]` は superseded を読んでいる**(manifest は active が先頭) | 採用 | horizon-artifact §3 |
| 19 | **遡及行は窓なし v1 を指すので報告不能** | 採用(ただし解決は codex 案の「除外理由」ではなく**報告が単一 digest スコープなのでそもそも現れない**と確認した) | horizon-artifact §4 |
| 20 | **捕捉結果を保存するタスクが無い**。かつ終了分岐が `summary` を丸ごと上書きして消す | 採用 | FR-015a / job-observability §1 |
| 21 | **「試みた」印だけでは起動前クラッシュで捕捉機会が永久に失われる** | 採用 | FR-015(状態機械) |
| 22 | **`predict_job` は「利用者が選んだ」を意味しない**(`run_one` が自動で積む) | 採用 | FR-009 / D2a |
| 23 | CLI の既定契機が経路ごとに違うのにテストが無い | 採用 | job-observability §3 |

**この 2 回目のレビューが最大の収穫だった** — 1 回目の指摘を全部直した後の設計に、
憲法 V との衝突と「テストは通るが要件を満たさない」型の欠陥が 5 件残っていた。

**Gate result: 全項目 PASS。VI と技術制約は正当化を伴う → Complexity Tracking に記載。**

## Project Structure

### Documentation (this feature)

```text
specs/086-capture-on-predict/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0 output (D0-D10 + D1a / D2a-D2c)
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── capture-eligibility.md  # 適格判定と差し替えの規約
│   ├── fetch-politeness.md     # プロセス跨ぎ制限とクールダウン
│   ├── job-observability.md    # ops / API / admin の契約追加
│   └── horizon-artifact.md     # 主 horizon の事前登録と新 version
├── checklists/requirements.md  # 完了済み(全 PASS)
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
db/
├── migrations/versions/0013_capture_provenance.py  # 新規
├── src/horseracing_db/__main__.py                  # 新規: db 初の CLI 入口(argparse)
├── src/horseracing_db/dedupe.py                    # 新規: 重複行の退避と復旧
└── src/horseracing_db/models/chaos.py              # 拡張: 出所 2 列 + UNIQUE + 取得抑制表(**退避表は ORM に置かない** — T005)

probability/src/horseracing_probability/
├── chaos_artifact.py            # 拡張: 主 horizon を必須化 / 承認 manifest の解決を集約
│                                #       (status=active・api / live / training の 3 重複を解消)
└── chaos_eligibility.py         # 新規: 表示適格 / 確認適格の判定(純関数)

scrape/src/horseracing_scrape/
└── fetch.py                     # 改修: FetchRefused(403/429 即送出・全利用者に波及)
                                 #       + 注入可能な事前フック(robots も頻度制限に乗せる。既定 no-op)

live/src/horseracing_live/
├── chaos_capture.py             # 改修: 判定 → 排他 → 取得の順序に是正
├── chaos_politeness.py          # 新規: プロセス跨ぎ制限とクールダウン + 捕捉専用 fetcher ファクトリ
└── cli.py                       # 拡張: 単一レースの機械可読出力 + 契機の指定

ops/src/horseracing_ops/
├── runner.py                    # 拡張: 予測処理の直前に捕捉を subprocess 実行
├── enqueue.py                   # 拡張: 予測ジョブの由来(手動 / 自動追随)
├── routers/predict.py           # 拡張: 予測ボタン経路に manual_ui を渡す
└── routers/refresh.py           # 拡張: 単一レース更新ボタンも利用者操作(もう一つの手動源)

api/src/horseracing_api/
├── schemas.py                   # 拡張: 実行記録に捕捉結果を純追加
├── routers/jobs.py              # 拡張: 転記のみ
└── chaos.py                     # 改修: 現行 artifact 解決を status=active に / 表示時の構成一致判定

admin/src/pages/JobsPage.tsx     # 拡張: 捕捉結果の表示(見送りを失敗色にしない)
admin/src/lib/captureLabels.ts   # 新規: 見送り理由の日本語ラベル対応表
front/src/components/RaceChaosPanel.tsx  # 改修: 鮮度表示を窓の幅に追随(FR-022a)
front/src/api/schema.d.ts        # 型を再生成(admin 側と両方コミット)
front/openapi.json               # 再生成
admin/openapi.json               # 再生成(front とバイト一致)
admin/src/api/schema.d.ts        # 型を再生成

training/src/horseracing_training/
├── chaos_bands.py               # 拡張: 契機別内訳 + horizon 層別 + 選択バイアス開示
└── cli.py                       # 拡張: chaos-bands add-horizon

artifacts/chaos_bands/
└── {new_digest}.json            # 主 horizon を含む新 version
config/chaos_bands_approved.json # 承認 digest の差し替え

# artifact の参照先と運用文書(T017b / T018a / T058 — 追跡漏れを防ぐため明示する)
.claude/launch.json              # api が pin している digest を新版へ(現状は superseded を指す)
deploy/README.md                 # 同上(環境変数の節)
training/scripts/export_chaos_outcome_fixture.py  # T018b: digest 張り替え(pin は計 5 ファイル — T018 に列挙)
docs/plan/086-capture-timing.md  # 新規: T018a の実測記録(10s/20s 予算の判定根拠)
memory/chaos-084-capture-operations.md            # T058: 運用メモの是正
```

**Structure Decision**: 084 で確定した依存方向をそのまま踏襲する。

| 置き場 | 理由 |
|---|---|
| `probability/chaos_eligibility.py` | 適格判定は `api`(表示)と `live`(捕捉)と `training`(報告)の**三者**が使う。三者が共有するのは `db` と `probability` だけ |
| `live/chaos_politeness.py` | 取得を行うのは `live` のみ。抑制状態は DB に置き、プロセスを跨ぐ |
| `ops/runner.py` | 捕捉の実装を import せず subprocess で呼ぶ(既存境界を維持) |
| `db/models/chaos.py` | 出所と抑制状態を DB に置くことで、ワーカーが複数でも状態を共有できる |

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 退避表 `chaos_snapshots_quarantine` の追加 | 084 の追記経路が ≥2 行/レースを作りうる環境で、無条件 `UNIQUE(race_id)` を張るには重複を外す必要がある | 黙って削除する案は却下 — void 行は監査記録であり、086 の中心にある「観測を静かに失わない」規律に反する。中断だけする案も却下 — 重複を持つ環境が恒久的に upgrade 不能になる |
| migration 0013(スキーマ追加) | 契機を記録しないと選択バイアスを検知できず(FR-009/011)、取得抑制の状態はプロセスを跨いで共有する必要がある(FR-017) | ファイルロックはコンテナを跨がない。既存テーブルへの相乗りは意味論を汚す |
| 主 horizon を「広い窓」にする | 狭い窓では確認コホートが**約 6 年**かかり前向き検証が事実上完了しない(実測) | 狭い窓は統計的に純粋だが、実測で発走前クリックの 5% しか適格にならない。広い窓の代償(オッズ成熟度の不均一)は **horizon 層別報告のバケットを窓に合わせて分割して**可視化する(既存の 4 バケットでは中央値と 71% が `60m+` の 1 本に潰れて機能しない) |
| 窓外でも捕捉する(表示専用) | **却下した狭い窓なら** 窓外を見送ると 95% で荒れ度が出なかった。採用した広い窓では窓外は 4.4% だが、利用者が待っている経路でそれを捨てる理由がない | 「窓外は完全見送り」(spec 初版の FR-001)は実データと矛盾していたため計画中に是正した |
| **near-post 捕捉の量産はできない**(限界の明示) | 開催日の発走は 8.8 時間に分散し、1 日 1 コマンドでは 1〜2 レースしか窓に入らない | 全レースを T−30 分で捕捉するには 1 日 20〜27 回の実行 = スケジューラが必要で、憲法が先送りしている。**084 に書いた「1 日 1 コマンドで T−30 分」は達成不可能だったので是正する** |
| **窓外で捕捉されたレースは確認適格にならないまま確定する**(実測 4.4%) | 撮り直せば必ずオッズ履歴になる(憲法 V) | 「後から窓内で撮り直して昇格」案は、構成が変わっていないレースを 2 回撮る操作であり V に正面から反する |
| **自動追随の予測から外部取得が発火する** | 取込後に自動で積まれる予測(`predict_auto`)でも捕捉が走る。**起動契機は 024/028 で既存だが、そこから発生する外部ネットワーク取得は 086 で新たに増える** — 正当化はここを避けて通らない | 「`predict_auto` では捕捉しない(明示 opt-in にする)」案を検討したが、取込直後は発走前である確率が高く**最も価値のある捕捉機会**を捨てることになる。増える取得は頻度制限・拒否後のクールダウン・1 レース 1 観測(再取得なし)で三重に抑えられ、時刻を決めて自律的に起動する仕組みは導入しない |
| **手動クリックが RUNNING の予測ジョブに相乗りしない**(028/044 の重複排除の緩和) | 走っているジョブは由来を読み終えており `capture_trigger` を後から貼り替えられない。相乗りすると利用者選択が `predict_auto` として記録され FR-011 / SC-007 が壊れる | 同一レースで予測が二重に走る(55-113 秒 × 2)。追随 recommend は既存の in-flight dedup が吸収する。選択バイアスの正しさを優先した |
| **抑制の状態共有は捕捉経路にしか掛からない** | 既存の日次取得と実行層の取得は同じ取得元を叩くが、抑制を書く配線は入れない | 拒否時の**即送出**(`FetchRefused`)は `scrape` を直すので全利用者に及ぶが、**抑制の読み書き**まで配線すると全経路に DB 依存が入り 086 のスコープを超える。帰結として日次取得が 429 を受けても直後の捕捉は取りに行く — FR-016/FR-017 に残存リスクとして明記した |
| **捕捉後に出走取消が入ったレースは荒れ度が出なくなる** | 凍結観測が現況を記述しなくなるため。追随させれば必ずオッズ履歴になる | 「構成が変わるたびに撮り直す」案も、生き残った馬のオッズを複数時点で保存するので V に反する。憲法 V を改定しない限りこれ以外の設計はない |
