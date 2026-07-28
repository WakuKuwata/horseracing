# Contract: 捕捉の適格判定と差し替え

**Feature**: 086 | **Spec**: FR-001, FR-001a, FR-002, FR-002a, **FR-002b**, FR-003, FR-003a,
FR-004, FR-004a, FR-007, FR-008

---

## 1. 判定の順序(規範)

> **この節が判定順序の正本である**。tasks / research / CLAUDE.md の順序記述は
> すべてここへの参照であり、順序を変えるときはここを先に直す。

**外部取得より前に**すべての判定を終える。1 つでも外れたら型付きの見送りを返し、
**取得も保存も行わない**。

```text
1. race_id が妥当 / Race が存在 / race_date がある      → 否なら rejected
2. 出走表が揃っている                                   → 否なら rejected(entries_incomplete)
3. 結果が未確定                                         → 否なら skipped(result_settled)
4. post_time が既知                                     → 否なら skipped(post_time_unknown)   ← FR-004
5. now < post_time                                      → 否なら skipped(post_time_elapsed)   ← FR-001
6. 残り時間 >= min_seconds_to_post(運用引数)           → 否なら skipped(min_seconds_to_post)
6b. **主 horizon の artifact をロード**(手順 7 が窓を要るため)  → 読めなければ rejected(artifact_unavailable)
7. **契機が自動追随の予測なら、窓内であること**            → 窓外なら skipped(outside_primary_horizon) ← FR-001b
8. このレースの凍結観測が**一度も存在しない**(下記 §2)  → あれば skipped
8b. 出走頭数が足りている(4 頭以上)                    → 否なら **skipped**(no_started_horses / field_too_small)
9. 排他制御を取得できる                                  → 否なら skipped(concurrent_capture)  ← FR-003
10. 取得抑制の状態(fetch-politeness.md §2)             → 抑制中なら skipped(source_cooldown)
                                                          待ちが上限を超えるなら skipped(throttle_backlog)
10b. **残り時間の確認**(単調時計の単一期限・FR-018)     → 尽きていれば skipped(deadline_exceeded)
--- ここまで外部取得なし ---
11. 取得
11b. **残り時間の確認**(解析の前)                       → 尽きていれば skipped(deadline_exceeded)
12. **結果未確定の再確認**(`pending_after`)              → 否なら skipped(result_settled_during_fetch)
                                                          ※ これが `capture_strength` を決める
13. **発走時刻の再確認**(発走してしまっていないか)      → 発走済みなら skipped(post_time_elapsed_during_fetch)
14. **運用下限の再判定**(取得に数秒かかるため)          → 割ったら skipped(min_seconds_to_post_during_fetch)
15. **出走構成の再確認**(取得中に変わっていないか)      → 変わっていれば skipped ← FR-003a
15b. **残り時間の確認**(保存の前)                       → 尽きていれば skipped(deadline_exceeded)
16. 凍結 → 保存
```

**5・6・9 の順序が本 feature の核**である。084 は 5 と 6 を取得の**後**に判定し、
9(排他制御)も取得の後に取っていた(`chaos_capture.py:274` の `fetch_win_odds` → `:297` post_time →
`:302` min_seconds_to_post → `:436` advisory lock)。
実測で予測クリックの 45% が発走後なので、順序を直すだけで無駄な取得が半減する。

**6 は日次運用の経路にも効く**。`--min-seconds-to-post` を指定した日次捕捉は、
現状「取得してから残り時間で捨てる」ので、開催日に窓外のレースを叩き続けている。

**主 horizon は判定に入らない**(FR-001a)— ただし**自動追随の予測だけは例外**で、
窓外なら見送る(FR-001b)。この経路には画面で待つ利用者がいないので窓外を保存する
利得が無い一方、「1 レース生涯 1 観測」により**唯一の枠を消費して確認適格を永久に奪う**。
それ以外の契機では、窓は**確認適格の定義**であって捕捉の可否ではない。

**日次運用には別のガードを置く**(FR-001b1): その日の**最も遅い発走**までの残り時間が
窓の上限を超えるなら**既定で拒否する**(コマンド開始時刻を基準に**日単位で 1 回**判定。
レース単位にすると同じ日の中で部分的に枠を焼く)。1 レース生涯 1 観測なので、2 日前に流すと
その開催日の全レースが窓外で枠を使い切り、確認コホートから恒久的に脱落する。
日次は「主たる観測源」なので、操作ミス 1 回でそれを失うのを黙認しない。

---

<!-- FR-002 の根拠(なぜ 1 レース生涯 1 観測なのか)の正本は本節である。
     spec / plan / data-model / research D0 は本節を参照するだけにする。 -->

## 2. 一度きりの捕捉(FR-002)と無効化(FR-002a)

**1 レースにつきオッズを含む行は生涯ちょうど 1 行**である。再捕捉は理由を問わず行わない。

```text
row = そのレースの chaos_snapshots(status を問わず)

if row exists:
    now = {(horse_id, horse_number)} from race_horses where entry_status = started
    if row.status == 'active' and row の field 集合 != now:
        UPDATE chaos_snapshots SET status='void', void_reason='field_changed'
        （任意の最適化。新しい行は作らない）
    → 見送る(reason = 'already_captured')   ← status が active でも void でも同じ
```

### 不一致の判定は読み取りのたびに行う(FR-002b)

上の無効化は**次に捕捉を試みたときにしか走らない**。取消の後に誰も予測を再実行しなければ、
古い構成の観測がそのまま表示され続ける。
したがって**正しさの担保は読み取り時の判定**であり、無効化の記録は最適化にすぎない。

```python
def display_eligible(snapshot, started_now) -> bool:
    """発走前に取られ、かつ凍結構成が現況と一致する観測だけが表示に使える。"""
    if snapshot.status != "active" or snapshot.seconds_to_post is None:
        return False
    return frozen_entry_set(snapshot.field) == started_now
```

- **表示**(api): 一致しなければ荒れ度を出さない。理由は **`field_changed_after_capture`**。
  **行が `void(field_changed)` に落ちた後も同じ理由を返す** — `active` 行が無いからといって
  `no_snapshot` にすると「観測はあるのに無い」と報告することになり、
  同じレースの説明が「誰かが予測を押したか」で揺れる(1 レース 1 行なので追加クエリは不要)
- **確認コホート**(前向き報告): 一致しなければ除外理由 `field_changed_after_capture` で数える。
  **`void(field_changed)` の行も同じ理由に数える**(`snapshot_not_active` に混ぜない)

報告は結果確定後に走るので、そのときの `race_horses` は最終的な取消を反映している。
この 1 箇所の比較で取り逃がしがなくなる。

### なぜ撮り直さないのか(憲法 V)

計画中に検討して**却下した 2 案**:

| 却下案 | 却下理由 |
|---|---|
| 窓外で捕捉 → 後に窓内で撮り直して昇格 | **出走構成が同じレースを 2 回撮ることになる**。生き残った全馬のオッズが 2 時点分保存され、これは憲法 V が禁じるオッズ履歴そのもの |
| 出走構成が変わるたびに撮り直す | 「構成が違えば時系列ではない」という論法は成立しない。構成 A→B→C でも**生き残った馬のオッズは 3 時点分保存される**。分類の付け替えでは V との衝突は解けない |

さらに実装上の決定打として、前向き報告は
`training/chaos_bands.py:1895-1896` で複数行のレースを
**確認コホートから丸ごと除外する**。撮り直しは、そのレースを捕捉した意味そのものを消す。

**受け入れる損失**: 窓外で捕捉されたレース(実測 4.4%)は確認適格にならないまま確定する。
出走取消が入ったレースは荒れ度が出なくなる。
これは「オッズ履歴を持たない」という憲法の要求と引き換えに払う正直な代償である。

### 失効は単調である(A→B→A への対処)

不一致の判定は**現況との集合一致**という状態の述語である(FR-002a)。
ただし**一度不一致になった観測は、構成が元に戻っても復活させない**(FR-002a1)。
`void(field_changed)` の印は**単調な失効**として扱い、読み取り時の判定と AND を取る:

```python
usable = snapshot.status == "active" and frozen_set == started_now
```

復活を許すと、B の時点で誰かが予測を押したかどうかで適格性が変わり、
**同じレースの適格性が観測不能な要因で揺れる**(SC-005 が主張する
「記録の有無を問わない」と正面から矛盾する)。

### 出走構成が単調に縮むとは限らない

出走表の取込は `entry_status` を upsert するので、取込元の訂正で
**A → B → A** と戻りうる(`scrape/upsert.py`)。
「構成は減るだけ」という前提に依存した設計は取らない — 一度きりの捕捉ならこの問題は生じない。

---

### 遡及行の例外

084 由来の `void_reason='recaptured'` / `'late_scratch'` の行は 086 では生成されず、
**遡及行にしか存在しない**。表示経路はこれらを `no_snapshot` として扱う
(`field_changed_after_capture` は「現況と一致しない観測がある」という意味であり、
撮り直しや取消で無効化された旧行にはあてはまらない)。

---

## 3. 適格性の導出(保存しない)

```python
def confirmation_eligible(snapshot, artifact, started_now) -> bool:
    """確認コホートに入るのは窓内の確認可能な観測だけ。"""
    if not display_eligible(snapshot, started_now):
        return False
    if snapshot.capture_strength != "confirmatory":
        return False
    return within_primary_horizon(
        snapshot.seconds_to_post,
        artifact.preregistration["primary_horizon"],
    )
```

**非遡及(FR-007)は報告の構造から従う**: 前向き報告は
`load_prospective_rows(..., artifact_digest=...)` で**単一の凍結設定に絞って**読む
(`training/chaos_bands.py:1714`)。
つまり報告に入る観測の artifact は**常に報告対象の artifact そのもの**であり、
新しい窓を持つ設定を発行しても、その報告に古い観測は入らない。
**観測ごとに artifact を解決し直す仕組みは要らない** — 構造的に保証されている。

同じ理由で、窓を持たない旧設定を指す観測(086 以前の遡及行)は
**新しい設定の報告に現れない**。特別な除外理由も要らない。

```python
def within_primary_horizon(seconds_to_post: int, horizon: Mapping) -> bool:
    lo = int(horizon["minimum_seconds_to_post"])
    hi = int(horizon["maximum_seconds_to_post"])   # None はローダが既に拒否している
    return lo <= seconds_to_post <= hi
```

境界は**両端を含む**(`>=` / `<=`)。
`maximum_seconds_to_post` の `None` 分岐は**置かない** —
「上限なし」は暗黙の全時刻受理と等価であり、ローダが読み込み時点で拒否する
(horizon-artifact.md §1)。ここで防御的に受けると「上限なしも一応合法」という
矛盾したメッセージになる。

**置き場**: `probability/src/horseracing_probability/chaos_eligibility.py`(純関数・DB 非依存)。
`api`(表示の可否)/ `live`(捕捉)/ `training`(確認コホート)の三者が import する。
`started_now` は呼び出し側が DB から渡す(純関数は DB を触らない)。

---

## 4. 見送り理由の語彙(安定・機械可読)

`live/src/horseracing_live/chaos_capture.py` の `ChaosCaptureRejected` 呼び出し箇所を
網羅した表である。**実行層(ops)が付ける理由は下の別節にある** — 分類の完全集合は
「本表 ∪ ops 由来」である。

**`表示区分`** 列は運用画面と日次要約の色分けに使う(job-observability §6・FR-022)。
`適格` = 適格性由来の見送り(中立色)/ `取得不可` = 取りに行けなかった(注意色)/
**`—` = rejected 群**(そもそも捕捉の対象になっていない異常。`outcome` で分類され、
表示区分の対象外)。
**T049 の静的な集合一致テストは `適格` と `取得不可` だけで組む** —
`—` の行を混ぜると期待集合が曖昧になる。

| reason | status | 外部取得 | 表示区分 | 084 での status | 意味 |
|---|---|---|---|---|---|
| `already_captured` | skipped | なし | 適格 | (新規) | このレースは既に捕捉済み(有効・無効を問わない) |
| `field_changed_during_fetch` | skipped | **あり** | 取得不可 | (新規) | 取得中に出走構成が変わった(FR-003a)。保存しない |
| `robots_disallowed` | skipped | **robots のみ** | 取得不可 | (新規) | 取得元の robots.txt が拒否した。**クールダウンは書かない**(FR-016 の対象は 403/429)ので毎回 robots.txt は叩く。`fetch_failed` と混ぜない — 取得元の障害ではなく方針である |
| `fetch_failed` | **failed** | **あり** | 取得不可 | (新規) | 取得元が 403/429 以外で失敗した(非 200・接続断・解析失敗)。**外側の打ち切りではない**ので `unknown` にしない — 恒常的な取得元障害が「たまたま遅かった」に化けると原因が追えない |
| `result_settled_during_fetch` | skipped | **あり** | 取得不可 | rejected(`result_settled` と同一) | 取得中に結果が確定した。**取得前の `result_settled` と同じ理由にしない** — 「取得なし・適格」と分類されて、実際は取得した異常が正常に見える |
| `throttle_backlog` | skipped | なし | 取得不可 | (新規) | 頻度制限の待機が上限を超える混雑(FR-017b) |
| `outside_primary_horizon` | skipped | なし | 適格 | (新規) | **自動追随の予測**が窓外だった(FR-001b)。他の契機では見送り理由にならない |
| `deadline_exceeded` | skipped | **あり得る** | 取得不可 | (新規) | 捕捉全体の実時間の期限を使い切った(FR-018) |
| `result_settled` | skipped | なし | 適格 | **rejected** | 結果が確定済み |
| `post_time_unknown` | skipped | なし | 適格 | (新規) | 発走時刻が不明(FR-004) |
| `post_time_elapsed` | skipped | なし | 適格 | **rejected** | 発走済み |
| `ok` | captured | **あり** | 適格 | (新規の表記) | 捕捉に成功した(`outcome="captured"` に対応。理由は情報として `ok`) |
| `min_seconds_to_post` | skipped | なし | 適格 | skipped(取得後) | 残り時間が運用下限未満(取得前・手順 6) |
| `post_time_elapsed_during_fetch` | skipped | **あり** | 取得不可 | (新規) | 取得中に発走した |
| `min_seconds_to_post_during_fetch` | skipped | **あり** | 取得不可 | (新規) | 取得中に運用下限を割った |
| `concurrent_capture` | skipped | なし | 適格 | (新規) | 別プロセスが同一レースを捕捉中 |
| `source_cooldown` | skipped | なし | 取得不可 | (新規) | 取得元が抑制中 |
| `artifact_unavailable` | rejected | なし | — | (新規) | 主 horizon の artifact が読めない(fail-closed)。取得はしない |
| `entries_incomplete` | rejected | なし | — | rejected | 出走表が揃っていない |
| `invalid_race_id` / `race_not_found` / `race_date_unknown` | rejected | なし | — | rejected | 084 既存 |
| `invalid_post_time` | rejected | なし | — | rejected | `post_time` が tz-naive |
| `no_started_horses` / `field_too_small` | **skipped** | なし | **適格** | rejected | 凍結対象の頭数が足りない。**084 の `rejected` から再分類する** — 少頭数レースは日常的に存在する正常な見送りであり、`rejected`(=注意色)にすると FR-022 の「適格性由来の見送りは正常に読める」に反する(SC-010 の例外 1 と同型) |
| `invalid_capture_time` | rejected | **あり** | — | rejected | 捕捉時刻が tz-naive |
| `source_unavailable` | rejected | **あり** | — | rejected | 取得元名が空 |
| `partial_market_odds` / `invalid_popularity_ranks` | rejected | **あり** | — | rejected | 取得後に凍結できないと判明 |

### ops 由来の理由(`capture_chaos` は生成しない)

次の 2 つは**実行層が付ける**理由で、`ChaosCaptureRejected` の呼び出し箇所には現れない。
admin の分類集合と突き合わせるときは**この節も含める**。

| reason | outcome | 表示区分 | 意味 |
|---|---|---|---|
| `auto_capture_disabled` | skipped | 適格(中立) | 運用スイッチで自動追随の捕捉を止めている(FR-001c) |
| `outer_timeout` | unknown | 取得不可(注意) | 外側の打ち切り(FR-019)。成功とも失敗とも決めつけない |
| `launch_failed` | failed | 取得不可(注意) | 捕捉プロセスの**起動自体**が失敗した(`uv` 不在など・T038)。`fetch_failed`(取得元の障害)と混ぜない — こちらは手元の実行環境の問題である |

**適格性由来の `skipped` は正常な結果**である。運用画面で警告色にしてはならない(FR-022)。
**`表示区分 = 取得不可` は別区分**として示す — 本表の 9 値 + ops 由来の `outer_timeout` / `launch_failed` = **11 値**(job-observability §6)。

**未知の理由の既定は「取得不可」(注意色)とする** — 本表に無い理由文字列が来たら、
適格性由来(中立)ではなく取得不可に寄せる。
逆にすると、新しい失敗理由が増えたときに
**取得元の障害が「正常な見送り」として黙って埋もれる**(FR-022 / SC-001 が守りたいもの)。
admin は生の文字列を表示しつつ注意色にする。

**規則**: 取得の**後**に行う再確認で落ちたものは、取得の**前**の同名判定と
**必ず別の理由**にする(接尾辞 `_during_fetch`)。同じ理由にまとめると
「取得なし・適格」と分類され、**実際は外部取得を 1 本無駄にした異常が正常に見える**。
現時点で 4 つある: `result_settled_during_fetch` / `post_time_elapsed_during_fetch` /
`min_seconds_to_post_during_fetch` / `field_changed_during_fetch`。

**「外部取得」列は §1 の順序どおりに実装した後の姿である**。084 では
`no_started_horses` / `field_too_small`(`chaos_capture.py:195,198` — `build_frozen_field` の中)は
**取得の後**にあった。§1 の手順 8b がこれを前段へ移す。
(`invalid_post_time` / `invalid_capture_time` は tz-naive の入力検証であって
適格判定ではない。086 は位置を変えないので、移設の対象に数えない。)
移し忘れると「理由は正しいが取得している」状態が残るので、
各判定に `spy.calls == 0` テストを置く(§5)。

### 084 からの status 再分類(SC-010 の明示的な例外)

`result_settled` / `post_time_elapsed` / `no_started_horses` / `field_too_small` は
084 では `ChaosCaptureRejected` の既定 `status="rejected"` で送出されていた
(`chaos_capture.py:128` の既定値)。
086 では**適格でないだけで異常ではない**ので `skipped` に再分類する。

**影響**: 日次 CLI の要約行(`capture-chaos races=… captured=… skipped=… rejected=…`)の
内訳が変わる。これは SC-010「084 の既存出力が不変」に対する**意図した例外**であり、
084 の既存テストを更新する(tasks の該当タスクを参照)。
理由: 実測で予測クリックの 45% が発走後であり、少頭数レースも日常的に存在する。これらを `rejected` と数えると
運用画面が常時「失敗だらけ」に見えてしまう(FR-022 と正面から衝突する)。

---

### 紛らわしい 3 つの名前(層が違う)

| 名前 | 層 | 意味 |
|---|---|---|
| `field_changed` | DB の `void_reason` | 捕捉後に構成が変わって無効化された行の印 |
| `field_changed_during_fetch` | 捕捉の見送り理由 | 取得中に構成が変わったので保存しなかった(FR-003a) |
| `field_changed_after_capture` | 表示の不可用理由 / 報告の除外理由 | 保存済みの観測が現況と一致しないので使えない(FR-002b) |

---

## 5. 検証(誤った理由で通るテストを禁じる)

各判定について、**取得回数を数えるスパイ fetcher** で `calls == 0` を assert する。
理由文字列の一致だけを見るテストは、判定順序が逆でも通ってしまうため**不十分**。

| 検証 | 期待 |
|---|---|
| **適格レース(正常系)** | `captured` かつ **1 回**・`active` 1 行 + readout 1 行 |
| 発走済みレース | `skipped/post_time_elapsed` かつ **fetch 呼び出し 0 回** |
| 結果確定済み | `skipped/result_settled` かつ **0 回** |
| post_time 不明 | `skipped/post_time_unknown` かつ **0 回** |
| 残り時間 < 運用下限 | `skipped/min_seconds_to_post` かつ **0 回** |
| 捕捉済み(active) | `skipped/already_captured` かつ **0 回** |
| **捕捉済み(void)** | `skipped/already_captured` かつ **0 回**(無効化後も撮り直さない) |
| 捕捉後に取消発生 | 行が `void/field_changed` になり **取得 0 回**・行数は 1 のまま |
| **取消 → さらに取消** | 追加の取得も追加の行も**一切ない** |
| **A→B→A の構成戻り** | 追加の取得も追加の行も**一切ない**(単調縮小に依存しない) |
| **窓外での捕捉** | `captured` かつ **1 回**・`confirmation_eligible == false`(FR-001a) |
| **窓外で捕捉した後に窓内で再実行** | `skipped/already_captured` かつ **0 回**・**オッズ入りの行は 1 行のまま** |
| **取得中に構成が変わる** | `skipped/field_changed_during_fetch`・**保存されない**(FR-003a) |
| **取得中に結果が確定する** | `skipped/result_settled_during_fetch`・**保存されない** |
| **取得中に発走する** | `skipped/post_time_elapsed_during_fetch`・**保存されない** |
| **取得中に運用下限を割る** | `skipped/min_seconds_to_post_during_fetch`・**保存されない** |
| 同一レース同時 2 プロセス | 合計 **1 回**・`active` 1 行 |
| **どのシナリオでも** | 1 レースのオッズ入り行数が**生涯 1 を超えない**(憲法 V) |
