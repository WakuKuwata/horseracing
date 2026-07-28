# Contract: 実行順序・ジョブ記録・可観測性

**Feature**: 086 | **Spec**: FR-013, FR-014, FR-015, **FR-015a**, **FR-018a**, **FR-019**,
FR-020, FR-021, FR-022, FR-023 | **Research**: D3

---

## 1. 実行順序(FR-013)

捕捉は**予測処理の直前・同一の実行単位内**で走る。

```text
run_predict(session, job):
    1. capture = summary.get("capture")
    2. if capture is None:
           summary.capture = {"state":"started", "started_at":…, "outcome":"unknown"}
           COMMIT                                       ← FR-015
           run = True
       elif capture["state"] == "started" and capture.get("retried") is not True:
           capture["retried"] = True; COMMIT            ← 起動前クラッシュからの 1 回だけ回復
           run = True
       else:
           run = False                                  ← done / launched。二度と取得しない
    3. if run: _live_capture_chaos(race_id, on_launched=mark_launched) を subprocess 実行
           mark_launched = Popen の直後に呼ばれ、**別セッションで**
           summary.capture["state"] = "launched" を書いて COMMIT   ← FR-015
           (取得が実際に走った証拠。これが無いと取得中のクラッシュが
            "started" のまま残り、再試行が取得をもう一度発火させる)
    4. summary.capture = {"state":"done", "outcome":…, …} を書いて COMMIT
       (**終了 3 分岐では `capture` と `predict_origin` の両方をマージする**)
    5. _serving_predict(race_id) を subprocess 実行      ← 従来どおり
    6. 予測の結果で job.status を決める                  ← 捕捉は影響しない(FR-014)
```

**`on_launched` の継ぎ目は必須**である。これが無いと `Popen` と `communicate()` の間に
呼び出し側が入り込めず、`launched` を `Popen` の**前**に書くしかなくなる。
そうすると起動前クラッシュが `launched` として残り、3 状態が 2 状態に潰れて
FR-015 が要求する「取得前 / 取得後」の区別が失われる
(しかも T041 のテストは `started` → 1 回再試行しか見ないので**緑のまま通る**)。

**別ジョブを先に積む方式は不可**(FR-013 が明示的に禁止)。
ワーカーは複数イテレーションでキューを drain するため、
先に enqueue しても予測が先に走りうる。

### `--json` の適用範囲

`--json` は **`--race-id` 専用**とする。`--date` と併用したら**型付きエラーで拒否**する。

日次は 36 レースを順に処理するので、1 行 JSON という契約が成立しない。
JSONL にすると要約行(T037b が 2 分類を足す)との関係が曖昧になり、
呼び出し側が「何行来るか」を仮定することになる。
機械可読が必要になったらそのとき別途定める(いま決め打ちしない)。

---

### 状態機械(FR-015)

```text
(なし) ──印を書く──→ started ──subprocess を起動した──→ launched ──終わる──→ done{captured|skipped|failed|unknown}
           │                                              │
           │                                              └── launched のまま再実行された
           │                                                    → **再試行しない**(取得は実際に走った)
           ├── started のまま再実行された(起動前クラッシュ)
           │     → 1 回だけ再試行し、以後は再試行しない
           │
           └── 外側打ち切り → **done{unknown}**(FR-019)
```

**3 状態でなければ FR-015 を満たせない**。FR-015 は
「**取得を始める前**に落ちたのか、**始めた後**に落ちたのかを記録から区別できる」ことと、
後者を再実行しないことを MUST としている。2 状態(`started` / `done`)だと、
`communicate()` の途中でワーカーが死んだ記録は `started` のまま残り、
再試行が**外部取得をもう一度発火させる** — FR-015 の MUST NOT を踏む。

- `started` は予測を始める前にコミットする(印が無いと起動前クラッシュで捕捉機会が永久喪失)。
- **`launched` は `Popen` の直後・`communicate()` の前に、別セッションで独立コミットする**
  (抑制状態と同じ理由 — ワーカーのトランザクションに書くと、
  クラッシュ時の巻き戻しで印ごと消えて 2 状態と同じ穴に戻る)。
- 打ち切りは「起動前クラッシュ」とは別物なので `done{unknown}` に落とす
  (`launched` のまま残しても再試行はしないが、結果が出た事実を記録する)。

### 結果は全終了経路に残す(FR-015a)

`run_predict` の既存実装は終了時に **`job.summary = {...}` で全体を代入し直す**
(`ops/runner.py:213 / 216 / 228` の 3 分岐すべて)。
そのままだと**先に書いた捕捉の結果が消える**。
API・admin の単体テストは作り物の summary で緑になるので、
**本番の記録だけが静かに失われる**。

→ 3 分岐すべてで既存の `capture` キーを**マージする**。
検証は「作り物の summary を読む」のではなく
**`run_predict` を最後まで走らせてコミット済みのジョブ行を読む**。

---

## 2. 境界(FR-023)

ops は捕捉の実装を import しない。既存の subprocess パターンを踏襲する。

**期限の値の正本は `contracts/fetch-politeness.md` §4**(本節はその実装配置を定める)。
予測経路の**実効値は ops が argv で与える**(live 側の `predict_*` 既定は
フォールバックであり、ops の定数と一致させる — 一致を静的テストで固定する)。
live 側に既定値を持たせて argv を省略すると、`ops` の静的不等式テストが検証した値と
実際に使われる値が食い違い、FR-018 が黙って破れる。

### 自動追随の捕捉を止める運用スイッチ(FR-001c)

| 項目 | 値 |
|---|---|
| 環境変数 | `OPS_CAPTURE_ON_AUTO_PREDICT` |
| 既定 | **有効**(未設定なら捕捉する) |
| 無効にする値 | `0` / `false`(大小文字を問わない) |
| 無効時の挙動 | subprocess を起動せず、`summary.capture` に `{state:"done", outcome:"skipped", reason:"auto_capture_disabled"}` を書く |

**黙って何もしない実装にしない** — 記録が無いと、止めているのか壊れているのかが
運用画面から区別できない。`predict_auto` は**人の操作なしに外部取得が走る唯一の経路**なので、
取得元との関係が悪化したときに仕様変更なしで止められる必要がある。
手動の予測(`predict_manual`)はこのスイッチの対象外。

```python
_CAPTURE_TIMEOUT_S = 18        # 外側の打ち切り(SC-011 の 20 秒から起動分の余裕を引いた値)
_CAPTURE_DEADLINE_S = 10       # 予測経路の期限(値の正本は fetch-politeness §4)

def _live_capture_chaos(
    race_id: str, *, trigger: str,
    on_launched: Callable[[], None] | None = None,   # ← Popen の直後に呼ぶ(FR-015)
) -> subprocess.CompletedProcess:
    cmd = [
        "uv", "run", "--project", str(_LIVE_DIR), "python", "-m", "horseracing_live",
        "capture-chaos", "--race-id", race_id,
        "--trigger", trigger,                                # predict_manual | predict_auto
        "--json",
        "--capture-deadline-seconds", str(_CAPTURE_DEADLINE_S),   # ← 必ず渡す
        "--database-url", owner_database_url(),
    ]
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    # 子孫まで確実に終了させるため Popen を保持する(FR-018a)
    with subprocess.Popen(cmd, stdout=PIPE, stderr=PIPE, text=True,
                          cwd=str(_LIVE_DIR), env=env,
                          start_new_session=True) as proc:
        if on_launched is not None:
            on_launched()          # 別セッションで state="launched" を独立コミットする
        try:
            out, err = proc.communicate(timeout=_CAPTURE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()                    # ゾンビを残さない
            raise
```

**`subprocess.run` は使えない** — `run` は内部で `Popen` を隠蔽し、
呼び出し側に `pid` を渡さないまま**直接の子だけ**を kill する。
`os.killpg` を呼ぶには `Popen` を自分で保持する必要がある
(この点を見落とすと FR-018a はそもそも実装できない)。
`uv run` は中間の起動プロセスなので、実際の捕捉プロセスが生き残って
per-race の排他を握り続け、記録が `unknown` になった後で保存を完了させうる。
→ `start_new_session=True` で新しいプロセスグループにし、
打ち切り時は `os.killpg` で**グループ全体**に終了を送り、`wait()` で回収する。

### `trigger` は呼び出し元で決まる(FR-009)

**`predict_job` という単一の値を使ってはならない。**
`ops/runner.py::run_one` は出走表の取込後に**自動で** predict ジョブを積む
(`enqueue_predict`)。`enqueue_predict` は UI ボタン由来も自動追随も一律に
`summary.source = "manual"` と記録している(`ops/enqueue.py:116`)ので、
このままでは自動追随の捕捉まで「利用者が選んだレース」として報告され、
SC-007 の選択バイアスの解釈が壊れる。

→ `enqueue_predict` に由来を持たせ(`manual_ui` / `auto_after_refresh`)、
**`summary.predict_origin` として永続化**し、`run_predict` がそれを読んで
`predict_manual` / `predict_auto` を渡す。

**重複排除時の昇格規則**: `enqueue_predict` は進行中のジョブを再利用する
(`enqueue.py:104-112`・`_ACTIVE` は QUEUED と RUNNING の両方)。
- 再利用先が **`QUEUED`** なら由来を **`manual_ui` へ昇格させる**。**逆の降格はしない**。
- 再利用先が **`RUNNING`** なら**相乗りせず新しいジョブを積む**。
  既に走っているジョブは由来を読み終えて捕捉も起動済みで、
  `capture_trigger` は `predict_auto` のまま確定して**後から貼り替えられない**。
  代償として同一レースで予測が二重に走る(実測 55-113 秒 × 2)。
  追随する recommend は `enqueue_recommend` の in-flight dedup が吸収する。
  **028/044 の重複排除の意味論を手動クリックに限って緩める**判断であり、
  `enqueue_predict` の docstring(`ops/enqueue.py:95-96` の 028 契約文)も更新する。

`run_predict` の終了 3 分岐は `job.summary` を全体代入するので、
**`predict_origin` も `capture` と同様に全分岐でマージする**。

`ops/tests/integration/test_boundary.py` の FORBIDDEN 一覧は**変更しない**
(`horseracing_live` は 053 で既に入っている)。

---

## 3. live CLI の追加(単一レースの機械可読出力)

```text
live capture-chaos (--race-id <id> | --date <d>) [--trigger <t>] [--json]
                   [--min-seconds-to-post N] [--capture-deadline-seconds N]
                   [--allow-outside-horizon]
```

| 引数 | 既定 | 説明 |
|---|---|---|
| `--trigger` | 経路ごとに別(下記) | `daily_operational` / `predict_manual` / `predict_auto` / `explicit_command` |
| `--json` | off | 1 行の JSON を stdout に出す(人間向け要約の代わり) |
| `--min-seconds-to-post` | 0 | **084 からの既存引数**。**運用下限**であって主 horizon の下限とは別物(FR-004a)。残り時間がこれ未満なら取得前に見送る |
| `--allow-outside-horizon`(**`--race-id` と併用したら型付きエラー** — `--date` 経路専用) | off | `--date` が窓の上限より先のときだけ意味を持つ。**既定では拒否**し、この引数があるときだけ実行する(FR-001b1 — 1 レース生涯 1 観測なので、上限より先で流すとその開催日の全レースが窓外で枠を使い切り確認コホートから恒久的に脱落する) |
| `--capture-deadline-seconds` | **契機ごとに別**(`predict_*` 10 / `daily_operational`・`explicit_command` 30) | 捕捉全体の実時間の期限。**predict 経路では ops が必ず明示的に渡す**(既定値に頼らない・§2)。日次は外側の打ち切りも遅延制約も無く FR-012 の主たる観測源なので、予測の体感を根拠にした 10 秒を掛けない |

`--trigger` と `--capture-deadline-seconds` の既定は **`--trigger` が経路ごと**(`--date`→`daily_operational` / `--race-id`→`explicit_command`)、
**`--capture-deadline-seconds` は `deadline_for(trigger)`**(契機に紐づく・§4 が正本):
`--date` なら `daily_operational` / 30 秒、`--race-id` なら `explicit_command` / **30 秒**。
argparse の単一の既定値で済ませると片方が静かに誤ラベルになるので、
**両方の経路を引数無しで実行して、契機と実効期限の両方を確認するテスト**を置く。
**既存の人間向け出力は不変**(`--json` を付けたときだけ切り替わる)= SC-010。

**FR-001b1 の日次拒否だけは非 0 で終わる**(1 レース単位の見送りではなく操作者のミスなので、
シェルから検知できる必要がある)。それ以外の
終了コードは**捕捉の成否を表さない**。見送りも正常終了(0)である
(呼び出し側は JSON の `outcome` を読む)。**FR-001b1 の対象日拒否のときだけ非 0**。

```jsonc
{"race_id":"…","outcome":"captured","reason":"ok","capture_strength":"confirmatory",
 "confirmation_eligible":true,"seconds_to_post":24124,
 "chaos_snapshot_id":"…","elapsed_s":4.2}
```

`race_id` は呼び出し側が既に知っているので**記録には入れない**(照合用のエコー)。
`summary.capture` に着地するのは `elapsed_s` を除く残りのキー
(`elapsed_s` は**射影しない** — `started_at` / `retried` と同じ意図的な非対称。
テストのための計測値であって公開契約に出すものではない)である。

---

## 4. ジョブ記録(FR-020)

`predict` ジョブの `summary.capture` に構造化して残す(data-model.md §5)。
`ingestion_jobs` のスキーマは変更しない(JSONB の中身のみ)。

| `outcome` | 意味 |
|---|---|
| `captured` | 凍結観測を保存した |
| `skipped` | 適格でないので見送った(**正常**) |
| `rejected` | 取得したが凍結できなかった |
| `failed`(**`fetch_failed` を含む** — 403/429 以外の取得失敗。`capture-eligibility` §4 では 表示区分 = 取得不可) | 取得に失敗した |
| `unknown` | 外側打ち切り(FR-019) |

---

## 5. API(純追加)

```python
class JobRow(BaseModel):
    ...
    summary: dict | None = None            # 生の転記
    capture: JobCaptureRow | None = None   # summary["capture"] の機械的な射影
```

`JobCaptureRow` は `outcome` を列挙型、**`reason` は開いた文字列かつ省略可**にする。
閉じた列挙にすると「未知の理由をそのまま出す」(§6)が満たせず、
**必須にすると `state="started"` の記録(理由がまだ無い)で `GET /jobs` が 500 になる** —
予測は 55-113 秒走るので、その間に一覧を叩けば必ず踏む。
形は data-model §6 が正本。

- 既存フィールドの型・名前・順序は不変
- API は値を**解釈も再計算もしない**。`capture` は `summary["capture"]` の
  **キーを写すだけの射影**であり、解釈には当たらない(021 規律は保たれる)
- OpenAPI は純追加(削除ゼロ)。`front/openapi.json` と `admin/openapi.json` を再生成し、
  両者のバイト一致テストを緑に保つ。**型は front / admin の両方**を再生成してコミットする
  (`check-openapi.sh` は committed の型と diff するので片方だけだと落ちる)
- 読み取り専用は不変(全 path GET)

---

## 6. admin 表示(FR-021・FR-022)

ジョブ履歴の行に捕捉チップを出す。

| `outcome` | 表示 | 色 |
|---|---|---|
| `captured` | 「荒れ度を凍結」 | 中立(強調しない) |
| `skipped`(適格性由来: 発走済み / 確定済み / 捕捉済み など) | 「捕捉見送り(理由)」 | **中立(グレー)** |
| `skipped`(**取得不可**) — **完全な列挙は `capture-eligibility` §4 の `表示区分` 列が正本**(ここに写すと必ずずれる。現時点で本表 9 値 + ops 由来 `outer_timeout` = 10 値)。**`fetch_failed` は `status=failed` なので上の `failed` 行に入るが、色は同じ「注意」** | 「捕捉できず(理由)」 | **注意** — 適格性由来の見送りと**同じ色にしない** |
| **`state="launched"` が外側の打ち切り(18 秒)より古い** | 「捕捉不明」 | **注意** — ワーカーが `communicate()` の途中で死んだ記録。T041 は再取得しないので、放置すると永久に「捕捉中」と表示される(§6 が防ぎたい failure mode の裏返し) |
| **`state="started"` / `"launched"`**(捕捉が進行中) | 「捕捉中」 | **中立** — `outcome` は `unknown` だが**まだ結果が出ていないだけ**。注意色にすると、捕捉が走っている 18 秒間ずっと警告に見える(FR-022 が禁じる「正常が異常に見える」状態) |
| `rejected` / `failed` / `unknown`(`state="done"`) | 「捕捉できず(理由)」 | 注意(ただし**行全体の成否表示は変えない**) |

**適格性由来の見送りと、取れなかった見送りを同じ色にしてはならない**。
同色にすると、予算不足で**全捕捉が静かに落ちている**状態が「正常」に見え、
SC-001 が未達のまま気づけない。

**禁止**:

- 捕捉の失敗でジョブ行を失敗として描く(FR-021)
- 見送りをエラー色・警告アイコンで描く(FR-022 — 実測で予測実行の 45% は発走後で見送りになる)

理由文字列は**日本語ラベルに写像**する(`chaosLabels.ts` と同じ単一対応表の方式)。
未知の理由は生の文字列をそのまま出す(握りつぶさない)。

---

## 7. 検証

| 検証 | 期待 |
|---|---|
| 捕捉が例外・非 0 終了 | 予測は実行され job は SUCCEEDED(INV-6) |
| 捕捉と予測の順序 | 呼び出し順のスパイで capture → predict |
| **JSON → `summary.capture` の着地(全 3 終了経路)** | `run_predict` を最後まで走らせ、**コミット済みのジョブ行**に `outcome` / `reason` / `capture_strength` / `confirmation_eligible` / `seconds_to_post` / `chaos_snapshot_id` が残る。FAILED・SKIPPED・SUCCEEDED の**すべての分岐**で確認する(FR-015a) |
| **JSON が壊れている / 空** | `outcome="unknown"` にして**予測は続行する** |
| **印を書いた直後にクラッシュ** | 再実行で捕捉が **1 回だけ**走る(永久に飛ばされない・FR-015) |
| **結果が確定した後の再実行** | 捕捉の subprocess が **0 回** |
| 期限の受け渡し | argv に `--capture-deadline-seconds` が載っている |
| **UI ボタン由来 vs 自動追随** | 前者が `predict_manual`・後者が `predict_auto` として保存される(両方の enqueue 経路を実行して確認・FR-009) |
| **打ち切り時の子孫プロセス** | プロセスグループごと終了し、打ち切り後に遅れて行が増えない(FR-018a) |
| ジョブ再試行 | 2 回目は捕捉の subprocess が **0 回**呼ばれる |
| 外側打ち切り | `summary.capture.outcome == "unknown"` |
| API | `summary` が転記される・既存フィールドが不変 |
| admin | `skipped` 行にエラー色のクラスが付かない |
| OpenAPI | front / admin の `pnpm check:openapi` が緑・snapshot がバイト一致・**両方の `schema.d.ts` がコミットされている** |
