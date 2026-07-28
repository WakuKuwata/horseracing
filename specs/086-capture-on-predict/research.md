# Research: 予測実行時の荒れ度スナップショット捕捉 (086)

**Date**: 2026-07-27 | **Plan**: [plan.md](./plan.md) | **Spec**: [spec.md](./spec.md)

すべての決定は**実コードの読解**と**実 DB の計測**で裏取りしてある。
推測で埋めた欄はない。

---

## D1: 主 horizon の具体値

**Decision**: `primary_horizon = {minimum_seconds_to_post: 600, maximum_seconds_to_post: 86400}`
(発走 10 分前 〜 24 時間前)。

**実測(本計画で取得)**:

```text
predict ジョブ 286 件(post_time 既知)
  発走前            158 件
  窓 [600, 86400]   151 件 = 発走前の 95.6%
  T−10 分より後       4 件 (2.5%)
  T−24 時間より前     3 件 (1.9%)
中央値 24,124 秒 = 発走 6.7 時間前 / 3 時間より前が 112 件 (71%)
```

**Rationale**:

- **下限 600 秒**: `seconds_to_post` は**予定**発走時刻からの計算である。発走は数分ずれうるので、
  T−2 分の捕捉は実際には発走後だったかもしれない。10 分の床を置くと「発走前である」ことが
  時刻誤差に対して頑健になる。代償は実測 4 件(2.5%)のみ。
- **上限 86400 秒**: 前日を超えると市場が形成されておらず、出走取消も未確定。
  24 時間は「前日発売開始」というドメイン上の自然な境界。代償は実測 3 件(1.9%)。
- **確認コホートの到達速度**: 窓内 95.6% を確認適格にできるので、
  2025 実績(109 開催日 / 3,455 レース・S≥20 基準率 9.3%)から最小陽性 100 まで **0.4〜0.8 年**。

**確認コホートの減衰(実測して定量化した)**: D0 の「1 レース 1 観測」設計は、
捕捉後に出走構成が変わったレースを確認コホートから落とす(FR-002a)。
実測: **2025 年以降 5,465 レースのうち取消を含むのは 300 レース = 5.5%**。
このうち実際に落ちるのは**捕捉より後に取消が入った分だけ**なので 5.5% は**上限**である
(捕捉時点で既に取消が記録済みなら凍結フィールドは現況と一致する)。
窓外の 4.4% と合わせても減衰は**最大 1 割弱**で、到達時期は 0.45〜0.9 年に伸びる程度。
判定日 2027-12-31 に対して余裕がある。前向き報告に
`field_changed_after_capture` の実測率を出し、想定を超えていないか監視する。

**Alternatives considered**:

| 案 | 却下理由 |
|---|---|
| T−10..30 分(狭い窓) | 実測で発走前クリックの **5.1%(8 件)** しか入らない。確認コホートに約 6 年かかり、判定日 2027-12-31 に間に合わない |
| 下限 0 秒 | 発走時刻のずれで発走後の観測が確認コホートに混入する |
| 上限なし | 数日前の薄い板を「市場の見立て」として確認コホートに入れることになる |

**残るリスク(開示)**: 窓が広いので観測のオッズ成熟度が不均一。
`by_capture_horizon` 層別を前向き報告に出し続けることで可視化する。
ただし**既存のバケット(0-9m / 10-29m / 30-59m / 60m+)は広い窓に合わない** —
登録した窓 [600, 86400] では `0-9m` が構造的に常に空で、
**中央値 24,124 秒と 71% のクリックが `60m+` の 1 本に潰れる**。
→ `10-30m / 30-60m / 1-3h / 3-6h / 6-12h / **12h+**` に**改称 + 分割する**
(`10-29m`→`10-30m` / `30-59m`→`30-60m` は範囲が同じままの改称であり、
キーで拾っている消費者に影響する。SC-010 の意図した例外 5)。
**最上位は上限を閉じない** — `12-24h` として 86399 で閉じると、窓の上端ちょうど(86400)の
観測が該当バケット無しになり `_capture_horizon` が `ValueError` を送出する。
**最下位の下限は artifact の `minimum_seconds_to_post` から導出する**
(`add-horizon` は任意の下限を受け付けるため)。
層別は**記述であって主判定ではない**(多重比較方針は既存のまま単一主要評価項目)。

---

## D1a: 開催日の発走時刻の分散と予測実行の頻度(実測)

**Decision**: 「スケジューラ無しでは近接捕捉を量産できない」という結論の根拠を数値で残す。

```text
開催日 1 日ぶんの races(post_time 既知)を取り、
  レース数                          36
  最初の発走から最後の発走まで       8.8 時間
predict ジョブの日次件数(開催日)   70 件超
```

**含意**: 発走が 8.8 時間に分散するので、日次コマンド 1 回で**発走直前(T−30 分)**に
入るのは 1〜2 レースだけ。全レースを近接捕捉するには 1 日 20〜27 回の実行が必要で、
それは憲法が先送りしているスケジューラそのものである。
**採用した広い窓 [600, 86400] なら 1 回で当日の未発走レースをほぼ全部拾える**が、
オッズの成熟度は揃わない(その不均一は `by_capture_horizon` 層別で開示する・D1)。

---

## D2: migration 0013 の要否と列

**Decision**: 必要。**スキーマは追加のみ**の migration 0013
(既存データに重複があるときは中断する — D2c)。

> **D2a で改訂**: 契機の語彙は 3 値ではなく **5 値**(`predict_job` を
> `predict_manual` / `predict_auto` に分割し `legacy_unknown` を追加)。以下は初版の記録。

```text
chaos_snapshots
  + capture_trigger        TEXT NOT NULL  -- D2a で 5 値に改訂
  + capture_policy_version TEXT NOT NULL  -- 'capture_policy_v1'
  CHECK (capture_trigger IN (...))

fetch_throttle_state (新規・運用状態)
  domain          TEXT PRIMARY KEY
  next_allowed_at TIMESTAMPTZ
  blocked_until   TIMESTAMPTZ NULL
  block_reason    TEXT NULL
  updated_at      TIMESTAMPTZ NOT NULL
```

**Rationale**:

- `capture_trigger` が無ければ FR-011/012 の**選択バイアス開示が原理的に書けない**。
  後から派生で復元することもできない(捕捉時にしか分からない)。
- 計画時点の実測は `chaos_snapshots` 0 / `chaos_readouts` 0 だが、これは**利便であって設計の前提ではない**。
  migration は行数に依存させず、常に nullable→backfill(`legacy_unknown`)→`SET NOT NULL` を流す
  (作者の環境の行数に依存した migration は行を持つ環境で壊れる)。
  DEFAULT は置かず**書き手に必ず明示させる**(暗黙の既定が 084 の horizon 欠陥の原因だった)。
- `fetch_throttle_state` は**運用状態**であって監査記録ではない。UPDATE される。
  憲法 V が禁じているのは「オッズの履歴を持つこと」であり、取得抑制の現在値はそれに当たらない。

**Alternatives considered**:

| 案 | 却下理由 |
|---|---|
| `confirmation_eligible` 列を保存 | 冗長。artifact digest から導出できる(D8)。保存すると artifact 差し替え時に整合を取る新しい義務が生まれる |
| 抑制状態をファイルロック / プロセス内変数で持つ | 実コード確認: `HttpFetcher._last_fetch` は**インスタンス変数**であり、捕捉は毎回新しい fetcher を作るのでクリック間・ワーカー間で 1 秒制限が効かない(FR-017 が指す実害)。ファイルはコンテナ跨ぎで共有されない |
| `ingestion_jobs` に相乗り | ジョブ表はジョブの記録であって取得元の状態ではない。意味論を汚す |

---

## D2b: 憲法 V を DB 制約に落とす(無条件 `UNIQUE(race_id)`)

**Decision**: `chaos_snapshots` に**無条件の `UNIQUE(race_id)`** を張る。

**Rationale**: 「1 レースのオッズ入り行は生涯 1 行」は plan の憲法 V チェックが全面的に依拠する主張だが、
0012 の部分 unique index は `WHERE status='active'` なので **void 1 行 + active 1 行が通る**。
コードとテストだけの担保では「規約は安全境界ではない」(horizon-artifact §2)という
本 feature 自身の原則と矛盾する。**規約を DB 制約に格上げする。**

**Alternatives considered**: 主張を「コードとテストで担保する」に弱める
→ 憲法 V の担保としては後退であり、0 行の今なら無コストで入るので採らない。

---

## D2c: 重複行があるときは中断し、退避は migration の外で行う

**Decision**: 0013 は重複を検出したら**中断するだけ**。退避表 `chaos_snapshots_quarantine` の
作成と退避は**復旧 CLI(`db/__main__.py`)が migration の外で**行う。

**Rationale**:

- 084 の出荷済みコード(`chaos_capture.py:452`)は旧行を void して**新行を追記する**ので、
  出走構成の変化を経験した環境には ≥2 行/レースが実在しうる。**D2 の「追加のみ」は
  スキーマの話であって、データが常に無傷という意味ではない。**
- **黙って削除しない** — void 行は監査記録であり、086 の中心にある
  「観測を静かに失わない」規律に反する。
- **退避表を 0013 が作ってはならない** — Alembic の DDL は 1 トランザクションなので、
  中断すると**作った表もろとも巻き戻り退避先が消える**。
- 中断だけで終えると重複を持つ環境が**恒久的に upgrade 不能**になるので、
  決定的な復旧手順(`status='active'` を残す・無ければ最新 `captured_at`)を CLI として用意する。

---

## D3: ops / API / admin の契約追加

**Decision**:

1. **ops**: `run_predict` が `_serving_predict` の**直前**に `_live_capture_chaos(race_id)` を
   subprocess 実行する(既存の `_serving_predict` / `_betting_recommend` / `_live_refresh` と
   同じ `uv run --project live` パターン)。境界テストの禁止 import 一覧は変更しない。
2. **API**: `JobRow` に `summary: dict | None` と、その `capture` キーを写した
   **型付きの `capture: JobCaptureRow | None`** を純追加する(FR-020 は結果の別と理由が
   公開契約に型で現れることを要求する)。
3. **admin**: ジョブ履歴に捕捉チップを表示。見送りは**中立色**(FR-022)。

**Rationale**:

- 実コード確認: `ingestion_jobs.summary` は DB にあるが `JobRow`(api/schemas.py:583-600)に
  無く、`routers/jobs.py` も渡していない。**052 の一般的な盲点**なので、
  捕捉専用フィールドを足すより `summary` をそのまま露出する方が波及が小さく、
  かつ他ジョブ種の可観測性も同時に上がる。
- ops は**捕捉の実装を import しない**。`ops/tests/integration/test_boundary.py` の
  FORBIDDEN に `horseracing_live` が既に入っている(053 で追加済み)ので、
  subprocess 以外の選択肢は最初から無い。

**Alternatives considered**:

| 案 | 却下理由 |
|---|---|
| 捕捉を別ジョブとして先に enqueue | FR-013 が明示的に禁止。ワーカーは複数イテレーションで drain するので**順序が保証されない**(予測が先に走りうる) |
| `JobRow` に `capture_*` を**平坦に**足す | ジョブ種ごとに列が増え続ける。`summary` の転記 + 入れ子の `capture` なら 1 回で済む |
| API に捕捉トリガ用の POST を足す | API は全 path GET(FR-023)。破る理由がない |

---

## D4: プロセス跨ぎのレート制限と拒否後のクールダウン

**Decision**: DB の `fetch_throttle_state` を**予約 → 取得**の 2 相で使う。

列は `next_allowed_at`(**次に叩いてよい時刻**)である。「最後に叩いた時刻」ではない。

```text
[捕捉とは別セッションの短いトランザクション]
  SELECT ... FROM fetch_throttle_state WHERE domain=? FOR UPDATE
  blocked_until > now()  → 取得せず skip(reason=source_cooldown)
  wait = max(0, next_allowed_at - now())
  next_allowed_at = now() + wait + min_interval   -- 予約(未来へ進める)
  COMMIT
[ローカルで wait だけ待つ]
[取得]
拒否・制限の応答 → 別セッションで blocked_until = now() + cooldown を書いて COMMIT,
                  再試行しない
```

**Rationale**:

- 抑制状態のロックを**HTTP の外**に出すので、取得中にこの行のロックを握り続けない。
- 予約(`next_allowed_at` を未来に進める)なので、同時に来た 2 プロセスが
  同じ待ち時間を計算して同時に叩くことがない。
- **列名は `next_allowed_at`**。`last_attempt_at` という名前に未来時刻を入れると
  名前と意味が乖離し、後から読む人が「最後に叩いた時刻」として使ってしまう。
- **抑制の書き込みは捕捉トランザクションと別セッションで独立にコミットする**。
  捕捉トランザクションは per-race 排他のため取得中も開いており、
  そこに書くと拒否時の巻き戻しでクールダウンが消えて SC-009 が黙って失敗する。
- **拒否の識別に scrape 側の変更が要る**: 実コード確認で
  `HttpFetcher._fetch_with_backoff`(fetch.py:145-159)は**403/429 を含む全非 200 を
  同じ `FetchError` に潰して 3 回リトライする**。
  → `FetchRefused(FetchError)` を追加し、403/429 は**即座に送出して再試行しない**。
  これは全 scrape 利用者の挙動を変えるが、**より礼儀正しい方向**への変更であり、
  1 秒・2 秒のバックオフで 429 が解けることは無いので機能的損失もない。
  `scrape` の既存テストを**全件**回して回帰が無いことを確認する(着手時の実測 97 件)。

**Alternatives considered**: Redis / 専用プロセスによるトークンバケット
→ 新しい常駐要素を増やす。単一オペレータのローカル運用に対して過剰。

---

## D0: 1 レース 1 観測(計画レビューで設計を撤回した点)

**Decision**: **1 レースにつきオッズを含む凍結観測は生涯 1 件だけ**。再捕捉は行わない。
捕捉後に出走構成が変わったら、その行を**その場で無効化する**(撮り直さない)。

**当初の案(撤回)**: 「出走構成が変わるたびに差し替える」+「窓外→窓内は 1 回昇格」。
根拠は「各行は異なる出走構成に 1 対 1 対応するのでオッズ時系列ではない」だった。

**撤回の理由**(codex レビューで指摘・実コードで裏取り):

1. **論法が成立しない**。構成 A→B→C でも**生き残った馬のオッズは 3 時点分保存される**。
   分類の付け替えでは憲法 V(オッズ履歴を保存しない)との衝突は解けない。
2. **昇格案は自分の不変条件を自分で破っていた**。窓外→窓内の昇格は
   **出走構成が変わっていない**レースを 2 回撮る操作であり、
   「同一構成では再捕捉しない」という当の不変条件に正面から反する。
3. **決定打**: 前向き報告は `training/chaos_bands.py:1895-1896` で
   1 レースに複数行あるレースを `not_one_row_per_race` として
   **確認コホートから丸ごと除外する**。差し替えは、そのレースを捕捉した意味を消す。
4. 出走構成は**単調に縮むとは限らない**。出走表の取込は upsert なので
   訂正で A→B→A と戻りうる(`scrape/upsert.py`)。

**受け入れる損失**(正直に開示する):

- 窓外で捕捉されたレース(実測 4.4%)は確認適格にならないまま確定する
- 捕捉後に出走取消が入ったレースは荒れ度が出なくなる

これは「オッズ履歴を持たない」という憲法の要求と引き換えに払う代償である。
**憲法 V を改定しない限り、これ以外の設計はない。**

---

## D5: 取得前に判定を完了させる方法(FR-001・FR-002 の両立)

**問題**: FR-001 は「外部取得の**前**に有効な凍結観測の有無を判定せよ」と言い、
FR-002 は「有無ではなく**出走構成の一致**で判定せよ」と言う。
一見すると構成の比較には取得した結果が要るように見える。

**Decision**(D0 を受けて改訂): 取得前の判定は**行の存在**だけで済む。

```text
行が存在する(active でも void でも)→ 取得せず skip(already_captured)
行が存在しない                      → 適格判定を続ける
```

出走構成の比較は**捕捉の可否**ではなく**既存行の無効化**に使う:

```text
active 行の field 集合 != race_horses(started)の集合
  → その行を void(field_changed)にする。新しい行は作らない。取得もしない
```

さらに**取得の後・保存の前**にも同じ比較を行う(FR-003a)。
取得には数秒かかり、その間に出走取消が入ると古い構成を凍結してしまうため。
排他制御が守るのは同時捕捉であって、出走表の更新ではない。

**Rationale**: 084 の `_void_reason`(chaos_capture.py:375-378)が既にこの集合比較を
持っている。086 はその述語を「差し替えの引き金」ではなく
「無効化の引き金」として使う。オッズは比較に一切入らない。

---

## D6: 排他制御の置き方

**Decision**: `pg_try_advisory_xact_lock` を**適格判定の後・取得の前**に置き、
取得と書き込みを同じトランザクションで囲む。取れなければ `concurrent_capture` として skip。

**Rationale**:

- 実コード確認: 現在の `capture_chaos` は `acquire_fresh_capture`(取得)を終えてから
  `session.begin_nested()` 内で `pg_advisory_xact_lock` を取る(chaos_capture.py:416/436)。
  つまり**同時実行は二重に取得してから片方が捨てられる**。SC-008 が求めるのは取得 1 回。
- `try_` にすることで待ち行列を作らない。予測クリックの連打は待つ価値がないので、
  2 番目以降は即座に「別プロセスが捕捉中」として見送るのが正しい。
- ロック保持時間は取得予算(D7)で上限が決まる。トランザクションは READ COMMITTED なので、
  取得の後段の result-pending 再確認は**新しいスナップショットを読む**(検知が効く)。

---

## D7: 実時間の期限と外側の打ち切り(FR-018・FR-018a・FR-019)

**Decision**:

```text
外側(ops の subprocess timeout)  : 18 秒(プロセスグループごと終了・predict 経路のみ。
                                    SC-011 の 20 秒は起動と終了処理を含む実時間なので、
                                    `communicate()` の上限はそれより小さく取る)
内側(捕捉全体の実時間の期限・1 レースあたり・試行 1 回):
  `predict_manual` / `predict_auto` = 10 秒 /
  `daily_operational` / `explicit_command` = 30 秒
  (**契機に紐づける** — 操作者が 1 レースを手で流す `explicit_command` にも
  外側の打ち切りと遅延制約が無く、UI 由来の 10 秒を掛ける理由がない)
```

**経路で分ける**: 日次には外側の打ち切りも遅延制約も無く FR-012 の主たる観測源なので、
予測の体感を根拠にした 10 秒を掛けると主ソースが静かに欠測する。
FR-018 の不等式は外側が存在する predict 経路にのみ適用する。

**Rationale**:

- 実コード確認: 現状は `max_retries=3` × `httpx timeout=20.0` + backoff(1+2 秒)で
  **最悪 63 秒超**。どんな短い外側打ち切りも必ず内側が食い破る(FR-018 違反)。
- **足し算の見積もりでは不十分**(計画レビューで判明)。見積もりから漏れるもの:
  `HttpFetcher.get` は取得の前に **robots.txt を別途取得する**(`fetch.py:111`)/
  頻度制限の待機 / httpx の `timeout` は段階ごとの上限であって全体の上限ではない。
  → **単調時計による単一の期限**で全体を縛る。
- **当初案(内側 25 秒 / 外側 45 秒)を下げた**。予測本体は実測 55 秒なので
  45 秒の上乗せは**最大 82% 増**で「体感的に遅らせない」という性能目標と矛盾する。
  捕捉は補助機能であり、取り逃がしても次の予測実行が拾う。
  **1 回試行・10 秒**の方が単純で取得元にも優しい。
- **既存の 20 秒クライアントを実際に張り替える**必要がある
  (**解決は `make_capture_fetcher` の新設**=`_make_fetcher` は不変。
  同関数は 5 つの取込 CLI が共有しており、scrape のテストはネットワークを使わないので
  timeout を変えても赤くならない。正本は `contracts/fetch-politeness.md` §4)
  (CLI は `scrape/cli.py::_make_fetcher` 経由で `httpx.Client(timeout=20.0)` を作る)。
  新しい定数を足すだけでは実際の挙動は変わらない。
- **外側打ち切りは `outcome="unknown"` として記録する**(FR-019)。
  かつ `start_new_session=True` + `killpg` で**子孫まで終了させる**(FR-018a) —
  `uv run` は中間の起動プロセスなので、これだけ kill すると捕捉プロセスが生き残り、
  記録が `unknown` になった後で保存を完了させうる。
- **検証は静的な不等式では足りない**。遅い HTTP サーバ(robots.txt を含む)に対して
  実際の CLI を走らせ、実時間・排他の解放・子孫プロセスの不在を確認する。

---

## D8: 確認適格の判定をどこで行うか(遡及禁止の実現)

**Decision**: **保存しない。導出する。**

> **計画レビューで改訂**: 当初は「観測ごとに `readout.artifact_digest` から artifact を
> 解決し直す」仕組みを置き、窓を持たない遡及行には除外理由
> `legacy_artifact_without_horizon` を与えるつもりだった。
> **どちらも不要**と判明した — 前向き報告は
> `load_prospective_rows(..., artifact_digest=...)` で**単一の凍結設定に絞って**読む
> (`training/chaos_bands.py:1714`)ので、報告に入る観測の artifact は
> **常に報告対象の artifact そのもの**である。非遡及は構造から従い、
> 窓を持たない遡及行は新しい設定の報告に**そもそも現れない**。

```text
display_eligible(snapshot, started_now)
  = snapshot.status == "active"
    and snapshot.seconds_to_post is not None
    and frozen_entry_set(snapshot.field) == started_now      ← FR-002b

confirmation_eligible(snapshot, artifact, started_now)
  = display_eligible(snapshot, started_now)
    and snapshot.capture_strength == "confirmatory"
    and within(snapshot.seconds_to_post, artifact.preregistration.primary_horizon)
```

**Rationale**:

- artifact は content-addressed かつ create-only(084)。`chaos_readouts.artifact_digest` は
  **捕捉時に使われた artifact を指し続ける**。そこから窓を読めば、
  後から別の窓を持つ artifact を発行しても**過去の観測の適格性は動かない**(FR-007)。
- boolean を保存すると、artifact を差し替えたときに「保存値と導出値のどちらが正か」という
  新しい整合義務が生まれる。074/076 の content-addressed 規律と同じ理由で導出を選ぶ。

**置き場**: `probability/chaos_eligibility.py`(純関数)。
`api`(表示)/ `live`(捕捉)/ `training`(報告)の三者が使い、三者が共有できるのは
`db` と `probability` だけ。084 で `chaos_artifact.py` を `probability` に置いたのと同じ理由。

---

## D2a: 契機の語彙(計画レビューで分割した点)

**Decision**: `predict_job` の 1 値では足りない。**`predict_manual` と `predict_auto` に分ける**。

**Rationale**: `ops/runner.py::run_one` は出走表の取込後に**自動で** predict ジョブを積む
(`enqueue_predict`)。`enqueue_predict` は UI ボタン由来も自動追随も一律に
`summary.source = "manual"` と記録している(`ops/enqueue.py:116`)。
1 値にまとめると**自動追随の捕捉まで「利用者が選んだレース」として報告され**、
選択バイアスを過大に見積もる(SC-007 の解釈が壊れる)。
自動追随はデータ更新に紐づくので**中立**である。

遡及行のラベルは **`legacy_unknown`**。084 の CLI は `--date` と `--race-id` の
両方を許すので、既存行がどちらで取られたかを**区別する情報が残っていない**。
`daily_operational` を推測で付けると内訳と開示が静かに汚染される。

---

## D9: 主 horizon 欠落時の fail-closed をどこに置くか

**Decision**: **`load_chaos_artifact` の検証ステップに置く**(単一箇所)。
`preregistration.primary_horizon` が無い / 型が不正 / 下限>上限 なら型付きエラーで拒否する。

**Rationale**:

- 実コード確認: 現在の欠陥は `training/chaos_bands.py:1806-1816` の `_primary_horizon` で、
  キーが無ければ `{mode: "sole_active_confirmatory_snapshot_per_race", minimum_seconds_to_post: 0, maximum_seconds_to_post: None, artifact_field_present: False}` に**黙って落ちる**。
  ここだけ直すと、表示経路(`api`)と捕捉経路(`live`)は依然として窓なし artifact を受け入れる。
- ローダで拒否すれば**全経路が一度に fail-closed** になる。084 が
  `load_chaos_artifact` に 8 段の検証を集約したのと同じ設計。
- `_primary_horizon` は共有純関数(D8)に置き換え、フォールバック分岐そのものを削除する。

**副作用(tasks で潰す)**: `primary_horizon` を持たない既存の artifact fixture が
すべてローダで弾かれる。fixture 更新はタスクに明示的に積む。

**ブートストラップの循環を汎用の生読みで解かない**(計画レビューで是正):
窓を必須にすると v1 が読めなくなり、新 version の発行と新旧バイト一致検証が実行不能になる。
当初は「digest 検証のみの生読み + 呼び出し元 2 箇所の静的テスト」で解こうとしたが、
**静的テストは規約であって安全境界ではない**(未承認・不正な artifact を
`add-horizon` の入力にできてしまう)。
→ **単目的の `upgrade_legacy_artifact_horizon`** を置き、
既知の承認済み digest しか受け付けず、窓以外の検証はすべて通す。

---

## D10: 新 artifact version の発行方針

**Decision**: v1(`e782c255…`)は**書き換えない**。`primary_horizon` を足した
**新しい digest** を発行し、`config/chaos_bands_approved.json` に
`status="active"` で追加、v1 を `status="superseded"` に変える。

**現行の解決を `status="active"` に是正する**(計画レビューで判明した 084 の欠陥):
`load_current_chaos_artifact` は `approved[-1]` を現行としている
(`chaos_capture.py:541`)が、manifest は active を**先頭**・superseded を**末尾**に置いている。
つまり**現行のコードは superseded の artifact を読んでいる**。
`status` フィールドは manifest にあるのに一度も参照されていない。

- λ2 / λ3 / 五分位境界 / fit ハッシュ / `fit_through` / `valid_from` は**完全に同一**にする
  (荒れ度の値もバンドも変わらない = SC-010)。
- 差分は `preregistration.primary_horizon` の追加**のみ**。
- 現行の解決は **`status="active"` の唯一の項目**にする(是正後)。
  末尾追記に頼らない — 是正前の `approved[-1]` は superseded を掴んでいた。
- 旧 digest を参照する行があっても、前向き報告は**単一 digest スコープ**なので
  新しい設定の報告には現れない(D8)。計画時点で snapshot が 0 件だったことは
  **利便であって設計の前提ではない**。

**Alternatives considered**: v1 を書き換えて digest を変えない
→ content-addressed の意味が消える。084 の create-only 規約に正面から反する。

---

## 未解決事項

なし。Technical Context に NEEDS CLARIFICATION は残っていない。
