---
description: "Task list for 086 予測実行時の荒れ度スナップショット捕捉"
---

# Tasks: 予測実行時の荒れ度スナップショット捕捉 (capture-on-predict)

**Input**: Design documents from `/specs/086-capture-on-predict/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: **含める。** 本 feature は SC-001..011 と各 contract の「検証」節がテストを明示的に
要求している。とくに **取得回数を数えるスパイ fetcher** は必須である
(理由文字列だけを見るテストは判定順序が逆でも通ってしまうため — codex 指摘「誤った理由で通る
テストがある」への対応)。

**Organization**: user story ごとにフェーズを分ける。US2 → US5 → US1 の順に依存する。

> **ID 順は実行順ではない**。各 Phase 冒頭の「実行順序」行を必ず読むこと
> (T018a / T021b / T025b / T025c / **T036c** / T047a-c が Phase 5 に散在し、T045 は欠番)。
> **順序行がある Phase(3 / 5 / 7)ではそれに従う。他は ID 順でよい。**
> **接尾辞は親子関係を意味しない** — T047a-c は T047(Phase 7)の子ではなく、
> T052a は T052 の子ではない。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(別ファイル・未完了タスクに依存しない)。
  **同一ファイルを触るタスク同士は `[P]` が付いていても直列に実行する**
  (同じテストファイルに複数のテストを足す場合が該当する)
- **[Story]**: US1..US6(spec.md の user story に対応)

## Path Conventions

モノレポ。各パッケージは `<pkg>/src/horseracing_<pkg>/` と `<pkg>/tests/`。
front / admin は `<pkg>/src/`。

---

## Phase 1: Setup

**Purpose**: 品質ゲートを閉じ、実測ベースラインを取り、
後の検証を「変わっていないこと」で言えるようにする

- [x] T000 **codex レビュー(plan + tasks)を実行し採否表を plan.md に追記する**。
      spec 段階のレビュー以降に決まった設計はまだレビューを受けていない:
      migration 0013 の NOT NULL 追加分岐 / `pg_try_advisory_xact_lock` を HTTP 取得の
      外周に置くトランザクション設計 / 25s-45s 予算 / `read_chaos_artifact_raw` の
      ブートストラップ経路 / **void 行の上限と憲法 V の関係**(最重点)。
      **Phase 2 の前段ゲート**とし、採否表を plan.md「Constitution Check」に書くまで先へ進めない。
      **[実施済み 2026-07-27]** 23 件全採用・不採用ゼロ。採否表は plan.md に記録済み。
      最大の収穫は**設計の撤回**(構成が変わるたびの差し替えと窓外→窓内の昇格を廃止し、
      1 レース 1 観測に変更)
- [x] T000a **取得時間の下限を実測する(先出しスパイク)**:
      実レース 1 件に対して **robots.txt の往復 + `fetch_win_odds` の 1 回**だけを実行し、
      端から端の実時間を測る(保存も判定も要らない)。
      **これが 10 秒に収まらないなら、内側の期限も外側の打ち切りも設計し直しになる** —
      T018a の分岐 (b)「捕捉を予測の後に回す」は **FR-013(MUST)の仕様変更**であり、
      (c)「日次のみに戻す」は feature の価値をほぼ消す。
      どちらも Phase 5 まで作ってから判明すると US1 の大半が無駄になる。
      **`chaos_snapshots` は 0 行 = 捕捉は実 netkeiba に対して一度も完走していない**ので、
      10s/20s は上限側の論拠だけで決まっている。T018a は端から端の確認として残す
- [x] T001 ローカル Postgres を起動して接続を確認する(`docker start docker-postgres-1`・
      `DATABASE_URL='postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing'`)
- [x] T001a [P] **DB の `idle_in_transaction_session_timeout` を確認する**:
      `0`(無制限)か **30 秒より長い**こと。捕捉は排他を取ったまま外部取得を行うので、
      これより短いと日次経路(期限 30 秒)が静かに落ちる
      (`contracts/fetch-politeness.md` §4)。短ければ設定を直してから着手する
- [x] T002 [P] ベースライン行数を記録する(`chaos_snapshots` / `chaos_readouts`)。
      **migration の形をこの実測で変えてはならない** — 作者の環境の行数に依存した
      migration は行を持つ環境で壊れる。この記録は事後確認のためだけに使う
- [x] T003 [P] 変更前のテスト状況を記録する(`scrape` / `live` / `probability` / `training` /
      `ops` / `api` / `db` / `admin` / `front` / `features` / **`eval`** /
      **`betting`** / **`serving`** / **`ingest`** を実行し、緑の件数を控える。
      後ろ 3 つは chaos を触らないが、**conftest が `alembic upgrade head` を走らせる**ので
      **migration 0013 の適用可否そのもの**が検証対象になる。
      **この列挙が正本**で、T054 / T055 / quickstart §10 はこれを参照する)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: スキーマと ORM。**全 user story がこれを前提にする**

**⚠️ CRITICAL**: このフェーズが終わるまでどの user story も着手できない

- [x] T004 migration 0013 を作る(`db/migrations/versions/0013_capture_provenance.py`):
      **`down_revision = "0012_chaos_readout"`**(`"0012"` ではない —
      実際の revision 識別子は `db/migrations/versions/0012_chaos_readout.py:21` で
      `0012_chaos_readout`。`"0012"` にすると Alembic がグラフを解決できない)。
      `chaos_snapshots` に `capture_trigger` / `capture_policy_version` を追加する。
      **手順は行数に依存させない**(常に同じ SQL を流す):
      nullable で ADD → `legacy_unknown` / `capture_policy_v0` で backfill(0 行なら no-op)→
      `SET NOT NULL` → CHECK 制約を追加。
      CHECK の許可値は `daily_operational` / **`predict_manual`** / **`predict_auto`** /
      `explicit_command` / `legacy_unknown` の 5 つ。
      遡及ラベルに `daily_operational` を使ってはならない — 084 の CLI は `--date` と
      `--race-id` を両方許すので、既存行がどちらで取られたかの情報が残っていない。
      **`chaos_snapshots` に無条件の `UNIQUE(race_id)` を追加する** —
      086 は「1 レースのオッズ入り行は生涯ちょうど 1 行」を憲法 V の担保としているが、
      0012 の部分 unique index は `WHERE status='active'` なので **void 1 行 + active 1 行が通る**。
      規約とテストだけの担保では「規約は安全境界ではない」という本 feature 自身の原則
      (horizon-artifact §2)と矛盾する。
      既存の部分 index は冗長になるが残す(0012 を書き換えない)。
      **既存の重複行への対処を必ず書く**(行数に依存した migration にしないため —
      084 の出荷済みコード(`chaos_capture.py:452`)は**旧行を void して新しい active 行を
      INSERT する**ので、出走構成の変化を経験した環境には ≥2 行/レースが実在しうる):
      重複があれば **UNIQUE を張る前に型付きの例外で中断し、どのレースが何行持っているかと、
      `dedupe-chaos-snapshots` を実行してから再 upgrade する手順を示す**。
      **退避表はこの migration では作らない**(理由は T004a)。
      **黙って void 行を消してはならない** — 監査記録である
      (086 の中心にある「観測を静かに失わない」規律)。
      どれを残すかは T004a が**決定的な規則**で決めるので操作者の判断は要らない。
      新表 `fetch_throttle_state`(`domain` PK / **`next_allowed_at`**(次に叩いてよい時刻。
      未来時刻を入れるので `last_attempt_at` とは呼ばない)/ `blocked_until` /
      `block_reason` / `updated_at`)。既存の列・index・trigger は一切触らない
- [x] T004a **重複行の復旧 CLI を用意する**(**migration の外**・data-model §2b):
      **`db` にはこれまで CLI が無い**ので新規に作る:
      `db/src/horseracing_db/__main__.py`(argparse)+ `db/src/horseracing_db/dedupe.py`。
      サブコマンド `dedupe-chaos-snapshots [--apply]`(**既定は dry-run**)。
      実行時に `chaos_snapshots_quarantine` を**必要なら作成**する
      (`quarantine_reason` はリテラル `"unique_race_id_backfill"`・data-model §2b)。
      **列は「退避を実行する時点の `chaos_snapshots`」から実測して写す**
      (`information_schema` を読む)。CLI が走るのは 0013 が**中断した後**= DB はまだ 0012 なので、
      `capture_trigger` / `capture_policy_version` は**存在しない**。
      0013 適用後の列集合を前提に固定すると、この経路で必ず落ちる。
      `quarantined_at` / `quarantine_reason` を足したうえで、
      **`status='active'`(無ければ最新 `captured_at`)を残し、それ以外を退避してから削除する**
      決定的な規則で動く(**操作者の判断を要さない**)。**黙って削除しない**(監査記録である)。
      **退避表を migration 0013 で作ってはならない** — Alembic の DDL は 1 トランザクションなので、
      0013 が重複を見つけて中断すると**作った表もろとも巻き戻り退避先が消える**。
      運用は「0013 が中断 → この CLI を実行 → 0013 を再実行」
- [x] T005 ORM を更新する(`db/src/horseracing_db/models/chaos.py`):
      `ChaosSnapshot` に 2 列追加。**無条件 `UNIQUE(race_id)` を `__table_args__` にも書く**
      (書かないと ORM metadata からスキーマを作る経路で INV-2 が効かず、
      INV-1 と同じ「migration にしか無い制約」の穴を新たに作ることになる)。
      新モデル `FetchThrottleState` を追加し(**退避表は ORM に持たせない** —
      T004a の CLI が退避実行時点の列を実測して作るので、静的な列定義と食い違う)、
      `db/src/horseracing_db/models/__init__.py` から export する。
      `chaos_readouts` の append-only 規約(UPDATE 禁止 trigger)は `fetch_throttle_state` に
      **適用しない**(運用状態であって監査記録ではない)
- [x] T006 [P] migration の integration テストを書く
      (`db/tests/integration/test_capture_provenance.py`):
      upgrade → downgrade → upgrade で既存テーブルが保たれる・CHECK 制約が未知の契機を弾く・
      `capture_trigger` を省いた INSERT が NOT NULL で失敗する・
      **0012 の部分 unique index `uq_chaos_snapshots_active_race_id` と
      `chaos_readouts` の UPDATE 禁止 trigger が 0013 適用後も有効である**
      (どちらも migration にのみ存在し ORM の `__table_args__` には無いので、
      INV-1 はこの integration テストでしか担保できない)・
      **無条件 `UNIQUE(race_id)` が効く**(同一レースに 2 行目を入れようとすると
      status を問わず失敗する = 憲法 V の構造的担保)
- [x] T006a [P] **遡及分岐を直接テストする**(同ファイル):
      `0012_chaos_readout` から開始し、**既存の snapshot 行を 1 件仕込んでから** 0013 に上げる。
      migration が成功し、遡及行が `legacy_unknown` / `capture_policy_v0` になり、
      その後の新規捕捉も通ることを確認する。
      **さらに「同一レースに 2 行(void + active)」を仕込んだケースも走らせ**、
      migration が**型付きの例外で中断**し、そのうえで
      **`dedupe-chaos-snapshots --apply` を実行 → 再 upgrade が成功する**ところまで通す。
      中断だけを確認すると、**復旧経路が壊れていても緑になる**
      (084 の出荷済みコードはこの状態を作りうるので、これが高リスク側の分岐である)。
      「作者の環境が 0 行だった」ことに依存せず、**両方の分岐を実際に走らせる**
- [x] T006b **migration 0013 で赤くなる既存テストをすべて更新する**(実測で 5 ファイル):
      (a) `db/tests/integration/test_chaos_tables.py` — `_SNAPSHOT_COLUMNS`(18 行の 12 キー厳密集合)に
      2 列を足す。`_CHAOS_TABLES`(17 行)を使う downgrade テスト(90-101 行)は
      **`fetch_throttle_state` が `existing_tables` 側に入って落ちる**ので、
      0013 で追加されるテーブルを除外集合に加える。
      (b) **`ChaosSnapshot(...)` を組む全テストに 2 列を渡す**(NOT NULL) —
      `db/tests/integration/test_chaos_tables.py` / `live/tests/integration/test_chaos_capture_db.py` /
      `api/tests/unit/test_race_chaos.py` / `api/tests/integration/test_race_chaos_api.py` /
      `api/tests/perf/test_chaos_readout_p95.py`。
      **`db/tests/conftest.py:40` と `api/tests/conftest.py:42` は `upgrade(cfg,"head")` するので、
      0013 適用後は NOT NULL 違反で即座に落ちる**。
      T034b は同じ 2 ファイルの `void_reason` を直すが NOT NULL には触れていない。
      **これを落とすと Phase 2 の「スキーマ準備完了」が嘘になり、
      T003 のベースライン件数との突合で原因不明の回帰として現れる**。
      (c) **`live/tests/integration/test_chaos_golden_case.py`** — `ChaosSnapshot(...)` は
      作らないが `capture_chaos(...)`(`:95`)と `load_current_chaos_artifact()`(`:94`)を
      直接呼ぶので、**T006c(契機がキーワード必須)と T014(窓が必須)の両方で落ちる**。
      084 の SC-008 意味論ゴールデンテストで、この feature の影響範囲で最も価値の高い
      回帰ガードなので黙って赤のままにしない。**T017 で窓付き artifact を発行するまで
      緑にならない**ことを明記する
- [x] T006c **`capture_chaos` に出所の引数を足す**(**Phase 5 ではなくここ**):
      `capture_chaos(..., *, capture_trigger, capture_policy_version)` をキーワード必須引数で受け、
      `ChaosSnapshot` に保存する。新規捕捉は `capture_policy_v1` を書く。
      **T004 の NOT NULL が入った瞬間から `live/tests/integration/test_chaos_capture_db.py` は
      `capture_chaos` を **7 箇所**(`:100,148,167,182,213,223,250`)で呼び、
      `test_chaos_golden_case.py` も 1 箇所呼ぶので、書き手を Phase 5 に置くと Phase 2-4 が赤のまま**になる
      (T006b の修正だけでは緑にできない)。
      T035 はこの引数を呼び出し側に配線するタスクとして Phase 5 に残る
- [x] T006d **無条件 `UNIQUE(race_id)` で赤くなる 084 のテストを同時に直す**(**2 本ある**):
      `:164-201` の `test_late_scratch_voids_old_snapshot_and_appends_one_new_active_row`
      (**`len(snapshots) == 2` を assert**)と、
      **`:213-241` の再捕捉テスト**(`void_reason == "recaptured"` — こちらも
      2 行目の INSERT で `IntegrityError` になる)。
      どちらも T006e で挙動が変わるので**同じフェーズで直す**
      (T034b は Phase 5 に残るが、赤くなるのは Phase 2 なのでここで潰す)。
      084 の追記経路そのものを固定するテストなので、**制約を入れる Phase 2 で同時に直す**
      (T034/T034b は Phase 5 で実装本体を直すが、テストはここで先に落ちる)。
      「取消では行が増えず `void/field_changed` になる」に書き換える
- [x] T006e **再捕捉の廃止(void を其の場で書く挙動)を Phase 2 で入れる**
      (`live/src/horseracing_live/chaos_capture.py`):
      T004 の無条件 `UNIQUE(race_id)` は、084 の追記経路(`:452` で旧行を void して
      **新しい active 行を INSERT**)と**同じフェーズでは共存できない** —
      入れた瞬間に `IntegrityError` になる。
      T006c が NOT NULL のために書き手を Phase 2 へ前倒ししたのと同じ理由で、
      **追記をやめて既存行を `status='void'` / `void_reason='field_changed'` に UPDATE する**
      中核だけをここで入れる。
      **暫定の戻り値も明示する**(Phase 2-4 の間の挙動が未定義にならないように):
      行が存在すれば `skipped/already_captured` を返し、
      **void にするのは凍結構成が現況と違うときだけ**。
      これは最終形と同じなので、T034 は本当に残余だけになる。
      無条件に void にすると、同一構成での再実行が**有効な観測を毎回壊す**
      (T006d の書き換え後のテストは通ってしまう)。
      判定順序の是正(T032)・抑制の結線(T036)・出所の配線(T035)は Phase 5 のまま。
      これを分けないと Phase 2 の「スキーマ準備完了」も Phase 3/4 のゲートも**達成不能**になる
- [x] T007 [P] migration head アサーションを 0012 → 0013 に更新する
      (`features/tests/unit/test_feature020_leak_guard.py` /
      `test_feature021_leak_guard.py` / `test_feature023_leak_guard.py` /
      `test_feature040_leak_guard.py` / `test_feature066_leak_guard.py` /
      `test_feature084_leak_guard.py` / `test_materialize_fallback_columns.py` /
      `live/tests/unit/test_no_schema_change.py`)
- [x] T008 実 DB に migration を当てる(`cd db && uv run alembic upgrade head`)。
      適用前後で `chaos_snapshots` / `chaos_readouts` の行数が同一であることを確認する
- [x] T008a **`CLAUDE.md` の 086 ブロックを最新の設計に揃える**(**[計画中に実施済み]** — 着手時に差分確認だけ行う)(Phase 9 ではなく**ここ**)。
      実装時に最初に読まれる文書なので、撤回済みの設計を読みながら実装する事故を防ぐ。
      着手時に **plan.md の Summary / Constitution Check・spec の FR 一覧・tasks の件数・
      `contracts/capture-eligibility.md` §1 の判定順序**と読み比べ、差分を潰す。
      計画中に大半は是正済みなので、着手時は**差分確認だけ**を行う
      (**予算は契機ごと**=predict 10s / 外側 18s、daily・explicit 30s(外側なし)・
      **`_make_fetcher` は変えず `make_capture_fetcher` を新設**・
      **SC は SC-011 まで**・契機 5 値・1 レース生涯 1 行・FR-008a(削除済み) 削除・
      取得前判定は行の存在のみ・非遡及は単一 digest スコープ由来・
      昇格関数は単目的・バケット分割)。
      着手時に **plan.md の Summary / Constitution Check と読み比べて差分がないことを確認する**
      (計画がさらに動いていたら追随する)

**Checkpoint**: スキーマ準備完了 — user story に着手できる。
**Phase 2 は全緑でなければならない**。窓を必須にする T014 は Phase 3 なので、
この時点で赤いテストがあれば本物の欠陥である。
(**Phase 3 の途中**では `test_chaos_golden_case.py` と
**実 digest を pin している 5 ファイル**が赤くなるが、T016-T017-T018b で緑に戻る。
**Phase 3 の checkpoint 時点では全部緑**でなければならない。)

---

## Phase 3: User Story 2 - 主 horizon の事前登録 (Priority: P1・US1 の前提)

**Goal**: 確認コホートに入る観測を「事前に決めた時刻の窓で取られたもの」に限る。
084 の出荷済み欠陥(`0..∞` への暗黙フォールバック)を是正する。

**Independent Test**: 主 horizon を持たない凍結設定で前向き報告が**処理を止める**
(暗黙に全時刻を受理しない)= SC-006。

**実行順序(ID 順ではない)**:
T009 / T010 / T011 / T012b(テスト)→ T013 → **T014 → T018**(ローダを固くしたら fixture も同時に)
→ T014a → T015 → T016 → **T017 → T017b + T018b** → T012 / T012a
(T017a は fixture ベースで実 digest に依存しないので、上の並列テスト群に含める)

### Tests for User Story 2

- [x] T009 [P] [US2] 適格判定の純関数テストを書く
      (`probability/tests/unit/test_chaos_eligibility.py`):
      境界値 600 / 86400 が**両端とも窓内**・599 / 86401 が窓外・`seconds_to_post is None` は
      確認適格でない・`capture_strength != "confirmatory"` は確認適格でない・
      表示適格と確認適格が独立に判定される・
      **層別バケットの境界値**が例外にならず正しいバケットに落ちる —
      窓下限 600 の場合(600 / 1799 / 1800 / 43200 / **86400**)と、
      **窓下限を 300 にした場合**(300 / 599 も落ちる)と、

      **窓上限を 20000 に縮めた場合**(`21600` 以降のバケットが
      そもそも生成されない = 構造的に空のバケットが出ない)を検証する。
      **ラベル文字列そのものも assert する** — `[600, 86400]` で
      `10-30m / 30-60m / 1-3h / 3-6h / 6-12h / 12h+` の 6 本ちょうど。
      所属だけを見るテストは、境界の比較演算子を 1 つ間違えて
      `30-60m` が `0-1h` になっても緑のまま通る。
      **窓上限 20000 でもラベルを assert する** — ちょうど
      `10-30m / 30-60m / 1-3h / 3h+` の 4 本で最上位の上限は `None`
      (この主張は現状 T044 にしかなく、規則を持つモジュール側で押さえていない)。
      **`[600, 1700]` のような内側の境界を 1 つも含まない窓**でも
      ラベルが `10m+` になり `0h+` にならないこと

      (最上位は上限を閉じない・最下位の下限は artifact 由来)
- [x] T010 [P] [US2] artifact ローダの fail-closed テストを書く
      (`probability/tests/unit/test_chaos_artifact_horizon.py`):
      `primary_horizon` 欠落 / Mapping でない / `minimum` が非整数 or 負 /
      `maximum` が `null` / **`maximum` が整数でない** / `maximum < minimum` /
      **`maximum == minimum`**(幅ゼロの窓)/
      **短縮別名 `min_seconds_to_post` を使った窓**・**`max_seconds_to_post` を使った窓**の
      各ケースで**型付きエラー**になる(現行は 2 つとも受理する・`chaos_bands.py:1817-1824`)
- [x] T011 [P] [US2] 3 経路の fail-closed 統合テストを書く
      (`training/tests/integration/test_horizon_fail_closed.py` /
      `live/tests/unit/test_capture_artifact_gate.py` /
      **`api/tests/integration/test_race_chaos_api.py`**):
      窓なし artifact で表示・捕捉・報告の**3 経路とも**止まる(SC-006・INV-4)。
      **api も対象経路である**(`api/src/horseracing_api/chaos.py:192` が
      `load_chaos_artifact` を呼ぶ)ので、api の脚を落とすと INV-4 が 2/3 しか担保されない
- [x] T012 [P] [US2] 新旧 artifact のバイト一致テストを書く
      (`probability/tests/unit/test_chaos_artifact_horizon.py` に追加):
      1. **fixture を用意する** — `artifacts/` は `.gitignore` の `/artifacts` で除外されるので、
         `f190e65c…` / `e782c2…` / 新 version の payload を
         `probability/tests/fixtures/` にコミットする(実 artifact に依存すると
         新しいチェックアウトや CI で実行できない)。fixture 化により **T017 への依存は無い**。
      2. **旧側は不変の fixture を独立に読む** — 昇格関数の出力同士を比べない
         (関数が λ / 五分位境界 / 確率を**両側に同じように**壊しても等価テストは緑になる)。
      3. 同一の凍結フィールドから `chaos_readout` を計算し、バンド・全確率・`expected_s` が
         完全一致することを assert する(INV-7・SC-010。
         **`artifact_digest` は必ず変わるので射影から除く** — payload は自分の digest を
         内蔵し `chaos_artifact.py:208-212` が「`artifact_digest` を除いた全体」の
         ハッシュと照合するため、窓を足せば digest も書き換えねば**ローダに拒否される**。
         **`version`(`chaosbands-v1`)は据え置く** — `live/chaos_capture.py:347` が
         `artifact.version` を `chaos_snapshots` に永続化しており、bump すると
         SC-010 の 10 例外に無い出力変更になる)。
      4. **superseded 系列も同じ主張で押さえる**(`f190e65c… ≡ e782c2…`)—
         T017b が `.claude/launch.json` の参照を f190e65c… から新 digest へ移すので、
         この等価性が張り替えを値保存にしている根拠になる
         (実測では λ / 五分位境界 / fit ハッシュがバイト同一で、
         差分は `preregistration` / `code_sha` / digest のみ)。
      5. **payload の差分が `preregistration.primary_horizon` の追加と `artifact_digest` の再計算の**ちょうど 2 点**であること**を機械的に assert する
         (T016 の差分表示は目視なので回帰を止められない)。
         **digest の再計算は `upgrade_legacy_artifact_horizon` の責務**である
         (呼び出し側が忘れると自己整合検証で落ちる)。
      6. **v1 のファイル自体が変更されていないこと**を assert する
         (`artifacts/chaos_bands/{v1_digest}.json` の digest が発行前と一致する。
         create-only は実装の性質としてしか書かれておらず、誰も検証していない)。
      (ファイルを生読みし直さない)
- [x] T012a [P] [US2] 昇格関数が**未承認・不正な digest を拒否する**ことをテストする
      (`probability/tests/unit/test_chaos_artifact_horizon.py` に追加)。
      汎用の生読み関数が存在しないこと(= `add-horizon` の入力が
      承認済み digest に限られること)の担保
- [x] T012b [P] [US2] manifest の解決をテストする(**2 関数を別々に**。
      許可集合は **superseded を含む**・現行解決は **active の唯一の項目**を返す。
      許可集合まで active に絞ると、superseded を明示指定した過去報告が
      実行できなくなる[FR-007])
      (**`probability/tests/unit/test_chaos_manifest.py`** — T014a で解決子を
      `probability` に集約するため。`api` / `live` / `training` はいずれも
      `horseracing-probability` に依存している):
      `status="active"` の項目が返る・`active` が 0 件 / 2 件なら型付きエラー・
      **末尾の項目を返さない**(現行コードは `approved[-1]` で superseded を読んでいる)

### Implementation for User Story 2

- [x] T013 [US2] 共有純関数モジュールを作る
      (`probability/src/horseracing_probability/chaos_eligibility.py`):
      `primary_horizon(artifact)` / `within_primary_horizon(seconds_to_post, horizon)` /
      `display_eligible(snapshot, started_now)` /
      `confirmation_eligible(snapshot, artifact, started_now)` /
      **`capture_horizon_buckets(minimum, maximum) -> list[(label, lo, hi)]`**(列挙)と
      **`capture_horizon_bucket(seconds_to_post, *, minimum_seconds_to_post, maximum_seconds_to_post)`**(分類)
      (層別バケット。**バケット構成は窓の両端から導出する**
      (`contracts/horizon-artifact.md` §5 の `capture_horizon_buckets(minimum, maximum)` が正本)。
      下限だけを引数にすると、上限を縮めた版で `21600` 以降のバケットが生成されて
      **構造的に常に空**になる = SC-010 の例外 5 が直したのと同じ欠陥の再発。
      600 を焼き込むと `add-horizon` が min<600 の版を発行した瞬間に報告が `ValueError` で落ちる。
      `training/chaos_bands.py::_capture_horizon` はこれを import して使う —
      該当バケットが無いと `ValueError` を送出する仕様(def `:1797` / raise `:1803`)なので、
      **最上位を開いたままにする不変条件を純関数テストで固定する**)。
      `started_now` は呼び出し側が DB から渡す(純関数は DB を触らない)。
      DB に依存しない。境界は両端を含む(`>=` / `<=`)。
      **`maximum is None` の分岐は置かない**(ローダが既に拒否している・A3)
- [x] T014 [US2] artifact ローダに窓の検証を足す(FR-005 / FR-006)
      (`probability/src/horseracing_probability/chaos_artifact.py`):
      `preregistration.primary_horizon` を**必須項目**として検証し、
      欠落・型不正・`maximum is None`・**`maximum <= minimum`**(幅ゼロの窓は
      確認適格が構造的に到達不能)を型付きエラーで拒否する。
      **下限に上限値を課さない**(`minimum >= 1800` を弾く等はしない)—
      バケットのラベルは窓の両端から導出する規則(horizon-artifact §5)なので
      どの下限でも破綻せず、表示の都合を安全境界に持ち込む理由が無い。
      084 が当初推奨した T−30 分窓 `[1800, …]` もこの規則なら正当に発行できる。
      **短縮別名 `min_seconds_to_post` / `max_seconds_to_post` を `primary_horizon` の中で
      受け付けない**(`chaos_bands.py:1817-1824` は現状これを許している)。
      運用下限と同じ名前を窓の中で使えると FR-004a の命名分離が崩れる
      **単一箇所**なので api / live / training の 3 経路が一度に fail-closed になる。
      **同時に単目的の `upgrade_legacy_artifact_horizon(path, *, expected_digest, ...)` を
      追加する** — 既知の承認済み digest(= **manifest 掲載。`status` は問わない** —
      現行解決の `status="active"` とは別概念で、v1 が superseded になった後も
      入力として受理する)しか受け付けず、窓以外の検証はすべて通し、
      窓を足した新 payload と digest を返す。これが無いと窓を持たない v1 が二度と読めず、
      新 version の発行(T016)と INV-7 テスト(T012)が実行不能になる。
      **汎用の生読み関数は作らない** — 呼び出し元を静的テストで縛るのは規約であって
      安全境界ではなく、未承認・不正な artifact を `add-horizon` の入力にできてしまう
- [x] T014a [US2] 現行 artifact の解決を `status="active"` に是正し、**実装を 1 箇所に集約する**。
      現状は同じ述語が **3 箇所**に重複している —
      `live/chaos_capture.py::approved_digests_from_manifest`+`:541` /
      `api/chaos.py::_approved_digests`+**`:197-198`** の
      `artifact.artifact_digest != approved[-1]` ゲート /
      **`training/cli.py:1606-1611` のインライン parser**。
      **3 箇所すべてを解決子の呼び出しに置き換える**(training は明示 digest でロードするので
      `status` を見る必要は無いが、parser を 3 つ持つと将来どれかが規約を学び損ねる)。
      **安全に関わる述語を二重に持たない** — 解決子を
      `probability/src/horseracing_probability/chaos_artifact.py` に置き
      (`api` / `live` / `training` はいずれも `horseracing-probability` に依存している)、
      3 箇所はそれを呼ぶだけにする。
      **ただし 1 つの関数に統合してはならない** — 2 つの別概念である:
      1. **`approved_digests_from_manifest() -> tuple[str, ...]`**(**`status` 不問**)
         = 「manifest に載っているか」の許可集合。
         `upgrade_legacy_artifact_horizon` の入力規則(horizon-artifact §2)と、
         `upgrade_legacy_artifact_horizon` の入力規則(horizon-artifact §2)と、
         **将来 v3 以降で superseded になった窓付き版を明示指定する過去報告**
         (FR-007 の単一 digest スコープ)がこれに依存する。
         (現時点の 2 版はどちらも窓を持たないので後者はまだ成立しない —
         いま効くのは昇格関数の入力規則の方である。)
      2. **`resolve_current_digest() -> str`**(**`status="active"` の唯一の項目**・
         0 件 / 2 件以上は型付きエラー)= 「今どれを読むか」。
      **`active` 限定の 1 関数で 3 箇所を置き換えると、superseded を明示指定した報告が
      実行できなくなり FR-007 の運用が壊れる**。
      `live`(:541)と `api`(:197)の**現行判定**は 2 を使い、
      `live` の許可集合と `training/cli.py` の明示 digest ロードは 1 を使う:
      現状の `approved[-1]`(541 行)は **manifest の末尾 = superseded の artifact を
      読んでいる**(`config/chaos_bands_approved.json` は active を先頭に置いている)。
      `status` フィールドは manifest にあるのに一度も参照されていない。
      `status="active"` の唯一の項目を解決し、0 件 / 2 件以上は型付きエラーにする。
      **086 とは独立に存在する 084 の欠陥である** — 現 manifest は active が先頭・
      superseded が末尾なので `approved[-1]` は **superseded** を指す。
      帰結は環境変数がどの digest を指しているかで変わる:
      `.claude/launch.json` は f190e65c…(superseded)を指しているのでゲートは通るが
      **superseded の版から読んでいる**。active の e782c2… を指す設定なら
      ゲートに弾かれて**荒れ度が出ない**。
      いずれにせよ api 側を直さないと T017 で新 digest を発行した瞬間に破綻する(T017b)
- [x] T015 [US2] `training` のフォールバック分岐を削除する
      (`training/src/horseracing_training/chaos_bands.py`):
      `_primary_horizon`(1806-1837 行)の `{minimum: 0, maximum: None,
      artifact_field_present: False}` へ落ちる分岐を**丸ごと削除**し、
      `_within_primary_horizon` ともども T013 の共有関数に置き換える。
      **報告の `primary_horizon` 出力キーを凍結する**:
      `mode`(常に文字列 `"artifact_seconds_to_post_window"`。
      フォールバック時の `"sole_active_confirmatory_snapshot_per_race"` は消える)/
      `minimum_seconds_to_post` /
      `maximum_seconds_to_post` の 3 つ。**`artifact_field_present` は削除**
      (フォールバックが消えて常に true になり情報量がゼロになるため)
- [x] T016 [US2] 新 version を発行する CLI を足す
      (`training/src/horseracing_training/chaos_bands.py` + `cli.py`):
      `chaos-bands add-horizon --artifact <digest>
      **`--primary-horizon-min-seconds-to-post` / `--primary-horizon-max-seconds-to-post` /
      `--primary-horizon-basis`(必須)**。
      `basis` を定数で焼き込んではならない — 窓ごとに根拠が変わるので、
      `measured_coverage_…` について禁じたのと同じ失敗モードになる。
      **バケット構成が窓から機械的に導出される**(horizon-artifact §5 の `buckets()`)
      (最下位バケットのラベル式 `{min//60}-29m` が `31-29m` のように破綻し、
      範囲も空になるため・horizon-artifact §5)。
      **運用下限の `--min-seconds-to-post`(`live capture-chaos`)と同じ綴りにしてはならない** —
      FR-004a の命名分離を CLI の表層でも守る。
      内部で `upgrade_legacy_artifact_horizon` を呼ぶ(承認済み digest 限定)。
      **承認 manifest は書き換えない** — artifact ファイルを書くところまでが `add-horizon` の責務で、
      `status="active"` の付け替えは **T017 の手作業**である。
      自動承認にすると「承認済み digest しか受け付けない」という安全境界が
      自己承認で無効化される。
      **create-only** — 元の artifact は書き換えず、`preregistration.primary_horizon` だけを
      足した新しい digest を `artifacts/chaos_bands/` に書く。
      **`primary_horizon` には 4 キーすべてを書く**: `minimum_seconds_to_post` /
      `maximum_seconds_to_post`(CLI 引数)+ `basis` / 
      `measured_coverage_of_pre_race_predict_clicks`。
      **`basis` は必須・`measured_coverage_…` は省略可**とする。
      カバレッジは窓に依存する実測値だが、CLI から予測ジョブ履歴を集計するのは
      artifact 発行の責務ではない。**指定窓が `[600, 86400]` のときだけ D1 の実測値 0.956 を書き、
      それ以外の窓では省略する**(定数を焼き込むと別の窓の artifact が虚偽のメタを持つ)。
      ローダは付帯メタの欠落を拒否しない(記述であって判定に使わない)。
      ローダの検証(horizon-artifact §2)は下限・上限の 2 キーのみを必須とし、
      付帯メタの欠落は拒否しない(記述であって判定に使わないため)。
      **`artifact_digest` は必ず再計算する**(payload は自分の digest を内蔵し、
      `chaos_artifact.py:208-212` が「`artifact_digest` を除いた全体」のハッシュと
      照合する。据え置くと新 artifact は**ローダに拒否され**表示・捕捉・報告が全部止まる)。
      **`version` は据え置く**(`chaosbands-v1`。`live/chaos_capture.py:347` の値が `:475` で
      **`chaos_readouts.artifact_version`** に永続化されるので、bump は SC-010 の例外に無い出力変更)。
      **それ以外は逐語コピーする**
      (`as_of` / `code_sha` / `calibration_status` も書き換えない。
      `approved_at` は**manifest 側のフィールドで artifact payload には無い** —
      INV-7 は readout のバイト比較なのでこれらの差異を検出できない)。
      出力に**全キーの差分**を出し、差分が `preregistration.primary_horizon` の追加と
      `artifact_digest` の再計算の**2 点だけ**であることを目視できるようにする。λ2 / λ3 / 五分位境界 /
      `fit_input_hash` / `race_set_hash` / `fit_through` / `valid_from` が同一であることを
      出力で示す
- [x] T017 [US2] 新 artifact を実際に発行して承認する
      (**入力は現 `status="active"` の `e782c255…`**。superseded の f190e65c… を渡しても
      INV-7 は値が同一なので通ってしまい、系譜だけが静かにずれる):
      `--primary-horizon-min-seconds-to-post 600 --primary-horizon-max-seconds-to-post 86400`
      で実行する。
      `config/chaos_bands_approved.json` に新 digest を **`status="active"`** で追加、
      現 e782c2… を **`status="superseded"`**(+ `superseded_by`)に変える。
      行は残す(create-only・監査痕跡)。
      **manifest の新エントリは既存エントリと同じ項目を埋める**
      (`version` / `band_axis` / `fit_from` / `fit_to` / `fit_through` / `valid_from` /
      `n_races_fit` / `lambda2` / `lambda3` / `calibration_status` / `approved_at` /
      `approved_note`)。`basis` と `measured_coverage_…` は **payload 側**に T016 が書くので
      manifest には重複させない
- [x] T017b [US2] **api / 運用の artifact 参照を新 digest に張り替える** — **対象ファイルを明示する**:
      api は `CHAOS_BANDS_ARTIFACT_PATH` で**特定のファイルパス**を読む
      (`api/src/horseracing_api/chaos.py:50,183,187`)。
      **`.claude/launch.json:9` は現在 `f190e65c…`(superseded)を指している**ので、
      T014a で解決を `status="active"` に是正した瞬間に**ローカル開発の荒れ度表示が壊れる**。
      更新対象: **`.claude/launch.json`** / `deploy/README.md`(§ 環境変数)/
      運用メモの起動手順。すべて新 digest のパスに揃える。
      **実際に API を起動してレース詳細で荒れ度が出ることを確認する**(SC-010)
- [x] T017a [P] [US2] **報告が単一 digest スコープであること**をテストする
      (`training/tests/unit/test_prospective_scope.py`):
      窓を持たない旧 digest の readout を仕込んでも、新 digest の報告に**現れない**
      (`load_prospective_rows` の `WHERE ChaosReadout.artifact_digest == …`・
      `chaos_bands.py:1714`)。
      **これが FR-007(非遡及)の実体**であり、観測ごとに artifact を解決し直す仕組みは要らない
- [x] T018 [US2] 窓を持たない artifact fixture のうち**成功経路を期待しているものだけ**を更新する
      (`training/tests/` / `live/tests/` / `api/tests/` / `probability/tests/` 配下)。
      **本タスクは合成 fixture への `primary_horizon` 追記だけを行う**(T014 の直後に実行できる)。
      **実 digest を pin している側の張り替えは T018b**(新 artifact が出来た後でないと実行不能)。

      実 digest を pin しているのは**実測 5 ファイル**で、いずれも窓なしの `f190e65c…`
      (= superseded)を指しており、**T014(窓必須)と T014a(active 解決)の両方で赤くなる**:
      `training/tests/fixtures/chaos_outcome_fixture.json:5` /
      `training/scripts/export_chaos_outcome_fixture.py:15` /
      `api/tests/perf/test_chaos_readout_p95.py:33` /
      **`api/tests/unit/test_race_chaos.py:28-29`** /
      **`api/tests/integration/test_race_chaos_api.py:21-22`**(`CHAOS_ARTIFACT_PATH` を monkeypatch)。
      1 つ目は **084 の SC-008 意味論ゴールデン回帰**(`test_chaos_outcome_regression.py`)の
      入力であり、**SC-010「荒れ度の値・バンド・λ が不変」の主要な担保**である。
      **黙って通る fixture を残さない** — 更新漏れは T011 のテストが検出する。
      **次の 2 種類は意図的に窓なしのまま残す**(消すと他タスクが実行不能になる):
      (a) T010 / T011 が **fail-closed の拒否を assert する**ための窓なし artifact、
      (b) T012 が INV-7 で**独立に読む不変の v1 fixture**。

- [x] T018b [US2] **実 digest を pin している 5 ファイルを新 digest に張り替える**
      (**T017 の直後・T017b と同時に実行する** — 新 artifact のファイルが無いと実行不能):
      T018 に列挙した 5 ファイル。fixture の追記ではなく**参照先の張り替え**である。
      `chaos_outcome_fixture.json` は 084 の SC-008 意味論ゴールデン回帰の入力なので、
      **張り替え後も outcome がバイト一致すること**を確認する(荒れ度の値は不変・INV-7)

**Checkpoint**: 窓が事前登録され、どの経路も窓なし artifact を受け付けない。
**フェーズ途中で赤くなるテストがある** — `live/tests/integration/test_chaos_golden_case.py` と、
**実 digest を pin している 5 ファイル**(T018 に列挙)。
いずれも T014(窓必須)/ T014a(active 解決)で赤くなり、
T016-T017 で新 artifact が出て T018b が張り替えると緑に戻る。
**checkpoint 時点では全部緑に戻っていること** — 赤いまま次へ進まない。

---

## Phase 4: User Story 5 - 取得の礼儀とレート制御 (Priority: P1)

**Goal**: 予測ボタンの連打・同時実行が取得元への負荷にならないようにする。
UI クリックが取得を駆動する前に、この安全弁を先に入れる。

**Independent Test**: `chaos_politeness` 単体で予約・待機・クールダウンが機能し、
429 に対し 1 回目で送出・再試行なし。
**捕捉経路への結線(T036)後の振る舞い(`skipped/source_cooldown`・取得 0 回・SC-009)は
Phase 5 の T036a / T036b で検証する** — Phase 4 の時点では `capture_trigger` が
NOT NULL(T004)で書き手 T035 も未実装なので、保存まで走る検証は必ず落ちる。

### Tests for User Story 5

- [x] T019 [P] [US5] `FetchRefused` のテストを書く(`scrape/tests/unit/test_fetch_refused.py`):
      403 / 429 が**1 回目で**送出される・スパイの呼び出しが **1 回**・
      **robots.txt が 429 を返したら `FetchRefused` が送出され、本取得の `client.get` が 0 回**
      (`_robot_allows` は現状すべての非 200 を「robots 無し=許可」に潰すので、
      ここを見ないと SC-009 が素通りする)・
      **robots.txt の 403 は従来どおり「robots 無し=許可」に落ちる**
      (`contracts/fetch-politeness.md`。403 を拒否に変えると、取得元が robots.txt に
      403 を返すだけで 5 つの取込 CLI が全部止まる。
      **この分岐は scrape の既存テストでは守れない**ので、ここで両方向を固定する)。
      **クールダウンの書き込みは検証しない** — 書くのは live の `chaos_politeness` であり、
      scrape 層の被テストコードは `fetch_throttle_state` を知らない
      (フェイクを噛ませると空虚な緑になる)。その検証は T020 / T036a が持つ・
      その他の非 200 は従来どおり予算内でリトライする
- [x] T020 [P] [US5] プロセス跨ぎ制限のテストを書く
      (`live/tests/integration/test_chaos_politeness.py`):
      **`chaos_politeness` モジュール単体**で検証する(`capture_chaos` 経由の検証は
      結線後の T036a / T036b — Phase 4 では `capture_trigger` が NOT NULL で保存段が落ちる)。
      **別インスタンス**の fetcher 2 つで連続取得すると 2 回目が 1 秒以上待つ
      (現状は待たない)・`blocked_until` 中は予約 API が `SKIP(source_cooldown)` を返す・
      `blocked_until` 経過後は通常どおり取得する・
      **抑制行が無い状態で同時 2 プロセスが初期化しても一意制約違反が起きない**・
      **B が予約して待機中に A が 429 で抑制 → B の取得が 0 回**(FR-017a)・
      **多数の同時予約で待機が上限を超えたら `throttle_backlog`**(FR-017b)・
      **実 fetcher で robots.txt と本取得の 2 本が 1 秒以上離れる**
      (`HttpFetcher.get` は本取得の前に robots.txt を別途送り、その送信は `_rate_limit` を
      **通らない**(`fetch.py:111`)ので、放置すると 1 捕捉で 2 本が無制限に飛ぶ)・
      **実 CLI が叩く URL から導出したキーが抑制行のキーと一致する**
      (`scrape/fetch.py::_domain` と同一規則。別キーだと**全テスト緑のまま**
      プロセス跨ぎ制限が無効になる — T024 が名指ししたリスクの検証)
- [x] T021 [P] [US5] **fetcher 単体で実時間の期限を検証する**(実 CLI 経路は T021b)
      — **`--capture-deadline-seconds` と経路別既定は T037(Phase 5)で入る**ので、
      Phase 4 では **T025 の `deadline_for("predict_manual")` = 10 秒**に対して検証し、
      経路別既定(predict 10 / それ以外 30)の確認は **T043** が担当する
      (`live/tests/integration/test_capture_deadline.py`):
      遅い HTTP サーバ(**robots.txt も遅い**)に対して計測する。
      **Phase 4 では fetcher 単体の実時間計測のみ**を行う(行数・排他解放のアサーションは
      付けない — T032 / T035 が未実装で保存段が必ず落ち「理由が違うまま緑」になる)。
      **結線後の end-to-end 検証は T021b(Phase 5)が担当する** —
      チェックが付いた後に必須の再実行が黙って落ちるのを防ぐため、別 ID にする。
      **静的な不等式テストでは不十分**(FR-018)— `HttpFetcher.get` は取得の前に
      robots.txt を別途取得し(`fetch.py:111`)、httpx の `timeout` は
      段階ごとの上限であって全体の上限ではないため、足し算の見積もりから漏れる
- [x] T021a [P] [US5] 実際に構成される HTTP クライアントを検証する
      (`live/tests/unit/test_capture_fetcher_config.py`):
      **`live/cli.py` の捕捉経路が実際に `make_capture_fetcher` から fetcher を得ている**こと
      (新ファクトリを直接呼ぶだけのテストは、本番経路が旧 `_make_fetcher` のままでも緑になる)・
      その fetcher の**各段階の timeout が
      `connect=3.0 / read=5.0 / write=5.0 / pool=3.0` にちょうど一致する**こと
      (「内側の期限以下」だけでは契約の値と違う組でも緑になる)・
      **既存 scrape 経路の fetcher が timeout 20s / `max_retries` 実効 3 のままである**こと
      (`_make_fetcher` は `max_retries` を渡しておらず `HttpFetcher` の既定を継承している)
      (`_make_fetcher` を書き換えていない証拠)・
      **`max_retries == 1`** であること・
      **実 fetcher に非 200 を返させたとき、対象 URL への `client.get` が
      ちょうど 1 回**であること(`0` だと 0 回になるがスパイでは検出できない)。
      **robots.txt の取得は別に数える** — `_robot_allows` は同じ `client.get` を使い、
      `self._robots` はインスタンス変数で捕捉は毎回新しい fetcher を作るので、
      初回のホスト接触は必ず 2 本(robots + 対象)になる。
      「合計 1 回」と書くと必ず落ちるか、実装時に緩められる
      (「20 秒のままでない」だけでは 15 秒でも緑になる)。
      新しい定数を足しただけでは実際の挙動は変わらない
      (CLI は `scrape/cli.py::_make_fetcher` 経由で `httpx.Client(timeout=20.0)` を作る)

### Implementation for User Story 5

- [x] T022 [US5] 型付き例外を追加する(`scrape/src/horseracing_scrape/fetch.py`):
      `class FetchRefused(FetchError)` に `status_code` を持たせ、
      `_fetch_with_backoff` が 403 / 429 を受けたら**バックオフに入らず即座に送出**する
      (リトライ回数を消費しない)。
      **送出位置に注意** — 既存の `try` 内で raise すると同じブロックの
      `except Exception as exc`(`fetch.py:154`)が捕まえて `last_err` に落とし、
      **バックオフして 3 回叩く**。`except FetchRefused: raise` を先に置くか、
      `try` の外で status を判定する。`Retry-After` があれば例外に載せる(上限 6 時間)。
      **`_robot_allows`(`fetch.py:118-131`)にも同じ規則を適用する** —
      現状は非 200 と例外をすべて `rp = None  # no robots -> allow` に潰すので、
      **robots.txt が 429 を返しても拒否として扱われず、クールダウンも書かれず、
      本取得へ進んでしまう**。`_fetch_with_backoff` だけを直すとこの経路が素通りし、
      T019 / T036a / T036b は全部緑のまま **SC-009 が破れる**
- [x] T022a [US5] **robots.txt を頻度制限に乗せる継ぎ目を `scrape` に作る**
      (`scrape/src/horseracing_scrape/fetch.py`):
      `_robot_allows`(`:118-131`)は `self._client.get(f"{domain}/robots.txt")` を
      **`_rate_limit` も `_fetch_with_backoff` も通さずに**直接呼ぶので、
      `live/chaos_politeness.py` の予約は**本取得しか消費できない**。
      1 捕捉で同一ホストへ 2 本飛び、その間隔が制限されないまま残る。
      → `HttpFetcher` に**注入可能な事前フック**(例 `pre_request(url)`)を足し、
      robots 取得も本取得も**同じフックを通す**。既定は no-op なので
      既存の scrape 利用者の挙動は変わらない(T021a がそれを assert する)。
      **この継ぎ目が無いと T020 の robots アサーションは実装不能**で、
      FR-017 は取得元へのリクエストの半分しか覆わない
- [x] T023 [US5] `scrape` の既存テストを**全件**回して回帰が無いことを確認する(T003 の記録と照合)。
      `FetchRefused` で挙動が変わる唯一の既存パッケージなので、緑を確認してから次へ進む
- [x] T024 [US5] 予約 → 取得の 2 相を実装する
      (`live/src/horseracing_live/chaos_politeness.py`):
      **抑制のキーは実際の取得 URL から導出する**(スキーム + ホスト。
      `scrape/fetch.py::_domain` と同一規則を使い独自に組み立てない) —
      別のキーが計算されると**全テスト緑のままプロセス跨ぎ制限が無効**になる。
      **予約は「1 捕捉」ではなく「1 リクエスト」に対して行う** —
      `HttpFetcher.get` は本取得の前に robots.txt を別途送り、その送信は
      `_rate_limit` を**通らない**(`fetch.py:111`)ので、
      1 捕捉あたり同一ホストへ 2 本が無制限に飛ぶ。**robots 取得も予約を消費させる**。
      短いトランザクションで **`INSERT ... ON CONFLICT DO NOTHING`(初回作成の競合を潰す —
      `FOR UPDATE` は不在行をロックしないので 2 プロセスが同時に INSERT すると片方が落ちる)**
      → `SELECT ... FOR UPDATE` → `blocked_until` 判定 → 待ち時間を計算 →
      **上限(既定 3 秒)を超えるなら `throttle_backlog` で見送る**(FR-017b)→
      `next_allowed_at` に**未来時刻を書いて予約** → COMMIT →
      ローカルで待つ → **抑制状態を読み直す**(FR-017a — 待っている間に別プロセスが
      拒否を受けて抑制を書いた可能性がある)→ 取得。
      **抑制状態の読み書きは捕捉トランザクションとは別の短命なセッションで行い、
      独立にコミットする**。捕捉トランザクションは per-race 排他のため取得中も
      開いたままなので、そこに抑制を書くと `FetchRefused` の巻き戻しで消える。
      `FetchRefused` を受けたら別セッションで
      `blocked_until = now + cooldown`(429=30 分 / 403=60 分・`Retry-After` 優先/上限 6 時間)を
      書いてコミットし、再試行しない。
      呼び出し側には**例外を投げず** `skipped/source_cooldown` を返す
- [x] T025 [US5] **単調時計による単一の期限**を実装する
      (`live/src/horseracing_live/chaos_politeness.py`):
      `deadline = monotonic() + capture_deadline_s` を作り、
      頻度制限の待機・robots.txt の確認・取得・解析・保存の**各段階の前に残り時間を確認**し、
      尽きていれば `skipped/deadline_exceeded` で打ち切る。
      **捕捉経路は `max_retries=1`(試行 1 回)**。
      **`0` にしてはならない** — `_fetch_with_backoff` は `for attempt in range(self.max_retries)`
      (`fetch.py:148`)なので `0` は「試行 0 回」= HTTP を一度も叩かず失敗する。
      スパイ fetcher のテストも遅いサーバのテストも**緑のまま全捕捉が落ちる**。
      (捕捉は補助機能であり、失敗しても次の予測実行が拾う。
      `fetch-politeness` §3 の「予算内でリトライ」は scrape の一般利用者向けの記述であり
      捕捉経路には適用しない。)
      **契機 → 期限の写像を純関数 `deadline_for(trigger)` として `chaos_politeness` に置く**
      (`predict_*` = 10 / `daily_operational`・`explicit_command` = 30)。
      CLI も ops もこれを参照する。**CLI 層だけに写像を置いてはならない** —
      `capture_chaos` を CLI 以外から呼ぶ経路が主たる観測源に予測用の 10 秒を掛けてしまう。
      **T037 は argparse の既定を `deadline_for` に通すだけ**(写像を持たない)。
      ops が argv で与える実効値と `deadline_for("predict_manual")` の一致を静的テストで固定する。
      **既定の期限は契機で分ける**: `predict_manual` / `predict_auto` = 10 秒、
      **`daily_operational` / `explicit_command` = 30 秒**(値の正本は fetch-politeness §4)。
      日次は外側の打ち切りも遅延制約も無く、FR-012 が「主たる観測源」と定めた経路なので、
      予測の体感を根拠にした 10 秒を掛けると**主ソースが静かに欠測**しうる。
      **捕捉専用の fetcher ファクトリ `make_capture_fetcher` を
      `live/src/horseracing_live/chaos_politeness.py` に新設する**
      (置き場を決めておかないと、テストが新ファクトリを直接 import して緑になる一方で
      本番経路は旧 `_make_fetcher` を使ったまま、という状態が起こる)。
      **`live/cli.py:123,135` の `from horseracing_scrape.cli import _make_fetcher` /
      `_make_fetcher(1.0, None)` を新ファクトリ呼び出しに差し替える**。
      **段階別 timeout は `httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)` で固定する**
      (`contracts/fetch-politeness.md` §4 の値。
      「内側の期限以下」とだけ書くと、どんな組でも通ってしまう)。
      `scrape/cli.py::_make_fetcher` は変えない** — あれは entries / odds / results / laps の
      全 ingest CLI が共有している(`:71,162,200,229,242`)ので、timeout と `max_retries` を
      書き換えると**日次取込が静かに壊れる**(scrape のテストはネットワークを叩かないので
      T023 の回帰確認でも検出できない)。
      **CLI フラグの追加は T037 に一本化する**(同一ファイル・同一フラグを 2 タスクに分けない)

**Checkpoint**: 取得元への礼儀の**部品**が揃った。
`capture_chaos` への結線は T036、結線後の振る舞い検証は T036a/T036b で行う
(この時点ではまだ UI クリックに取得を任せられない)

---

## Phase 5: User Story 1 - 予測ボタンで捕捉も走る (Priority: P1) 🎯 MVP

**Goal**: 利用者が既に行っている予測実行に相乗りして凍結観測を積む。
別コマンドを覚えなくてよくする。

**Independent Test**: 発走前・未確定・未捕捉のレースで予測を実行 → 予測が生成され、
同じレースに有効な凍結観測が 1 件できる(SC-001)。

**実行順序(ID 順ではない)**:
T026 → T026a/T027/T027a/T027b/T028/T028a/T029/T030/T031(テスト)
→ T032 → T032a → T033 → T034 → T034a → T034b → T028b → T035 → T036
→ T037 → T037b → **T018a**(実測は CLI 引数が入ってから)→(必要なら T025b → T025c)
→ T036d → T021b → T036a → T036b
→ T038 → T038a → T038b → **T038d**(SC-011 の実測)→ **T036c**(SC-009 の end-to-end・ops 結線の後でないと通らない)
→ T038c → T047a → T047b → **T047c + T052a(同一コミット)**
  (`UNAVAILABLE_LABEL` は `Record<Reason, string>` なので、型だけでもラベルだけでも tsc が赤)

### Tests for User Story 1

- [x] T026 [US1] **取得回数を数えるスパイ fetcher** を用意する
      (`live/tests/conftest.py` にフィクスチャ追加)。
      以降の判定テストはすべて `spy.calls` を assert する。
      **`[P]` ではない** — T026a/T027/T027a/T028/T028a/T029 がこれに依存する
- [x] T026a [P] [US1] **正常系のテストを書く**
      (`live/tests/integration/test_capture_happy_path.py`):
      発走前・未確定・未捕捉の適格レースで捕捉すると
      `outcome=captured` かつ **`spy.calls == 1`**・`active` snapshot が**ちょうど 1 行**・
      readout が 1 行・`capture_strength == "confirmatory"`(SC-001)。
      **`weak` にはならない**(FR-004 が post_time 不明の保存を禁じたため・SC-010 の例外 7。
      skip ケースに置くと snapshot が作られず空虚に真になるので、正常系で assert する)。
      **MVP の主張そのものなので専用テストを置く**(他テストの副作用で暗黙に通る状態にしない)
- [x] T027 [P] [US1] 判定順序のテストを書く
      (`live/tests/integration/test_capture_eligibility_order.py`):
      発走済み → `skipped/post_time_elapsed` かつ **fetch 0 回** /
      結果確定済み → `skipped/result_settled` かつ **0 回** /
      post_time 不明 → `skipped/post_time_unknown` かつ **0 回** /
      **残り時間 < 運用下限 → `skipped/min_seconds_to_post` かつ 0 回**・
      **started が 4 頭未満 → `skipped/field_too_small` かつ 0 回**(T032a で再分類)・
      **started が 0 頭 → `skipped/no_started_horses` かつ 0 回**(T032a で再分類)
      (どちらも 084 では取得後に判定していたので、前倒しの検証が要る)/
      **運用下限の既定は 0**(未指定なら残り時間で見送らない)/

      捕捉済み(active)→ `skipped/already_captured` かつ **0 回** /
      **捕捉済み(void)→ `skipped/already_captured` かつ 0 回**(無効化後も撮り直さない)
      (SC-002・SC-004)
- [x] T027b [P] [US1] **自動追随は窓外を見送るテストを書く**(同ファイル):
      `--trigger predict_auto` かつ窓外 → `skipped/outside_primary_horizon` かつ **取得 0 回**。
      同じ状況で `predict_manual` / `explicit_command` / `daily_operational` なら
      **捕捉される**(FR-001a と FR-001b の非対称を直接検証する)。
      **捕捉済みかつ `predict_auto` かつ窓外**のレースでは
      `already_captured` ではなく **`outside_primary_horizon`** が返り、
      **排他制御を取りに行かない**(判定順序が契約どおり — 窓判定が行の存在判定より前)。
      この検証が無いと、窓判定を抑制状態の位置まで下げても T027b が緑のまま通る
      理由: 自動追随には画面で待つ利用者がいないので窓外を保存する利得が無い一方、
      「1 レース生涯 1 観測」により**そのレースの唯一の枠を消費して確認適格を永久に奪う**
- [x] T027a [P] [US1] **窓外でも捕捉されるテストを書く**(同ファイル):
      `seconds_to_post` が主 horizon の外(例 T−5 時間で窓が T−10分..T−1時間 の fixture)でも
      `outcome=captured` かつ **`spy.calls == 1`**・`confirmation_eligible == false`。
      **窓外は見送りの理由にならない**(FR-001a)ことを直接検証する
- [x] T028 [P] [US1] **1 レース 1 観測**のテストを書く(FR-002 / FR-008)
      (`live/tests/integration/test_capture_single_observation.py`):
      出走取消で構成が変わると行が `void/field_changed` になり **取得 0 回・行数 1 のまま**
      (SC-005) / **2 回目の取消でも追加の取得も行も一切ない** /
      **A→B→A の構成戻り**: 追加の取得も行も一切ない・**B で失効した観測は A に戻っても
      復活せず、表示にも確認コホートにも入らない**(FR-002a1 の単調失効。
      行数と取得回数だけを見ると復活バグを見逃す) /
      オッズだけ動いても何も起きない(FR-002・取得 0 回) /
      **窓外で捕捉した後に窓内で再実行しても `already_captured` で取得 0 回・
      オッズ入りの行は 1 行のまま**(昇格は行わない) /
      **どのシナリオでも 1 レースのオッズ入り行数が生涯 1 を超えない**(INV-2・憲法 V)
- [x] T028a [P] [US1] **取得中の構成変化**のテストを書く(同ファイル):
      取得を一時停止させ、別セッションで `entry_status` を変えてから再開すると
      `skipped/field_changed_during_fetch` になり**保存されない**(FR-003a)。
      排他制御は同時捕捉を守るだけで出走表の更新は守らない
- [x] T029 [P] [US1] 同時実行のテストを書く
      (`live/tests/integration/test_capture_concurrency.py`):
      同一レースに 2 セッションから同時に捕捉すると
      **取得 1 回・`active` 1 行**に収束する(SC-008)
- [x] T030 [P] [US1] ops 結線のテストを書く(`ops/tests/unit/test_predict_capture.py`):
      捕捉が予測より**先**に呼ばれる(呼び出し順スパイ)・
      捕捉が例外 / 非 0 終了でも予測が実行され job が **SUCCEEDED**(SC-003・INV-6)
- [x] T031 [P] [US1] 境界テストが維持されていることを確認する
      (`ops/tests/integration/test_boundary.py` は**変更しない**)

### Implementation for User Story 1

- [x] T032 [US1] 判定を取得の前に移す(`live/src/horseracing_live/chaos_capture.py`):
      `capture_chaos` の順序を
      race 妥当性 → 出走表 → 結果未確定 → **post_time 既知** → **未発走** →
      **残り時間 >= 運用下限** → **主 horizon の artifact をロード**(窓の判定に要る。
      読めなければ `rejected/artifact_unavailable` で取得しない)→
      **自動追随なら窓内**(FR-001b)→ **行が存在しない** →
      **頭数ゲート**(`no_started_horses` / `field_too_small` — 現状は `build_frozen_field` の
      中(`:195,198`)にあり**取得後**に判定している。DB の started 集合だけで判定できるので
      前倒しする。移さないと契約 §4 の「外部取得なし」が嘘になる。
      **status は `skipped`**(T032a — 少頭数は正常な見送り)→
      `pg_try_advisory_xact_lock` → 抑制状態 → 取得 →
      **結果未確定の再確認 → 発走時刻の再確認 → 運用下限の再判定 → 出走構成の再確認** → 保存
      に是正する(**順序の正本は `contracts/capture-eligibility.md` §1**)。`acquire_fresh_capture` から `post_time` ゲート(`:297`)と
      **`min_seconds_to_post` ゲート(`:302`)の両方**を前段へ引き上げる(C1 —
      後者を残すと `--min-seconds-to-post` を使う日次運用が
      「取得してから捨てる」ままになり FR-001/SC-004 が日次経路で破れる)。
      **運用下限の判定も取得後に保険として残す**(取得に数秒かかるので、
      保存時点で残り時間が下限を割った観測が保存されうる)。
      取得後の再確認は**次の 3 つ**(4 つ目の出走構成の再確認は T034a)を保険として残す —
      **結果未確定の再確認**(`:281` の `pending_after`。
      **専用の理由 `result_settled_during_fetch` で返す** — 取得前の `result_settled` と
      同じ理由にすると「取得なし・適格」と分類され、実際は取得した異常が正常に見える。
      これが `decide_capture_strength` に入り
      `confirmatory` を決めるので、落とすと `capture_strength` の意味が静かに変わり
      SC-010 例外 7 の論拠も崩れる)と、**発走時刻の再確認**(手順 13)と
      **運用下限の再判定**(手順 14。取得に数秒かかるので割りうる)
- [x] T032a [US1] `result_settled` / `post_time_elapsed` /
      **`no_started_horses` / `field_too_small`** を `skipped` に再分類する(同ファイル):
      084 は `ChaosCaptureRejected` の既定 `status="rejected"` で送出していた(`:128`)。
      **日次 CLI の要約行の内訳が変わる**ので、
      `live/tests/unit/test_chaos_capture_guards.py` と
      `live/tests/integration/test_chaos_capture_db.py` の既存アサーションを更新し、
      これが **SC-010 に対する意図した例外**であることをテストのコメントに残す
- [x] T033 [US1] 排他制御を取得の前に移す(FR-003)(同ファイル):
      `pg_advisory_xact_lock`(現状は取得の後・436 行)を
      `pg_try_advisory_xact_lock` に変え、**取得の前**に取る。
      取れなければ `skipped/concurrent_capture` を返す(待ち行列を作らない)。
      取得と書き込みを同じトランザクションで囲む
- [x] T034 [US1] **T006e の残余を仕上げる**(FR-002 / FR-002a)(同ファイル):
      **受け持ちの境界**: T006e = 追記分岐の削除と void の UPDATE 化(捕捉コア)。
      T034 = `_void_reason` の置換・取得前判定を行の存在だけにする・CLI のコミット規則。
      **追記の削除と void の UPDATE 化そのものは T006e(Phase 2)で完了している** —
      ここで扱うのは残りの 3 点だけ:
      取得前の判定は**行の存在だけ**(active でも void でも `already_captured`)。
      出走構成の比較は**捕捉の可否ではなく既存 active 行の無効化**に使う —
      `field` の `(horse_id, horse_number)` 集合が `race_horses`(started)と違えば
      その行を `status='void'` / `void_reason='field_changed'` に **UPDATE** する
      (新しい行は作らない・取得もしない)。
      **`_void_reason`(`chaos_capture.py:375-378`)は `late_scratch` / `recaptured` を返す関数で、
      086 ではどちらも生成されない** → `field_changed` 固定の述語に置き換える(または削除する)。
      `void_reason` に CHECK 制約は無いので DB 側の追加作業は不要。
      **CLI のコミット規則も直す**(`live/cli.py:163-169`)— 現状は
      `if report.captured: commit() else: rollback()` なので、
      **無効化だけを行った見送りは本番で必ず巻き戻る**。
      関数を直接呼ぶテスト(T028)は緑になるので気づけない。
      「捕捉した」または「無効化した」のいずれかで commit する規則に変え、
      **CLI 経由の integration テストで固定する**
      084 の追記型の差し替え(`session.add(ChaosSnapshot(...))` at `:452`)を削除する
- [x] T028b [P] [US1] **取得後の再確認 3 本のテストを書く**
      (`live/tests/integration/test_capture_post_fetch_rechecks.py`):
      取得を止めたまま状態を動かし、**保存されないこと**と理由を検証する —
      結果が確定 → `skipped/result_settled_during_fetch` /
      発走時刻を過ぎる → `skipped/post_time_elapsed_during_fetch` /
      運用下限を割る → `skipped/min_seconds_to_post_during_fetch`。
      いずれも**取得前の同名判定とは別の理由**であること
      (同じ理由にまとめると「取得なし・適格」に分類され、
      外部取得を 1 本無駄にした異常が運用画面で正常に見える)。
      **保険の分岐は黙って失敗するので、テストが無いと存在しないのと同じ**
- [x] T034a [US1] 取得後の構成再確認を実装する(同ファイル・FR-003a):
      保存の直前に `race_horses`(started)を読み直し、
      凍結しようとしている構成と一致しなければ `skipped/field_changed_during_fetch` で
      **保存せずに終わる**。取得には数秒かかるので、その間の出走取消で古い構成を凍結しうる
- [x] T034b [US1] `recaptured` / `late_scratch` を前提とした既存テストを更新する:
      086 は再捕捉そのものを行わないので、どちらの `void_reason` も生成されない。
      `live/tests/integration/test_chaos_capture_db.py` の **2 箇所**を書き換える —
      `:241`(`void_reason == "recaptured"`)と、
      **`:164-201` の `test_late_scratch_voids_old_snapshot_and_appends_one_new_active_row`**
      (テスト名と本体の両方が「旧行を void して**新しい active 行を追記する**」= 撤回した挙動を
      固定しており、T034 実装後に確実に赤くなる)。
      前者は「同一構成では行が増えない」、後者はテスト名ごと
      「取消では行が増えず `void/field_changed` になる」に改める。あわせて
      `api/tests/integration/test_race_chaos_api.py:87` のフィクスチャを
      `field_changed` に変える(表示側は void_reason の値を解釈しないので影響なし)
- [x] T035 [US1] **出所を呼び出し側から配線する**(実装本体は T006c で完了済み):
      **受け持ちの境界**: T006c = `capture_chaos` の引数追加と保存(捕捉コア)。
      T035 = live CLI と ops の各呼び出し元が正しい値を渡す配線。
      live CLI と ops の各経路が `capture_chaos(..., capture_trigger=…,
      capture_policy_version=…)` を正しい値で呼ぶようにする。
      新規捕捉は `capture_policy_version="capture_policy_v1"` を書く(T043 で assert する)。
      **Phase 7 に置いてはならない** — 0013 で両列は NOT NULL なので、
      書かないままでは Phase 5 の捕捉が**すべて失敗し** SC-001 が成立しない
- [x] T036 [US1] 抑制状態の判定を組み込む(同ファイル):
      取得の直前に T024 の予約を通し、抑制中なら `skipped/source_cooldown` を返す。
      取得中に `FetchRefused` が出たら**捕まえて** `skipped/source_cooldown` を返す
      (例外を CLI まで漏らさない・U4)。
      **403/429 以外の取得失敗**(非 200・接続断・解析失敗)も捕まえて
      `failed/fetch_failed` を返す。
      **`RobotsDisallowed`(`scrape/fetch.py:48`)だけは別扱い**で
      `skipped/robots_disallowed` を返す(取得元の障害ではなく方針。
      `fetch_failed` に混ぜると運用画面で区別できない)。
      (同上・例外を漏らすと ops が JSON を読めず
      `unknown`=外側打ち切りに化ける)
- [x] T036a [US1] **抑制が捕捉の巻き戻しを生き延びる**テストを書く
      (`live/tests/integration/test_chaos_politeness.py`):
      捕捉トランザクションを rollback しても `blocked_until` が DB に残っている。
      これが無いと SC-009 が**黙って**失敗する(後続の予測が同じ 429 を叩き直す)。
      **T036 の結線後でないと検証できない**ので Phase 5 に置く
- [x] T036c [US1] **SC-009 を end-to-end で検証する**
      (`ops/tests/integration/test_predict_capture_cooldown.py`):
      予測 1 回目で取得元が 429 → 予測 2 回目で **HTTP が 0 回**。
      T020(モジュール単体)/ T036a(巻き戻し耐性)/ T036b(CLI 終了コード)に分割された
      検証は、**予測経路の結線が壊れていても全部緑になる**。
      SC-009 の文言は「拒否の後、**後続の予測実行が**再試行しない」なので、
      予測経路を通した 1 本が要る
- [x] T036b [US1] `FetchRefused` が CLI まで漏れないテストを書く(同ファイル・T020 / T036a と同一ファイルなので直列):
      拒否応答で `capture_chaos` が `skipped/source_cooldown` を返し、
      CLI の終了コードが 0 のままである(U4・job-observability §3)
- [x] T025b [US1] **[条件付き — 発動せず]** 実測で予算は全て PASS(内側 37 倍・外側 12 倍・SC-011 13 倍の余裕)。 **T000a または T018a** の実測が内側期限に収まらなかった場合、
      あるいは**起動込みの実時間が SC-011 の 20 秒を超えた**場合に実行する:
      **値の正本は `contracts/fetch-politeness.md` §4**(コード上の配置が 2 箇所ある
      = 予測経路の `ops._CAPTURE_DEADLINE_S` と live CLI の契機別既定)。
      §4 を改訂したら、参照している文書と定数を**すべて**同時に直す:
      `contracts/job-observability.md` §2・§3 / `plan.md` Technical Context /
      `research.md` D7 / T025 / T037 / **T043(`explicit_command` の 30 秒)** / **T038**(`_CAPTURE_TIMEOUT_S` / `_CAPTURE_DEADLINE_S` の直書き)/
      **T021**(「= 10 秒」)/ **T018a**(「10 秒 / 18 秒の予算」)/
      **spec の SC-011**(外側を縛る 20 秒)/ **spec.md の「最大 18 秒」** /
      **plan.md の codex 採否表(25/45 を記録している行)** /
      **quickstart.md(§1 の idle-timeout 注記と §8b の期待出力)** / **T008a の同期チェックリスト** / **T048 / T049 / T050 の「最大 18 秒間」** の本文。
      **列挙に頼らず `specs/086-capture-on-predict/` と `docs/` を
      `18 秒` / `10 秒` / `30 秒` で grep して漏れを確認する**
      (列挙は必ず古くなる — 実際この一覧は 1 度取りこぼした)。
      外側 18 秒にも収まらない場合は **T025c** に進む
- [x] T025c [US1] **[条件付き — 発動せず]** T025b が不要なので分岐の判断も不要。 予算の引き上げで収まらない場合の分岐を決める:
      (b) 捕捉を予測の**後**に回す = **FR-013(MUST)の仕様変更** /
      (c) 予測経路の捕捉をやめて日次のみに戻す = feature の縮小。
      **どちらも codex 再レビューを経る**(憲法の品質ゲート)。
      決めた結果を spec の FR-013 / SC-011 と plan の Complexity Tracking に反映する。
      **この分岐に担当タスクが無いと、実測が失敗したときに次の一手が定義されない**
- [x] T036d [US1] **期限を `capture_chaos` に配線する**(FR-018)
      (`live/src/horseracing_live/chaos_capture.py`):
      **`capture_chaos(..., *, deadline)` をキーワード必須で受ける**
      (`capture_trigger` と同じ規律 — 既定値を置くと渡し忘れた経路が無期限になる)。
      **単調時計の単一期限**として保持し、**取得の前 / 解析の前 / 保存の前**に
      残り時間を確認して、尽きていたら `skipped/deadline_exceeded` を返す。
      **期限は「取得」だけの話ではない** — FR-018 は
      「頻度制限の待機・規約確認・取得・**解析・保存**」を 1 本の期限で縛る MUST であり、
      解析と保存は `chaos_politeness.py` ではなくこのファイルにある。
      `deadline_exceeded` の送出主体もここ(`ChaosCaptureRejected` の呼び出し箇所は
      `capture-eligibility` §4 が網羅している)。
      **CLI と ops の両経路が実際に値を渡していることを T043 / T038c で assert する**
      (T025 の写像を作っただけでは、誰も渡さなければ何も起きない)
- [x] T037 [US1] capture-chaos CLI に **4 引数**を追加する:
      `--trigger` / `--json` / `--capture-deadline-seconds` / `--allow-outside-horizon`
      (最後の 1 つは `--date` 経路専用。**`--race-id` と併用したら型付きエラー**で拒否する —
      黙って無視すると、操作者は窓外でも撮れたと誤解する)
      (`live/src/horseracing_live/cli.py`。`--race-id` は 284 行に既存・`--date` と排他):
      `--trigger {daily_operational,predict_manual,predict_auto,explicit_command}`、
      `--json`(**`--race-id` 専用**の 1 行 JSON。
      **自己計測の経過秒 `elapsed_s` を含める**(T021b が期限の検証に使う。
      subprocess の実時間には `uv run` の起動が混じるので測れない)。`--date` との併用は型付きエラー —
      日次は 36 レースを回すので 1 行 JSON という契約が成立しない)、
      `--capture-deadline-seconds`。
      **その日の最も遅い発走までの残り時間が窓の上限を超えるなら既定で拒否する**(FR-001b1・
      コマンド開始時刻を基準に**日単位で 1 回**判定する。レース単位にすると
      同じ日の中で部分的に枠を焼く)。
      `--allow-outside-horizon`(既定 off)を明示したときだけ実行する。
      この引数は job-observability §3 の CLI 契約表に**記載済み**であることを確認する(憲法 VI)。
      1 レース生涯 1 観測なので、2 日前に流すとその開催日の全レースが窓外で枠を使い切り、
      **主たる観測源の 1 開催日ぶんが確認コホートから恒久的に脱落する**。
      **既定は契機ごとに異なる**: `predict_manual` / `predict_auto` は **10 秒**、
      `daily_operational` / `explicit_command` は **30 秒**
      (フラグではなく**契機**に紐づける — 操作者が 1 レースを手で流す `explicit_command` にも
      外側の打ち切りと遅延制約が無いので、UI 由来の 10 秒を掛ける理由がない)。
      いずれも**1 レースあたり**の期限である(コマンド全体ではない — 日次は 36 レースを
      順に処理するので、全体に掛けると主ソースが静かに欠測する)。
      日次は外側の打ち切りも遅延制約も無く FR-012 の主たる観測源なので、
      予測の体感を根拠にした 10 秒を掛けない。`--trigger` と同じく単一の既定値で済ませない。
      **`--trigger` の既定は経路ごとに異なる**(`--date`→`daily_operational` /
      `--race-id`→`explicit_command`)。argparse の単一の既定値で済ませると
      片方が静かに誤ラベルになるので、**両経路を `--trigger` 無しで実行して
      保存値を確認するテスト**を T043 に含める。
      **人間向けの書式は `--json` 無しでは変えない** —
      ただし**要約行の見送り内訳だけは例外**(T037b)。
      内訳が変わるのは SC-010 の**意図した例外 1(rejected→skipped)と 8(見送りの 2 分類)**の範囲。
      終了コードは捕捉の成否を表さない(見送りも 0)
- [x] T037b [US1] **日次 CLI の要約行で見送りを 2 分類する**(FR-022 の日次版・SC-010 の例外 8)
      (`live/src/horseracing_live/cli.py` + `live/tests/integration/test_daily_summary.py`):
      現状は `skipped=36` の 1 本なので、**取得元のクールダウンが立って主たる観測源が
      1 件も取れていない日**と、**全レースが発走済みで見送った正常な日**が区別できない。
      **`skipped` 行だけを `表示区分` 列で 2 分割する** —
      `skipped_eligible=` / `skipped_unfetchable=`。
      `fetch_failed` は `status=failed` なので**この 2 分割の対象外**で、
      既存の `failed=` に入る。
      **分類は `contracts/capture-eligibility.md` §4 の `表示区分` 列を正本とする**。
      **表に無い理由は `skipped_unfetchable` に寄せる**(既定は「取得不可」)—
      逆にすると新しい失敗理由が正常な見送りとして埋もれる。**この既定をテストで固定する**
      (admin と同じ表を読む — 2 箇所で別々に列挙すると必ずずれる)。
      `auto_capture_disabled` / `outer_timeout` は日次経路では発生しない
      (どちらも実行層が付ける印であり、日次は subprocess で包まれていない)。
      **これは SC-010 の「既存の人間向け出力は不変」に対する意図した例外 8** なので、
      T057 のゴールデン差分でマスクする対象に入っていることを確認する

- [x] T018a [US1] **live CLI 単体の所要時間を実測し、内側 10 秒の予算を決着させる**
      (**SC-011(予測ジョブから見た 20 秒)の最終根拠は T038d** — こちらは内側の判断材料)
      (`docs/plan/086-capture-timing.md` に記録する):
      **T000a(Phase 1)は往復 1 本の下限**を測っただけで、
      判定・排他・保存・subprocess 起動を含む実際の所要時間は未検証のまま。
      **CLI 引数(T037)が入った後**に、実レース 1 件へ
      `live capture-chaos --race-id … --capture-deadline-seconds 60 --json` を
      **5 回**流し、中央値と最大値を記録する。
      **期限は意図的に大きく取る** — 10 秒を渡すと期限が発火して実時間が 10 秒付近で
      頭打ちになり `deadline_exceeded` が返るだけで、
      「10 秒には収まらないが 18 秒には収まる」のか「18 秒にも収まらない」のかを
      **区別できない**(判断材料そのものが取れない)。
      分布を取ってから 10 / 20 を当てはめて判定し、判定後に既定へ戻す。
      **判定は最大値で行う**(利用者が待つ経路なので、たまに超えるのは超えると同じ)。
      **`uv run` の起動オーバヘッドを単体で計測して別項目に記録する** —
      外側 18 秒は `communicate()` の上限であって起動・`killpg`・`wait` を含まない。
      SC-011 の「予測に加わる時間 20 秒以内」は**起動を含めた実時間**なので、
      起動オーバヘッドが 2 秒を超えるなら外側をさらに下げる。
      分岐:
      (a) 内側 10 秒に収まらないが外側 18 秒には収まる → **T025b** を実行して値を上げる。
      (b) 外側 18 秒にも収まらない → **捕捉を予測の後に回す**しかないが、
          これは **FR-013(MUST)の仕様変更**なので codex 再レビューを経る。
      (c) 取得元が恒常的に遅い → 予測経路の捕捉を諦め日次のみに戻す(feature の縮小)。
      **測れなかった場合**(発走前のレースが無い / 取得元に到達できない)は
      **(a) を選ばず、既定値のまま進める**。
      `docs/plan/086-capture-timing.md` に**「未検証の前提」として明記**し、
      Phase 5 の checkpoint に「SC-011 は未実測」と書き残す
      (根拠のない引き上げは FR-013 の性能目標を無効化するので、
      測れないことを理由に緩めない)
- [x] T021b [US5] **実 CLI 経路で期限・排他・子孫プロセスを end-to-end で検証する**
      (`live/tests/integration/test_capture_deadline_e2e.py`。
      T021 は fetcher 単体までで、結線後の確認は意図的にここへ分けてある):
      遅い HTTP サーバ(**robots.txt も遅い**)に対して**実 CLI を subprocess で**起動し、
      1. **CLI が `--json` に載せた自己計測の経過秒**が
         **内側の期限(`deadline_for(trigger)`)以内**である。
         **subprocess の実時間で測ってはならない** — `uv run` の起動は
         内側にも外側にも含まれない(`fetch-politeness` §4)ので、
         期限を使い切る**正しい実装が起動 1〜2 秒で赤くなる**。
         subprocess の実時間は**外側の打ち切りより前**であることの確認に使う
         (この 2 つを取り違えると、期限を不当に切り下げる圧力になる)・
      2. 打ち切り後に **advisory lock が解放**されている
         (別セッションから `pg_try_advisory_xact_lock` が取れる)・
      3. **子孫プロセスが 1 つも生き残っていない**(`start_new_session` + `killpg`・FR-018a)・
      4. 打ち切りの**後から行が増えない**(数秒待ってから件数を再確認する)。
      3 と 4 が無いと、`uv` だけを殺して捕捉プロセスが生き残り、
      記録が `unknown` になった後で保存を完了させる欠陥が**全テスト緑のまま**残る
      (`contracts/fetch-politeness.md` §6 が要求している検証)
- [x] T038 [US1] ops に捕捉を結線する(`ops/src/horseracing_ops/runner.py`):
      `_live_capture_chaos(race_id, *, trigger, on_launched=None)` を追加
      (**`on_launched` は `Popen` の直後・`communicate()` の前に呼ぶ継ぎ目**。
      T041 がここで `state="launched"` を別セッションで独立コミットする。
      継ぎ目が無いと呼び出し側が `Popen` の前にしか書けず、3 状態が 2 状態に潰れて
      FR-015 の「取得前 / 取得後」の区別が失われる — しかも T041 のテストは緑のまま通る)。
      (`uv run --project live` の
      既存 subprocess パターン・`_CAPTURE_TIMEOUT_S = 18`・`_CAPTURE_DEADLINE_S = 10` を
      **argv に必ず載せる**・`cwd=live`・`VIRTUAL_ENV` を落とす・
      **`start_new_session=True`** で新しいプロセスグループにし、
      打ち切り時は `os.killpg` で**グループ全体**を終了させる)。
      `subprocess.run` の timeout は直接の子(`uv`)しか殺さず、
      捕捉プロセスが生き残って排他を握り続け、記録が `unknown` になった後で
      保存を完了させうる(FR-018a)。
      `run_predict` の中で `_serving_predict` の**直前**に呼ぶ。
      **自動追随からの捕捉を止める運用スイッチを置く**(FR-001c):
      環境変数 `OPS_CAPTURE_ON_AUTO_PREDICT`(`0` / `false` で無効・**既定は有効**)。
      無効時は subprocess を起動せず `summary.capture = {state:"done",
      outcome:"skipped", reason:"auto_capture_disabled"}` を書く(黙って何もしない実装にしない)。
      人の操作なしに外部取得が走る唯一の経路なので、取得元との関係が悪化したときに
      仕様変更なしで止められるようにする。
      捕捉の結果は `job.status` の判定に**一切影響させない**(FR-014)。
      **呼び出しは広い `except Exception` で囲む** — `uv` 不在の `FileNotFoundError` 等が
      漏れると `worker.py` の `except Exception` が rollback + 再キューして
      **予測そのものが走らなくなる**。捕まえたら **`{state:"done", outcome:"failed", reason:"launch_failed"}`** を書いて続行する。
      **`state` を落とさない** — `JobCaptureRow.state` は必須の閉じた `Literal` なので、
      この経路の行だけが Phase 8 で `GET /jobs` を 500 にする
- [x] T038a [US1] **予測ジョブの由来を持たせる** — 呼び出し元は **3 箇所ある**
      (`ops/src/horseracing_ops/enqueue.py` 定義 +
      **`routers/predict.py:40`(UI ボタンの POST = 唯一の真の `manual_ui` 源)** +
      `runner.py:153`(取込後の自動追随))。
      既存テストの呼び出しも更新する
      (`ops/tests/integration/test_predict_dedup.py` **5 箇所** /
      `test_predict_flow.py` **5 箇所**)。
      **router を落とすと UI クリックが `predict_auto` に化けるか、
      キーワード必須引数で 500 になる**:
      `enqueue_predict(session, race_id, *, origin)` に由来を足す
      (`manual_ui` / `auto_after_refresh`)。`run_one` の自動追随は後者。
      **単一レースの「データ更新」ボタン(`POST /races/{id}/refresh` → `run_one`)も
      利用者が選んだレースである** — これを `auto_after_refresh` = 中立に分類すると
      FR-011 / SC-007 の選択バイアス推定が壊れ、しかも FR-001b で窓外だと取得すらされず
      「利用者が操作したのに荒れ度が出ない」ことになる。
      → **`enqueue_race` にも由来を持たせる**(`ops/enqueue.py:48`)。
      **`origin` はキーワード必須にする**(既定値を置くと、由来を渡し忘れた経路が
      静かに中立として記録され FR-009 が壊れる — `capture_trigger` と同じ規律)。
      **その代償として ops の既存テスト 9 ファイル・19 箇所が全部赤くなる**ので、
      すべて更新する: `test_predict_dedup.py` / `test_refresh_race_precompute.py` /
      `test_refresh_race_contract.py` / `test_freshness_force.py` /
      `test_worker_concurrency.py` / `test_refresh_day_flow.py` /
      `test_dedup_concurrent.py` / `test_audit_recorded.py` / `test_refresh_race_flow.py`
      (いずれも `enqueue_race(session, RID)` の位置引数呼び出し)。呼び出し元は 3 箇所:
      **`routers/refresh.py:50`(画面の単一レース更新ボタン = 唯一の真の手動源)** /
      `enqueue.py:208`(日次一括)/ `runner.py:295`(日次の fan-out)。
      由来を **`summary.refresh_origin`**(値は `manual_ui` / `daily_bulk`・
      **正本は data-model §5**)に残し、`run_one` がそれを読んで
      `enqueue_predict(..., origin=…)` に渡す。
      **`enqueue_race` も ACTIVE ジョブを再利用する**ので、`enqueue_predict` と
      **同じ昇格規則**(`QUEUED` なら手動へ昇格・`RUNNING` には相乗りしない)を適用する
      (**FR-009a** — 028 の重複排除の意味論が変わる。代償は同一レースの二重予測)。
      **実行中の auto ジョブが既に捕捉に成功していた場合**は、FR-008(先着勝ち)により
      観測は `predict_auto` のまま残る(手動側は `already_captured`)。
      これは仕様どおりであり、T038c ではこのケースを
      **「行は増えず契機は `predict_auto` のまま」**として固定する
      **`run_one` の `_has_active_prediction` ガード(`runner.py:152`)は塞がない** —
      既に有効な予測があるレースでは追随 predict が積まれず `predict_auto` の捕捉も起きないが、
      **これは受け入れる**(日次の中立な捕捉が拾う。利用者のクリックは
      `routers/predict.py` 経由なのでこのガードを通らない)。
      **`force=False` の鮮度再利用分岐は塞ぐ**(`enqueue.py:48-88`)— `fresh_seconds` 内に
      成功したジョブがあると `run_one` が走らず、**予測も捕捉も 1 度も起きない**。
      **採る側を決めてある**: 鮮度再利用のときも predict を積む
      (`force=True` にすると取込を丸ごとやり直して取得元への追加取得が増える。
      欲しいのは予測と捕捉であって再取込ではない)。
      **`run_one` の `job.summary = summary`(`runner.py:156`)は全体代入なので、
      由来も `capture` と同様にマージする**(FR-015a と同じ欠陥型 — 消えると
      滞留回収の再実行で追随 predict が `predict_auto` に化ける)。
      これを落とすと、利用者の更新クリックが一括由来のジョブに相乗りして
      **中立として記録され FR-009 / FR-011 / SC-007 が壊れる**
      (`enqueue_predict` で潰した欠陥と同型のものが兄弟経路に残る)。
      **由来は `summary.predict_origin` として永続化する**(`enqueue_predict` が書き、
      `run_predict` が読む)。`run_predict` の終了 3 分岐は `job.summary` を全体代入するので、
      **`capture` と同様に `predict_origin` も全分岐でマージする**(消えると監査で由来が追えない)。
      `run_predict` はこれを読んで `predict_manual` / `predict_auto` を `--trigger` に渡す。
      **重複排除で由来が入れ替わる問題を必ず塞ぐ**: `enqueue_predict` は進行中の
      predict ジョブがあれば**それを再利用する**(`enqueue.py:104-112`)ので、
      利用者のクリックが自動追随のジョブに相乗りすると `predict_auto` として
      記録されてしまう。**再利用先がまだ `QUEUED` なら由来を `manual_ui` へ昇格させる**
      (利用者選択は自動追随を上書きする。逆は行わない)。
      **`RUNNING` のジョブには相乗りせず、新しいジョブを積む** —
      `_ACTIVE` には `RUNNING` も含まれるが、既に走っているジョブは
      `run_predict` が**由来を読み終えて捕捉も起動(または FR-001b で見送り)済み**であり、
      `capture_trigger` は `predict_auto` のまま確定して**後から貼り替えられない**。
      昇格は**保存済みの観測を遡って再ラベルしない**(できない)。
      **代償を明記しておく**: `RUNNING` に相乗りしないと同一レースで予測が二重に走る
      (実測 55-113 秒 × 2)。追随する recommend ジョブは `enqueue_recommend` の
      in-flight dedup が吸収するので二重生成にはならない。
      これは 028/044 の重複排除の意味論を**手動クリックに限って**緩めるもので、
      選択バイアスの正しさ(FR-011)を優先した判断である。
      **`enqueue_predict` の docstring(`ops/enqueue.py:95-96` の 028 契約文)も更新する** —
      文書化された重複排除の意味論を変えるため。
      **現状は両方が一律 `summary.source="manual"`**(`enqueue.py:116`)なので、
      放置すると自動追随の捕捉まで「利用者が選んだレース」として報告され、
      SC-007 の選択バイアスの解釈が壊れる
- [x] T038b [US1] **捕捉結果をジョブ記録に着地させる**(`runner.py`):
      `_live_capture_chaos` の stdout の 1 行 JSON を parse し、
      **`state="done"`** / `outcome` / `reason` / `capture_strength` /
      `confirmation_eligible` / `seconds_to_post` / `chaos_snapshot_id` を
      `job.summary["capture"]` に入れる。
      **`state` を落とさない** — `JobCaptureRow.state` は必須なので、
      Phase 5 で書いた行が Phase 8 の `GET /jobs` で 500 になる
      (フェーズを跨ぐので Phase 5 のテストでは気づけない)。
      **`confirmation_eligible` は捕捉時点の参考値であり、確認コホートの正本ではない**
      (正本は報告時に digest スコープで導出する・FR-007。取消後や新 version 発行後に
      両者は食い違いうる)。
      **`run_predict` の終了 3 分岐(`runner.py:213 / 216 / 228`)はいずれも
      `job.summary = {...}` で全体を代入し直すので、全分岐で `capture` をマージする**
      (FR-015a — そのままだと先に書いた捕捉の結果が消える。
      API/admin の単体テストは作り物の summary で緑になるので**本番の記録だけが静かに失われる**)。
      **JSON が壊れている・空・想定外のときは `outcome="unknown"` にして予測を続行する**
- [x] T038d [US1] **予測ジョブから見た実時間を測る**(SC-011 の最終根拠):
      T038 の結線後に実 predict ジョブを 3 回流し、
      `_live_capture_chaos` の呼び出し前後の実時間(`uv run` の起動・
      `communicate`・`killpg`・`wait` を**すべて含む**)を記録する。
      **判定は最大値で行う**(T018a と同じ — 利用者が待つ経路なので、
      たまに超えるのは超えるのと同じ)。
      **T018a は live CLI 単体の実測であって、これとは別物である** —
      起動オーバヘッドを別に測って足すのは、FR-018 が期限について
      明示的に禁じた足し算の見積もりと同型。
      **20 秒を超えたら T025b(外側を下げる)に戻る**。
      **測れなかった場合**(発走前のレースが無い / 取得元に到達できない)は
      **既定値のまま進め**、`docs/plan/086-capture-timing.md` に
      「SC-011 は未実測」と明記して Phase 5 の checkpoint にも書き残す(T018a と同じ規律)。
      結果は `docs/plan/086-capture-timing.md` に追記する
- [x] T038c [US1] 着地のテストを書く(`ops/tests/unit/test_predict_capture.py` に追加):
      **`run_predict` を最後まで走らせ、コミット済みのジョブ行**に
      **`state` を含む 7 フィールド**が残ること(**`capture.state == "done"`** を明示に assert する)を
      **FAILED / SKIPPED / SUCCEEDED の 3 分岐すべて**で確認する
      (作り物の summary を読むテストにしない)・
      壊れた JSON で `outcome="unknown"` になり予測が続行する・
      **subprocess の起動自体が例外(`FileNotFoundError` 等)でも
      `state="done"` / `outcome="failed"` が残り、予測は続行する**・
      **`OPS_CAPTURE_ON_AUTO_PREDICT=0` で自動追随の捕捉が起動せず
      `reason="auto_capture_disabled"` が残る / 既定では起動する**(FR-001c の両側)・
      **`summary.predict_origin` が終了後のジョブ行に残る**(全 3 分岐)・
      UI ボタン由来が `predict_manual`・自動追随が `predict_auto` になる
      (**両方の enqueue 経路を実際に実行して**確認する)・
      **`QUEUED` の自動追随ジョブに手動クリックが相乗りしたら `predict_manual` に昇格する**・
      **画面の単一レース更新ボタン(`routers/refresh.py`)由来も `predict_manual` になる**
      (`enqueue_race` の由来が `run_one` を経て predict に伝わる)・
      **鮮度再利用の分岐でも捕捉機会が消えない**(`force=False` で最近成功した
      race ジョブがあるケース)・
      **`run_one` の終了後にコミット済みの race ジョブ行を読んで由来が残っている**・
      **`RUNNING` のジョブには相乗りせず新しいジョブが積まれる**
      (RUNNING に相乗りすると由来を貼り替えられず `predict_auto` で確定してしまう)・
      **二重に走った予測と追随 recommend が破綻しない**
      (recommend は in-flight dedup が吸収する)・
      **argv に `--capture-deadline-seconds` が載っており、その値が
      `ops` の `_CAPTURE_DEADLINE_S` と一致する**(フラグの有無だけを見ると
      値が静かにドリフトする。live 側の既定に頼ると FR-018 が黙って破れる)・
      **`ops` の定数と live 側の `predict_manual` 既定が一致する**。
      **検証方法を具体に決める**: `ops` は `live` を import できない
      (`test_boundary.py` が機械的に禁じている)ので、
      **`live/src/horseracing_live/chaos_politeness.py` をソーステキストとして読み**、
      `predict_manual` に対応する数値を取り出して比較する
      (このリポジトリの leak-guard / import-graph テストと同じ grep 型)。
      **「両方に定数がある」だけのトートロジーにしない** —
      片方を書き換えても緑のままなら、正本を宣言した意味が無い
      (`contracts/fetch-politeness.md` §4)

- [x] T047a [US1] **表示側の構成一致テストを書く**
      (`api/tests/integration/test_race_chaos_api.py` に追加):
      捕捉後に `race_horses` の `entry_status` を変えると
      **荒れ度が返らなくなる**(利用可能な観測なし)。
      snapshot が `active` のままでも抑制されること — つまり
      **無効化の記録に依存しない**ことを確認する(FR-002b・SC-005)。
      **`void(field_changed)` に落ちた後も理由が `field_changed_after_capture` のまま**であり
      `no_snapshot` に変わらないことも確認する(SC-010 例外 9)・
      **逆に、唯一の行が 084 由来の `void/recaptured`(または `late_scratch`)なら
      `no_snapshot` を返す**(`capture-eligibility` の「遡及行の例外」。
      T004a の復旧規則は `active` が無ければ最新 `captured_at` を残すので、
      `void/recaptured` だけが残るレースは実在しうる)
- [x] T047b [US1] **api の表示経路に構成一致判定を結線する**
      (`api/src/horseracing_api/chaos.py`):
      1. **`_snapshot_for_race` を新設する**(status 非依存・`LIMIT 1`)。
         既存の `_latest_active_snapshot`(107-119 行)は `WHERE status=='active'` を
         **クエリ境界で**掛けるので void 行を返せない。
      2. **`_latest_active_snapshot` の意味論は変えない** —
         `api/tests/unit/test_race_chaos.py` の 234-238 / 264 / 321 行が直接 assert し
         monkeypatch もしているので、変えると無関係なテストが落ちる。
      3. 取得した snapshot を `display_eligible(snapshot, started_now)` に通し、
         不一致なら荒れ度を出さない(`started_now` は `race_horses` から読む)。
      4. **`unavailable_reason` に新値 `field_changed_after_capture` を足す**。
         **列挙は 2 箇所にある** — `api/chaos.py:53` の内部 `UnavailableReason` と、
         **OpenAPI に出る `api/schemas.py:284-292` の `RaceChaosUnavailable.unavailable_reason`**。
         **両方に足す** — 後者を忘れると閉じた Literal + extra=forbid で
         pydantic の検証に落ち、T047a が叩く経路が 500 になり、T047c の再生成も空振りする。
         `void_reason='field_changed'` の行がある場合も**同じ理由**を返す —
         `active` 行が無いからと `no_snapshot` に落とすと「観測はあるのに無い」と
         報告することになり、説明が「誰かが予測を押したか」で揺れる。
         084 由来の `recaptured` / `late_scratch` の void 行は**この理由に含めない**
         (復旧 CLI がそれを残す場合があり、不可用の原因を取り違える)。
      5. 新値は閉じた `Literal` なので **OpenAPI 純追加 + front/admin の型再生成が
         Phase 5 のうちに要る**(T052 は Phase 8 で `JobRow` を扱うので、
         そこまで待つと `pnpm check:openapi` と型付き `Record` が 3 フェーズ赤のままになり
         Phase 5 の「独立に検証できる」主張が成り立たない → T047c で同時に回す)。
      6. **`api/tests/perf/test_chaos_readout_p95.py` を再実行する**
         (084 が計測した p95 経路に読み取りが 1 本増える)。
         **既存の p95 予算を超えたら `_snapshot_for_race` と `race_horses` の
         読み出しを 1 クエリに統合する**。超えなくても増分を記録する。
- [x] T047c [US1] **`unavailable_reason` の新値ぶんの OpenAPI と型を再生成する**
      (T047b と同じフェーズで回す): `front/openapi.json` / `admin/openapi.json` /
      `front/src/api/schema.d.ts` / `admin/src/api/schema.d.ts` を再生成してコミットし、
      両パッケージで `pnpm gen:types` → `pnpm check:openapi` を緑にする。
      **T052 は Phase 8 で `JobRow.summary` ぶんを同じ手順で再度回す**(2 回に分かれるのは
      契約追加が 2 フェーズにまたがるため)
- [x] T052a [P] [US1] **front の捕捉鮮度表示を窓の幅に追随させる**
      (`front/src/components/RaceChaosPanel.tsx::freshness`(53-62 行)+ テスト):
      現状は `発走${Math.round(seconds/60)}分前` と `captured_at` の **HH:MM のみ**で、
      T−30 分を前提にしている。窓が最大 86,400 秒になったので、
      **残り 1 時間以上は時間単位で表示し、取得時刻に日付を添える**(FR-022a)。
      放置すると前日取得の観測が「発走 1440 分前・09:30 取得」と出て当日取得と区別できず、
      凍結時点の開示(憲法 V)として機能しない。
      これは SC-010 の**意図した例外 6** である。
      あわせて **`field_changed_after_capture` の日本語ラベルが描画される**ことも
      同じテストファイルで検証する(T047b が足した新値の front 側の受け皿)。
      **ついでに 084 のラベル誤りを直す** — `UNAVAILABLE_LABEL` の `field_too_small` が
      「出走頭数が3頭未満」となっているが、実際のゲートは 4 頭未満である
      (`chaos_capture.py:196` / `capture-eligibility` §1 手順 8b)

**Checkpoint**: 予測ボタンで捕捉が走る。
US1 単体で SC-001..005 / SC-008 が検証できる
(SC-005 の「表示にも出ない」側は T047a / T047b がここで担保する)。
**FR-001b1(日次の窓外拒否と非 0 終了)と契機の保存値は Phase 7 の T043 まで未検証**である
(実装は T035 / T037 で入るが assert は T043 にある)。
SC-001..005 / SC-008 はこれに依存しないので、この checkpoint の主張は成立する。

---

## Phase 6: User Story 6 - 実行順序の保証 (Priority: P1)

**Goal**: 捕捉が予測の直前・同一実行単位で走ることと、
ジョブ再試行で外部取得が繰り返されないことを保証する。

**Independent Test**: 同じジョブを再実行すると捕捉の subprocess が **0 回**呼ばれる。

### Tests for User Story 6

- [x] T039 [P] [US6] 状態機械のテストを書く(`ops/tests/unit/test_predict_capture.py` に追加。
      **`launched` で止まった記録を再実行しても捕捉の subprocess が 0 回**
      = FR-015 の MUST NOT を機械的に固定する 1 本を必ず含める):
      **結果が確定済み**(`state="done"`)で再実行 → 捕捉の subprocess が **0 回**(FR-015) /
      **`state="started"` のまま残っている**(印をコミットした直後にクラッシュした状態)で
      再実行 → 捕捉が **1 回だけ**走る /
      その 1 回の後は二度と走らない(`retried` フラグ)
- [x] T040 [P] [US6] 順序のテストを書く(同ファイル):
      呼び出し順のスパイで capture → predict の順であることを確認する(FR-013)

### Implementation for User Story 6

- [x] T041 [US6] 状態機械を実装する(`ops/src/horseracing_ops/runner.py::run_predict`):
      捕捉を試みる前に
      `capture = {"state":"started", "started_at":…, "outcome":"unknown"}` を書いて **COMMIT**。
      **`Popen` の直後・`communicate()` の前に、別セッションで `state="launched"` に進めて COMMIT**
      (抑制状態と同じ理由 — ワーカーのトランザクションに書くと、
      クラッシュ時の巻き戻しで印ごと消えて 2 状態と同じ穴に戻る)。
      再実行時は **`state` が `done` または `launched` なら飛ばし**、
      `started` かつ未再試行なら **1 回だけ**走らせる。
      **2 状態では FR-015 を満たせない** — FR-015 は「取得を**始める前**に落ちたのか
      **始めた後**に落ちたのか」を記録から区別することと、後者を再実行しないことを
      MUST としている。`launched` が無いと、`communicate()` の途中で
      ワーカーが死んだ記録が `started` のまま残り、再試行が**外部取得をもう一度発火させる**
      (FR-015 の MUST NOT を踏む)。
      **逆に「試みた」印だけにしてもいけない** — 印をコミットした直後・
      subprocess を起動する前にクラッシュすると、再試行で捕捉が**永久に飛ばされ**
      そのレースの捕捉機会が静かに失われる(SC-001 が黙って未達になる)。
      `summary` は JSONB の変更追跡が効かないので**一度に代入する**
      (既存の `run_predict` / `run_one` と同じ規律)
- [x] T042 [US6] 外側打ち切りを `unknown` として記録する(同ファイル):
      `subprocess.TimeoutExpired` を捕まえて `os.killpg` で**プロセスグループごと終了**させ、
      **`state="done"`** / `outcome="unknown"` / `reason="outer_timeout"` を書き、
      **予測は続行する**。
      **`state="started"` のままにしてはならない** — T041 の「1 回だけ回復」が発火して
      打ち切られたレースにもう一度取得が走る(FR-019 の「再試行の対象にしない」に反する)。
      打ち切りは起動前クラッシュと違い、取得は**実際に走っている**
- [x] T042a [US6] 打ち切りのテストを書く(`ops/tests/unit/test_predict_capture.py` に追加):
      `summary.capture.outcome == "unknown"` かつ **`state == "done"`**・
      **打ち切り後に再実行すると捕捉の subprocess が 0 回**(再試行の対象にならない・FR-019)・
      **打ち切り後に遅れて snapshot 行が増えない**(子孫プロセスが生き残っていない)

**Checkpoint**: 順序と再試行が保証された

---

## Phase 7: User Story 3 - 捕捉の出所を記録し選択バイアスを可視化する (Priority: P1)

**Goal**: UI クリック由来と日次の中立な捕捉を区別し、選択バイアスを報告に出す。

**Independent Test**: 前向き報告に契機別の内訳と、利用者が選んだ捕捉の割合が出る(SC-007)。

**実行順序(ID 順ではない)**: T043 / T044(テスト)→ T046 → T047 → T046a

### Tests for User Story 3

- [x] T043 [P] [US3] 出所記録のテストを書く(FR-009 / FR-010)
      (`live/tests/integration/test_capture_provenance.py`):
      `capture_trigger` が保存され、`capture_policy_version` が
      **リテラル `"capture_policy_v1"`** である・
      **`source` が契機で上書きされない**(`source == fetcher.source`・INV-3・FR-010)・
      未知の契機は CHECK 制約で弾かれる・
      **その日の最も遅い発走が窓上限より先だと既定で拒否され、
      `--allow-outside-horizon` で実行できる**
      (FR-001b1 — 操作ミス 1 回で 1 開催日ぶんの確認適格を失わせない)・
      **対象日のどのレースも発走時刻が不明なときは拒否しない**
      (実データで 2024 年は充足率 0%。全件が手順 4 で見送られ取得は 0 本なので、
      拒否すると正常な日を操作ミス扱いすることになる)・
      **この拒否のときだけ終了コードが非 0 で、通常の見送りも取得失敗(`fetch_failed`)も 0 である**
      (取得失敗で非 0 にすると、ops が JSON を読めず `unknown`=外側打ち切りに化け、
      恒常的な取得元障害が「たまたま遅かった」として埋もれる)
      (FR-001b1 の MUST。一律 0 を返す実装でも他の assert は全部緑になるので、
      2 本立てで固定する)・
      **CLI を `--trigger` / `--capture-deadline-seconds` 無しで両経路実行**すると
      `--date` は `daily_operational` / 期限 **30 秒**、
      `--race-id` は `explicit_command` / 期限 **30 秒**として効く
      (argparse の単一既定値だと片方が静かに誤る)・
      **`--json` と `--date` の併用が型付きエラーで拒否される**
      (job-observability §3。実装は T037 にあるが、これまで誰も assert していなかった)
- [x] T044 [P] [US3] 報告のテストを書く
      (`training/tests/unit/test_prospective_trigger_breakdown.py`):
      **5 契機すべてが 0 件でも行として出る**(`legacy_unknown` も **`n=0` の行として必ず出る** —
      報告は単一 digest スコープなので遡及行は新版に現れないが、**行の数は常に 5**)・
      `user_selected_share` が **0 のときも出力される**・
      **`predict_manual` と `predict_auto` が合算されない**・
      `prospective_selection_bias` の **`observed_primary_source`** が実測の最多契機になる
      (**同数のときは契機名の辞書順**)・
      `policy_primary_source` は定数 `daily_operational`・
      **全契機 0 件なら `observed_primary_source` は null・`primary_source_claim_violated` /
      **`user_selected_role`** / **`removable`** / **`note`**(FR-012 の開示文。
      `observed_primary_source` は**同数のとき契機名の辞書順**で決める(決定的に)。
      `contracts/horizon-artifact.md` の 6 キーを全部埋める) は false**・
      **全行が `legacy_unknown` で分母が 0 なら `user_selected_share` は null**
      (0.0 にすると「偏りが無い」と読めてしまう。migration が既存行を全部
      `legacy_unknown` にするので現実に起こりうる)・
      **同数のときは決定的なタイブレーク**(契機名の辞書順)で揺れない・
      利用者選択が中立側を上回ったら `primary_source_claim_violated: true` が立つ・
      「除去できない」の開示が出る・
      既存の出力キーが不変(SC-010)・
      **`primary_horizon` の出力キーがちょうど `mode` / `minimum_seconds_to_post` /
      `maximum_seconds_to_post` の 3 つ**(`artifact_field_present` が無い)・
      **`analysis_unit` の下に出る**(位置も検証する)・
      **`by_capture_horizon` が 6 バケットで、最下位のラベルが窓下限から導出される**・
      **窓外の `seconds_to_post` は層別に渡らない**(窓内フィルタを通った行にしか
      `_capture_horizon` を呼ばない前提を固定する — 086 は窓外も保存するので、
      将来この前提が崩れると `ValueError` で報告が落ちる)
      (窓下限 300 の artifact では `10-30m` ではなく `5-30m` になる。
      **窓上限 20000 の artifact ではラベルがちょうど
      `10-30m / 30-60m / 1-3h / 3h+` の 4 本**で、最上位 `3h+` の上限は `None`)・
      (遡及行は単一 digest スコープにより**そもそも報告に現れない**ので、
      専用の除外理由は存在しない — 検証は T017a が担当する)・
      **凍結構成 ≠ 現況 started の観測が `field_changed_after_capture` として除外され
      件数に出る**(FR-002b の報告側 — 表示側は T047a が担保しており片脚だけ無検証にしない)

### Implementation for User Story 3

- [x] T046 [US3] 前向き報告に契機別内訳を足す(FR-011 / FR-012)
      (`training/src/horseracing_training/chaos_bands.py`):
      `by_capture_trigger`(**5 契機**・件数・確認適格数・`selection_biased` フラグ)・
      `user_selected_share`(= (`predict_manual` + `explicit_command`) / (全体 − `legacy_unknown`)。
      **分母が 0(全行が `legacy_unknown`)なら `null`** — 0.0 にすると「偏りが無い」と読める)・
      `prospective_selection_bias` を**純追加**する(既存の `selection_bias_note`
      = coverage 報告の別キーと混同しない)。既存キーは触らない。
      **`primary_source` は定数で印字しない** — 実際に最多の契機を出し、
      **式で凍結する**: `primary_source_claim_violated = (predict_manual + explicit_command) >
      (daily_operational + predict_auto)`。`legacy_unknown` は**分母から除外**する
      (バイアスの有無が不明なので比較に入れない)。
      `observed_primary_source` は最多契機(`legacy_unknown` を除く)。
      first-wins 規則により**利用者が見るレースでは予測クリックが日次を必ず先取りする**ので、
      「日次が主」を定数で出すと報告が実態と逆のことを言いうる。
      **`predict_manual` と `predict_auto` を合算してはならない** —
      データ更新の後に自動で積まれる予測は利用者が選んだものではなく中立であり、
      合算すると選択バイアスを過大に見積もる。
      **`by_capture_horizon` のバケットを広い窓に合わせて分割する**
      (**出力側のループ `chaos_bands.py:2210` も module 定数を走査しているので同時に直す** —
      分類だけ動的化して列挙が静的なままだと部分実装になる)
      (`10-30m / 30-60m / 1-3h / 3-6h / 6-12h / **12h+**` — 最上位は上限を閉じないので
      `12-24h` とは呼ばない)。
      **バケットの列挙は `probability` の `capture_horizon_buckets()` を import する**
      (T013。**training 側で再実装しない** — 2 箇所で別々に列挙すると必ずずれる。
      horizon-artifact §5 が正本。
      境界を焼き込むと、窓を狭めたときに構造的に空のバケットが生じる
      = SC-010 の例外 5 が直したのと同じ欠陥が無検出で再発する)。
      T013 が下限を引数で受け、T009 が窓下限 300 のケースをテストするので、
      **ラベル導出が唯一整合する選択肢である**
      **最下位の下限は artifact の `minimum_seconds_to_post` から導出する** —
      `add-horizon` は任意の下限を受け付けるので、600 を焼き込むと
      min<600 の版を発行した瞬間に `_capture_horizon` が `ValueError` で報告を落とす。
      最上位の上限は開いたままにする。
      現行の `0-9m / 10-29m / 30-59m / 60m+`(`chaos_bands.py:67-70`)では
      `0-9m` が構造的に常に空で、中央値 24,124 秒と 71% のクリックが
      `60m+` の 1 本に潰れ、層別の目的(広い窓の代償の可視化)を果たせない。
      これは SC-010 の**意図した例外 5** である
- [x] T047 [US3] **報告の loader に現況 started 集合を足す**
      (`training/src/horseracing_training/chaos_bands.py::load_prospective_rows`):
      現在は `ChaosReadout` / `ChaosSnapshot` / `Race` / `RaceResult` しか読まず
      **`race_horses` を一切見ない**(1682-1717 行)。
      FR-002b の報告側判定には現況の started 集合が要るので、
      `ProspectiveChaosRace` に列を足し、**レース単位の N+1 を避けてバルクで取る**
      (`load_prospective_rows` は既に `race_ids` を集めているのでそこに相乗りする)。
      **同時に `ChaosSnapshot.capture_trigger` を列の射影に足す** —
      `load_prospective_rows` は**明示の 12 列射影**(`:1694-1707`)なので、
      足さないと `by_capture_trigger`(FR-011 / SC-007)が計算できない
- [x] T046a [US3] 確認適格の導出を報告に結線する(同ファイル):
      報告対象の artifact の窓で判定する(報告は単一 digest スコープなので、
      それが「その観測が使った artifact」と一致する)。あわせて**凍結構成が
      現況の started 集合と一致するか**を判定し、不一致は除外理由
      `field_changed_after_capture` として数える(FR-002b)。
      **`void(field_changed)` の行も同じ理由に数える**(`snapshot_not_active` に混ぜない —
      混ぜると同じ事象が 2 つの理由に散る)。
      **出力先は既存の `exclusions` カウンタ**(`not_one_row_per_race` などと同じブロック・
      `chaos_bands.py:1891-1921` で数え `:2281` で emit)

**Checkpoint**: 選択バイアスが測定され報告に出る

---

## Phase 8: User Story 4 - 捕捉結果が運用画面で見える (Priority: P2)

**Goal**: 予測実行時の捕捉結果をジョブ履歴で確認できるようにする。
見送りを「正常」として読める表示にする。

**Independent Test**: ジョブ履歴で `summary.capture` が読め、
見送り行にエラー色が付かない。

### Tests for User Story 4

- [x] T048 [P] [US4] API のテストを書く(`api/tests/unit/test_jobs_summary.py`):
      `summary` が転記される・**`capture` が `summary["capture"]` から埋まる**・
      **OpenAPI に `JobCaptureRow` と `outcome` の列挙が現れる**(FR-020 の型付き要求)・
      **`reason` は列挙型ではない**(未知値でも 422 にならず素通しできる)・
      既存フィールドの型と名前が不変・
      **全 path が GET のままである既存の不変テストが緑**(FR-023)・
      `summary` が NULL の行でも 500 にならない・
      **`capture` があって `state="started"` かつ `reason` が無い行でも 200 で、
      `capture.reason` が null で返る**(捕捉が走っている最大 18 秒間は
      理由がまだ無い。その間に一覧を叩けば必ず踏むので、
      `reason` を必須の列挙にすると確実に 500 になる)
- [x] T049 [P] [US4] admin のテストを**既存ファイルに追記**する
      (`admin/src/pages/JobsPage.test.tsx` — admin は同居配置でこのファイルが既に存在する。
      新しいディレクトリを作らない):
      **適格性由来の見送り**(発走済み / 確定済み / 捕捉済み)に**エラー色のクラスが付かない**
      (FR-022)・
      **取れなかったもの**(`capture-eligibility` §4 の `表示区分 = 取得不可` の **9 値**
      + ops 由来の `outer_timeout` / `launch_failed` = **全 11 値**。
      `*_during_fetch` の 4 値と `fetch_failed` を落とさない)が
      適格性由来と**別クラス**になる
      (同色にすると、予算不足で全捕捉が落ちている状態が
      「正常」に見え SC-001 の未達に気づけない)・
      **admin の分類集合が「§4 の 適格 と 取得不可 の全値 + ops 由来」と一致する**
      (静的テスト。`表示区分 = —` の rejected 群は対象外)。
      `auto_capture_disabled`(FR-001c)と `outer_timeout`(FR-019)は
      `capture_chaos` ではなく実行層が付けるので、§4 の**「ops 由来の理由」小節**に載っている
      (本表だけと比べると取りこぼす)・
      **`state="started"` / `"launched"`(進行中)の行が中立色で描かれる**
      (`unknown` を無条件に注意色にすると、捕捉が走っている最大 18 秒間
      admin が常時警告だらけに見える — `job-observability` §6)・
      捕捉の失敗でジョブ行が失敗として描かれない(FR-021)・
      未知の理由文字列はそのまま表示される(握りつぶさない)

### Implementation for User Story 4

- [x] T050 [US4] `JobRow` に捕捉結果を**型付きで**純追加する(FR-020・憲法 VI)
      (`api/src/horseracing_api/schemas.py`):`summary: dict | None` に加えて
      **`capture: JobCaptureRow | None`** を定義する。
      **フィールドの正本は `data-model.md` §6** —
      `state`(`Literal["started","launched","done"]`)/ `outcome`(列挙型)/
      **`reason`(開いた文字列・任意)** / `capture_strength` / `seconds_to_post` /
      `confirmation_eligible`(FR-020 が「確認可能性の区分」を公開契約に MUST としている)/
      `chaos_snapshot_id`。
      **`state` を落としてはならない** — 進行中(`state="started"`)を
      完了扱いにすると、捕捉が走っている最大 18 秒間 admin が捕捉を注意色で塗り続ける
      (`contracts/job-observability.md` §6 が禁じている失敗モード)。
      **`reason` を必須の列挙にしてはならない** — 進行中の記録には理由がまだ無いので
      `GET /jobs` が確実に 500 になる。
      `dict` だけだと OpenAPI に結果の別も理由も現れず、admin が
      **公開契約に現れない形**に依存することになる(契約先行の原則に反する)。
      既存フィールドの型・名前・順序は不変
- [x] T051 [US4] router で転記する(`api/src/horseracing_api/routers/jobs.py`):
      `summary=j.summary` に加えて **`capture=summary.get("capture")` を
      `JobCaptureRow` に写す**。これは**キーを写すだけの射影**であり
      解釈でも再計算でもない(021 規律は保たれる)。
      射影が無いと `capture` は OpenAPI に現れるのに**常に null** になる。
      **射影は fail-soft にする** — `JobCaptureRow` の構築が
      `ValidationError` になったら**捕まえて `capture=None` にし、`summary` は返す**
      (FR-020。`outcome` は閉じた `Literal` なので、遡及行・部分書き込み・
      未知値が 1 件でもあると `GET /jobs` 全体が 500 になる)
- [x] T052 [US4] OpenAPI と型を再生成して同期する:
      `front/openapi.json` / `admin/openapi.json` を再生成し、両者の**バイト一致テスト**を緑に保つ。
      **型は front と admin の両方を再生成してコミットする** —
      `front/src/api/schema.d.ts` と `admin/src/api/schema.d.ts`。
      `check-openapi.sh` は committed の `openapi.json` から生成した型を
      committed の `schema.d.ts` と diff するので、**front 側を忘れると
      `pnpm check:openapi` が落ちる**(084 の前例どおり両方コミットする)。
      手順は各パッケージで `pnpm gen:types` → `pnpm check:openapi`
- [x] T053 [US4] admin に捕捉チップを足す(`admin/src/pages/JobsPage.tsx` +
      `admin/src/lib/captureLabels.ts`):
      **`skipped` を 2 分類する**(job-observability §6):
      `captured` = 中立 / **`skipped`(適格性由来)= 中立(グレー)** /
      **`表示区分 = 取得不可` の 11 値(本表 9 + ops 由来 `outer_timeout` / `launch_failed`)= 注意**
      (`fetch_failed` だけは `status=failed` なので `skipped` ではない —
      色の区分は `status` ではなく `表示区分` 列で決める)/
      **`state="started"` / `"launched"`(進行中)= 中立(「捕捉中」)。
      ただし `started_at` が外側の打ち切り(18 秒)より古ければ注意色**
      (ワーカーが途中で死んだ記録を永久に「捕捉中」と見せない)/
      **`auto_capture_disabled`(FR-001c で止めている)= 中立** /
      `rejected` `failed` = 注意 / `unknown` **かつ `state="done"`** = 注意(いずれも**行全体の成否表示は変えない**)。
      **「取れなかった」側の完全な列挙は `capture-eligibility` §4 の `表示区分` 列を正本とする**
      (`deadline_exceeded` / `source_cooldown` / `throttle_backlog` /
      **`field_changed_during_fetch`** / **`result_settled_during_fetch`** /
      **`*_during_fetch` の 4 値** / **`fetch_failed`** / **`robots_disallowed`**)。
      一色にまとめてはならない — contract が禁じた失敗モードを実装で固定することになる。
      理由文字列は日本語ラベルへの**単一対応表**で写像する
      (`front/src/lib/chaosLabels.ts` の band / event ラベルと**同じ方式で admin 側に新設する**。
      **front から import しない** — admin の独立性を壊さない。
      なお front の `UNAVAILABLE_LABEL` は `chaosLabels.ts` ではなく `RaceChaosPanel.tsx:23` にある)。
      未知の理由は**生の文字列を出しつつ注意色**にする(既定は「取得不可」・§4)

**Checkpoint**: 全 user story が独立に機能する

---

## Phase 9: Polish & Cross-Cutting Concerns

- [x] T054 [P] **T003 に列挙したパッケージ**の lint を通す
      (`ruff check` — db / probability / **eval** / live / ops / api / training / scrape / **features** / **betting** / **serving** / **ingest**、
      `pnpm tsc` / `pnpm lint` — admin / front)
- [x] T055 **T003 に列挙したパッケージ**のテストを回す。
      **`scrape` は必ず個別に確認する**(件数の正本は T003 の記録。計画時点の実測は 97 件)。
      **SC-010 の広域担保は「既存テストが緑」+「例外 10 件の個別テスト」+
      T057 の fixture ゴールデン差分**である。(`FetchRefused` で挙動が変わる唯一の既存パッケージ)
- [x] T056 [P] リーク境界のテストを足す
      (`features/tests/unit/test_feature086_leak_guard.py`):
      `capture_trigger` / `capture_policy_version` / 捕捉由来の値が
      モデル特徴・校正・買い目に流入しない(FR-024)・migration head が `0013` である・
      **`capture_policy_version` が報告の `by_*` 層別キーに現れない**(FR-009 の監査専用)・
      **スケジューラの入口(cron / APScheduler / systemd timer 等)が追加されていない**
      (FR-025 の否定要件を機械的に固定する・C5)・
      **`chaos_snapshots_quarantine` を `api` / `training` / `features` が参照しない**
      (data-model §2b の憲法 V 正当化は「どこからも読まない」に依拠しており、
      その根拠をここで固定する。既存の import-graph / grep 型と同じ書き方でよい)
- [x] T057 [quickstart.md](./quickstart.md) を実 DB で通す(10 手順)。
      とくに **SC-004(発走後で取得 0 回)** と **SC-002(連続実行で増えない)** は
      ops worker のログで外部取得が発生していないことを目視で確認する。
      **変更前に決定的な fixture でゴールデンを採取しておく**(`chaos_snapshots` が 0 行でも
      fixture を仕込めば `prospective-report` と日次 CLI 要約は決定的に出る)。
      SC-010 の **10 例外**だけをマスクして比較する
      (マスク対象は spec の SC-010 の表から機械的に導出する - 手で数えるとずれる)。
      実 DB で 1 行でも捕捉できたら、その時点の出力も基準に加える
      (SC-010 の広域担保は現状「既存テスト緑 + 例外別テスト」だけで、
      報告出力のゴールデン差分が無い。最初の 1 行がその基準を作れる)
- [x] T058 [P] 084 の運用メモを是正する
      (memory `chaos-084-capture-operations.md`):
      「1 日 1 コマンドで T−30 分」は**達成不可能**(開催日の発走は 8.8 時間に分散し
      1 回で窓に入るのは 1〜2 レース)。086 導入後の運用は
      「日次の中立な捕捉が主 + 予測実行で自動的に積み上がる」に更新する。
      **運用スイッチを運用メモと `deploy/README.md` に書く**(FR-001c):
      環境変数 `OPS_CAPTURE_ON_AUTO_PREDICT` / 既定は有効 / `0` または `false` で無効 /
      無効時は `summary.capture.reason = "auto_capture_disabled"` が残る。
      **名前が運用文書に出ていないと「仕様変更なしで止められる」という
      FR-001c の目的が実質未達になる**(止め方を知る手段が無い)。
      **残存リスクも運用メモに書く**: 取得は成功したが凍結できなかったレース
      (`partial_market_odds` / `invalid_popularity_ranks` /
      **取得後・保存前の `deadline_exceeded`**)は行を作らないので、
      予測を押すたびに取得しに行く(抑えるのは頻度制限とクールダウンだけ)。
      日次取得が 429 を受けても抑制は書かれないので、
      直後の予測実行からの捕捉が取りに行きうる(FR-016/FR-017 の適用範囲差)

---

## Dependencies & Execution Order

### Phase Dependencies

```text
Phase 1 (Setup)  ── T000 codex レビュー = 品質ゲート(憲法 MUST)
    ↓
Phase 2 (Foundational: migration 0013 + ORM)   ← BLOCKS すべて
    ↓
Phase 3 (US2 主 horizon)   ← US1 の前提。窓の定義が無いと確認適格が判定できない
    ↓
Phase 4 (US5 取得の礼儀)   ← UI クリックに取得を任せる前の安全弁
    ↓
Phase 5 (US1 予測ボタンで捕捉) 🎯 MVP
    ↓
Phase 6 (US6 実行順序)  ── Phase 7 (US3 出所記録) ── Phase 8 (US4 運用画面)
    ↓                          ↓                        ↓
                        Phase 9 (Polish)
```

### User Story Dependencies

- **US2 (P1)**: Foundational のみに依存。**US1 の前提**(窓が無いと確認適格を判定できない)。
  内部順序が固い: **T014(生読みの追加を含む)→ T016 → T012**
  (**T017 = 承認 manifest への掲載は前提ではない** — 昇格関数は
  manifest 掲載済みの digest を `status` 不問で受理する[horizon-artifact §2]ので、
  fixture 比較は payload だけで閉じる)。
  T014 でローダを固くした瞬間に v1 が読めなくなるので、
  生読み経路を**同じタスクで**足さないと T016 / T012 が実行不能になる
- **US5 (P1)**: Foundational のみに依存。US1 より**先**に入れる
  (UI クリックが取得を駆動する前に安全弁を置く)。
  ただし Phase 4 で揃うのは**部品**であり、`capture_chaos` への結線(T036)と
  結線後の振る舞い検証(T036a / T036b)は Phase 5 に属する
- **US1 (P1)**: US2 + US5 に依存。**MVP**
- **US6 (P1)**: US1 の ops 結線に依存
- **US3 (P1)**: US1(捕捉が出所を書く)+ US2(確認適格の導出)に依存
- **US4 (P2)**: US1 に依存(表示する中身が要る)。他 story と独立にテスト可能

### Parallel Opportunities

- Phase 1: T002 / T003 は並列(T000 の後)
- Phase 2: T006 / T007 は並列(T004・T005 の後)
- Phase 3: T009 / T010 / T011 / T012b / **T017a** の 5 本が並列(T017a も fixture ベースにする — T012 と同じ理由で実 digest に依存させない)。
  **T012 / T012a は執筆は並列だが実行は T014(昇格関数)と T016(新 payload の生成)の後**
  (**承認 manifest への掲載=T017 は不要** — fixture 比較は payload だけで閉じる)
- Phase 4: T019 / T020 / T021 / T021a のテスト 4 本が並列
  (**実測タスク T018a は Phase 5 に移動** — 保存段が Phase 5 の T035 を要求するため)
- Phase 5: **T026(スパイ fixture)を先に**完了させてから T026a / T027群 / T028群 / T029 / T030 / T031 / T047a が並列(**群の中は直列**)。
  **T047a は執筆だけ並列** — 緑になるのは T047b(表示側の結線)の後なので、
  実行順序行のとおり T038c の後に回す。
  実装 T032-T036 は**同一ファイル**なので直列。
  同一ファイルを触る群は直列: `test_capture_eligibility_order.py`(T027/T027a/T027b)/
  `test_capture_single_observation.py`(T028/T028a)/
  `test_chaos_politeness.py`(T020/T036a/T036b)/
  `test_predict_capture.py`(T030/T038c/T039/T040/T042a)/
  `test_chaos_artifact_horizon.py`(T010/T012/T012a)/
  `test_race_chaos_api.py`(T011/T034b/T047a)/
  `RaceChaosPanel.tsx`(**T052a のみ**。T047c は openapi / schema.d.ts 側だが、
  tsc のため T052a と同一コミットで入れる)
- Phase 7 / Phase 8 は US1 完了後に**並列で進められる**
- Phase 9: T054 / T056 / T058 は並列

---

## Parallel Example: User Story 1

```bash
# テストを先に並列で書く(実装前に FAIL することを確認する)
Task: "取得回数を数えるスパイ fetcher を live/tests/conftest.py に用意"
Task: "正常系を live/tests/integration/test_capture_happy_path.py に"
Task: "判定順序を live/tests/integration/test_capture_eligibility_order.py に"
Task: "1 レース 1 観測を live/tests/integration/test_capture_single_observation.py に"
Task: "同時実行を live/tests/integration/test_capture_concurrency.py に"
Task: "ops 結線を ops/tests/unit/test_predict_capture.py に"
Task: "表示側の構成一致を api/tests/integration/test_race_chaos_api.py に"
```

`live/src/horseracing_live/chaos_capture.py` を触る T032-T036 は同一ファイルなので**直列**。

---

## Implementation Strategy

### MVP (Phase 1 → 5)

0. **T000**: codex レビューで plan/tasks の未レビュー設計を潰す(憲法の品質ゲート)
0b. **T008a は計画中に実施済み**(`CLAUDE.md` の 086 ブロックは現行設計に同期済み)。
   着手時に plan.md の Summary / Constitution Check と読み比べ、
   計画がさらに動いていないことだけ確認する
1. Phase 1-2: スキーマを整える
2. Phase 3 (US2): 窓を事前登録し、084 の欠陥を塞ぐ
3. Phase 4 (US5): 取得の礼儀を先に入れる
4. Phase 5 (US1): 予測ボタンに捕捉を相乗りさせる
5. **STOP して検証**: quickstart 4-7 を実 DB で通す(SC-001 / SC-002 / SC-004 / FR-014)。
   **この時点では `JobRow.summary` の露出(T050-T052・Phase 8)がまだ無いので、
   `ingestion_jobs.summary` を DB から直接読む**(API 経由の確認は Phase 8 以降)。
   quickstart §7 が起こすのは**見送り**であって失敗ではないので、
   **SC-003(捕捉が失敗しても予測は成功)は T030 で担保する**。
   SC-005 / SC-008 / SC-009 も quickstart に手順が無いので自動テストで確認する
   (T028 / T029 / T020・T036a)
6. この時点で「予測を押すだけで前向き検証が積み上がる」が成立する

### Incremental Delivery

- Phase 6 (US6): 再試行で取得を繰り返さない保証を足す
- Phase 7 (US3): 選択バイアスを測って報告に出す(**研究上の正しさに必要**)
- Phase 8 (US4): 運用画面で結果が見える
- Phase 9: 仕上げと運用メモの是正

### 注意点

- **US3 を後回しにしない**。契機を記録しないまま観測を貯めると、
  後から選択バイアスを復元できない(捕捉時にしか分からない情報である)
- `live/src/horseracing_live/chaos_capture.py` は US1 の中心で複数タスクが集中する。
  ファイル競合を避けるため T032 → T036 は順に進める
- `scrape/fetch.py` の変更は**全利用者に波及する**。T023 の回帰確認を飛ばさない

---

## Notes

- [P] = 別ファイル・依存なし
- **T025b / T025c は条件付きタスク**(T000a または T018a の実測が収まらなかった場合にのみ起こす。`ops` の `_CAPTURE_DEADLINE_S` と `fetch-politeness` §4 / `job-observability` §2 を同時に直す)
- **T045 は欠番**(codex レビューで US3 から Phase 5 の T035 へ移した痕跡)
- **T047a / T047b は Phase 5 に前倒し**(SC-005 の表示側を MVP の checkpoint で担保するため)。**T017b は T017 の直後**に実行する(新 digest を発行したら、同じ流れで参照先を張り替える —間に別の作業を挟むと、superseded を指したままの環境が残る)
- **T000 の本文はレビューの「対象」であって結論ではない**(結論は plan.md の採否表。本文に残る 25s-45s / 生読み経路はレビューで撤回された)
- テストは実装前に書き、**FAIL することを確認してから**実装する
- **判定テストは必ず `spy.calls == 0` を assert する**。理由文字列の一致だけを見るテストは、
  判定順序が逆でも通ってしまう(codex 指摘への直接の対応)
- 各タスクまたは論理的なまとまりごとにコミットする
- Checkpoint ごとに story を独立に検証できる
