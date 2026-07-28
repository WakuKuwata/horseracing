# Data Model: 予測実行時の荒れ度スナップショット捕捉 (086)

**Date**: 2026-07-27 | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md)

migration **0013**(head 0012 → 0013)。**追加のみ**。既存の列・型・index・trigger は不変。

---

## 1. `chaos_snapshots`(084 で導入・本 feature で 2 列追加)

| 列 | 型 | Null | 説明 |
|---|---|---|---|
| … | | | 084 の既存列は**すべて不変** |
| `capture_trigger` | `TEXT` | NOT NULL | 捕捉の契機(下記 5 値) |
| `capture_policy_version` | `TEXT` | NOT NULL | 捕捉規則の版。本 feature は `capture_policy_v1` |

| 値 | 意味 | 選択バイアス |
|---|---|---|
| `daily_operational` | 開催日の一括捕捉(`--date`) | なし |
| `predict_manual` | 画面の予測ボタン | **あり** |
| `predict_auto` | データ更新の後に自動で積まれた予測 | なし |
| `explicit_command` | 単一レース指定の一回限りの実行(`--race-id`) | あり |
| `legacy_unknown` | 086 以前に取られた観測(区別する情報が残っていない) | 不明 |

**`predict_manual` と `predict_auto` を分ける理由**: `ops/runner.py::run_one` は
出走表の取込後に**自動で** predict ジョブを積む(`enqueue_predict`)。
`enqueue_predict` は UI ボタン由来も自動追随も一律に `source="manual"` と記録しているので、
このままだと**自動追随の捕捉まで「利用者が選んだレース」として報告されて**
選択バイアスの推定が壊れる(FR-009)。

**制約**:

```sql
ALTER TABLE chaos_snapshots
  ADD CONSTRAINT ck_chaos_snapshots_capture_trigger
  CHECK (capture_trigger IN ('daily_operational','predict_manual','predict_auto',
                             'explicit_command','legacy_unknown'));
```

**DEFAULT を置かない**理由: 書き手に必ず明示させる。
暗黙の既定値に落ちる設計こそが 084 の horizon 欠陥(`0..∞` フォールバック)の原因だった。

**既存行の扱い**: migration は**行数に依存せず常に同じ手順**を踏む。
「作者の環境で 0 行だったから」を根拠に形を変えると、行を持つ環境で壊れる。

```sql
ALTER TABLE chaos_snapshots ADD COLUMN capture_trigger TEXT;          -- nullable で追加
ALTER TABLE chaos_snapshots ADD COLUMN capture_policy_version TEXT;
UPDATE chaos_snapshots
   SET capture_trigger = 'legacy_unknown',
       capture_policy_version = 'capture_policy_v0'
 WHERE capture_trigger IS NULL;                                        -- 0 行なら no-op
ALTER TABLE chaos_snapshots ALTER COLUMN capture_trigger SET NOT NULL;
ALTER TABLE chaos_snapshots ALTER COLUMN capture_policy_version SET NOT NULL;
-- CHECK 制約は backfill の後に追加する
```

遡及ラベルは **`legacy_unknown`** である。`daily_operational` を付けてはならない —
084 の CLI は `--date` と `--race-id` の両方を許すので、
既存行がどちらで取られたかを**区別する情報が残っていない**。
推測でラベルを付けると契機別内訳と選択バイアスの開示が静かに汚染される。

`capture_policy_version` は `capture_policy_v0`(086 以前の規則で取られたことを明示)。

**遡及行は確認コホートに入らない**: 既存行の readout は窓を持たない v1 artifact を指す。
前向き報告は**単一の凍結設定に絞って**読む(`chaos_bands.py:1714`)ので、
遡及行は新しい設定の報告に**そもそも現れない**。特別な除外処理は要らない。
事前登録より前に取られた観測が確認コホートに入らないのは正しい挙動である。

**`capture_strength` の意味(084 から継承・086 で変えない)**:
`confirmatory` = 新規取得であり、取得の**前後**とも結果が未確定で、
`post_time` が既知かつ捕捉時刻がそれより前。
`weak` = `post_time` が不明、または前後の未確定確認が片方だけ通った。
`unknown` = 新規取得でない。
086 の順序是正(判定を取得の前に移す)で**この判定基準は変わらない** —
移動するのは判定の**位置**であって述語ではない。確認適格は `confirmatory` のみ。

**`source` との関係(FR-010)**: `source` は**実際にデータを取った先**(`netkeiba`)を指し続ける。
`capture_trigger` はそれを上書きしない。両者は直交する 2 つの事実である。

### 1 レースあたりの行(FR-002・FR-002a)

**1 レースにつき生涯ちょうど 1 行**。追記も差し替えもしない。

| status | void_reason | 意味 |
|---|---|---|
| `active` | NULL | 有効な凍結観測。表示に使える |
| `void` | `field_changed` | 捕捉後に出走構成が変わった。**その場で更新**(新しい行は作らない) |

**憲法 V**: オッズを含む行が 1 レースにつき 1 行しか存在しないので、
同一馬のオッズが複数時点で保存されることが**構造的にあり得ない**。
**これは DB 制約で担保する** — migration 0013 で**無条件の `UNIQUE(race_id)`** を追加する。
0012 の部分 index(`WHERE status='active'`)だけでは **void 1 行 + active 1 行が通る**ので、
「構造的」という主張がコードとテストだけの規約に落ちてしまう。
これが V を守る唯一の確実な方法である —
「構成が違えば時系列ではない」という分類による正当化は成立しない
(構成が変わっても生き残った馬のオッズは複数時点分保存されてしまう)。

`void` は **UPDATE**(`status` と `void_reason` のみ)。
`chaos_snapshots` には UPDATE 禁止 trigger が無い(それは `chaos_readouts` にのみある)ので
DB 側の制約に反しない。`field` と `captured_at` は書き換えない。

**084 からの変更**: 084 は取得後に旧行を void して新行を追記していた。
086 は**再捕捉そのものを行わない**ので `recaptured` も `late_scratch` も生成しない。
これを前提とした既存テストとフィクスチャの更新が要る
(`live/tests/integration/test_chaos_capture_db.py:241` /
`api/tests/integration/test_race_chaos_api.py:87`)。

**前向き報告との整合(決定打)**: `training/chaos_bands.py:1895-1896` は
1 レースに複数行あると `not_one_row_per_race` として**確認コホートから丸ごと除外する**。
差し替え設計は、そのレースを捕捉した意味を消してしまう。1 行設計ならこの除外は起きない。

0012 の部分 unique index は**冗長になるが 0012 を書き換えないため残す**。
最終的な不変条件は 0013 の**無条件 `UNIQUE(race_id)`** である。

---

## 2. `fetch_throttle_state`(新規・運用状態)

| 列 | 型 | Null | 説明 |
|---|---|---|---|
| `domain` | `TEXT` | PK | 取得先の**スキーム + ホスト**(例 `https://race.netkeiba.com`)。値は `scrape/fetch.py::_domain` と同一規則で導出する(名前は `domain` だが実体は origin) |
| `next_allowed_at` | `TIMESTAMPTZ` | NOT NULL | **次に叩いてよい時刻**(予約済みの未来時刻を書く)。`last_attempt_at` という名前にはしない — 未来時刻を入れる列なので名前と意味が乖離する |
| `blocked_until` | `TIMESTAMPTZ` | NULL | 拒否・制限を受けた後のクールダウン期限 |
| `block_reason` | `TEXT` | NULL | `http_403` / `http_429` など。**CHECK 制約は置かない**(自由文字列・運用の手動投入も許す) |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | 監査用 |

**append-only ではない**(UPDATE する)。これは監査記録ではなく**現在の運用状態**であり、
憲法 V が禁じる「オッズの履歴」には当たらない。`chaos_readouts` の UPDATE 禁止 trigger は
このテーブルには適用しない。

**状態遷移**:

```text
(行なし) ──初回取得── → 通常
通常 ──403/429── → 抑制中 (blocked_until = now + cooldown, 再試行しない)
抑制中 ──blocked_until 経過── → 通常
```

---

## 2b. `chaos_snapshots_quarantine`(新規・重複行の退避先)

migration 0013 が無条件 `UNIQUE(race_id)` を張る前に、**既存の重複行を退避する**ための表。
084 の出荷済みコードは「旧行を void して新行を追記」するので、
出走構成の変化を経験した環境には ≥2 行/レースが実在しうる。
**黙って削除しない**(監査記録である)。

| 列 | 型 | 説明 |
|---|---|---|
| … | | **退避を実行する時点の** `chaos_snapshots` の列を `information_schema` から実測して写す(CLI が走るのは 0013 中断後 = まだ 0012 なので `capture_trigger` は存在しない) |
| `quarantined_at` | `TIMESTAMPTZ` | 退避時刻 |
| `quarantine_reason` | `TEXT` | `unique_race_id_backfill` |

**残す 1 行の決定規則**: `status='active'` の行。無ければ `captured_at` が最新の行。
それ以外を退避してから削除する。手順は決定的で、操作者の判断を要さない。

**この表は migration 0013 が作るのではない。復旧 CLI が必要になったときに作る。**
Alembic の DDL は 1 トランザクションなので、0013 が重複を見つけて中断すると
**0013 が作った表もろとも巻き戻る** — 退避先が消えて復旧不能になる。
→ 0013 は重複を検出して**中断するだけ**。退避表の作成と退避は
復旧 CLI(migration の外)が行い、その後に 0013 を再実行する。

### 憲法 V との関係

退避表には同一レースの複数時点のオッズ行が入りうるが、**これは憲法 V の違反ではない**:

- **084 が既に保存した行の移設**であって、086 が新しく観測を積むわけではない。
  086 が加えるのは `UNIQUE(race_id)` という**これ以上増やさない**制約の方である。
- **どこからも読まない** — 表示(`api`)・確認コホート(`training`)・特徴(`features`)の
  いずれの経路もこの表を参照しない。参照が生まれていないことは T056 のリーク境界テストで固定する。
- **恒久保持を意図しない** — 操作者が内容を確認したら削除してよい。
  存在理由は「黙って消さない」ことだけであり、時系列として使うためではない。

したがって **migration 0013 が追加するのは 2 要素**:
`chaos_snapshots` の 2 列 + `UNIQUE(race_id)` と `fetch_throttle_state`(憲法 VI)。

---

## 3. 派生値(保存しない)

| 名前 | 導出 | 置き場 |
|---|---|---|
| **表示適格** | `status='active'` かつ**発走時刻が既知**(`seconds_to_post IS NOT NULL`。発走前であることは捕捉時に確認済み)かつ**凍結構成が現況の started 集合と一致**(FR-002b) | `probability/chaos_eligibility.py` |
| **確認適格** | 表示適格 かつ `capture_strength='confirmatory'` かつ `seconds_to_post` が `primary_horizon` の窓内 | 同上 |

**構成の一致は読み取りのたびに判定する**(FR-002b)。捕捉時の無効化だけでは、
取消の後に誰も予測を再実行しなければ古い構成の観測が表示され続ける。
`api`(表示)と `training`(確認コホート)の両方がこの判定を通す。

非遡及(FR-007)は報告が単一 digest スコープであることから構造的に従う(research D8)。

---

## 4. artifact の凍結設定(ファイル・DB ではない)

```jsonc
"preregistration": {
  "primary_horizon": {
    "minimum_seconds_to_post": 600,      // 発走 10 分前
    "maximum_seconds_to_post": 86400,    // 発走 24 時間前
    "basis": "schedule_jitter_floor_and_next_day_market_ceiling",
    "measured_coverage_of_pre_race_predict_clicks": 0.956
  },
  // 以下 v1 と完全同一(events / exclusion_rules / promotion_rule /
  // multiplicity_policy / calibration_tolerance / final_decision_date / …)
}
```

**必須項目**である(FR-005)。欠落・型不正・**上限 <= 下限**(幅ゼロの窓を含む)・
**`primary_horizon` の中の短縮別名 `min_seconds_to_post` / `max_seconds_to_post`** は `load_chaos_artifact` が拒否する(D9)。

---

## 5. ジョブ記録(既存 `ingestion_jobs.summary` の中身・スキーマ変更なし)

`predict` ジョブの `summary` に `capture` を足す。JSONB なので migration は不要。

```jsonc
{
  "kind": "predict",
  "source": "manual",
  "predict_origin": "manual_ui | auto_after_refresh",   // FR-009 の契機の正本
  "capture": {
    "state": "started | launched | done",
    "started_at": "2026-07-27T…",
    "retried": false,
    "outcome": "captured | skipped | rejected | failed | unknown",
    "reason": "ok | already_captured | post_time_elapsed | result_settled |
               post_time_unknown | concurrent_capture | source_cooldown | ...",
    "capture_strength": "confirmatory | unknown | null",   // weak は遡及行にのみ存在(SC-010 例外 7)
    "confirmation_eligible": true,
    "seconds_to_post": 24124,
    "chaos_snapshot_id": "…"
  },
  "output": "…"
}
```

**FR-015 の再実行防止**: `capture` キーは**予測を始める前に `state="started"` でコミットされ**、
subprocess を起動した直後に**別セッションで `state="launched"` に進める**。
再試行時、`done` と `launched` なら捕捉を飛ばし、`started` のまま残っていれば
(起動前のクラッシュ)**1 回だけ**やり直す。
**`launched` が無いと FR-015 の MUST NOT を踏む** — 取得の途中で落ちた記録が
`started` のまま残り、再試行が外部取得をもう一度発火させる。
終了時の 3 分岐すべてで `capture` を**マージする**(FR-015a)。

---

### race ジョブ側の由来(`summary.refresh_origin`・正本)

`enqueue_race` にも由来を持たせる(FR-009 / SC-007)。キーと語彙を凍結する。

| キー | 値 | 意味 |
|---|---|---|
| `summary.refresh_origin` | `manual_ui` | 画面の単一レース更新ボタン(`routers/refresh.py`) |
| | `daily_bulk` | 日次一括(`enqueue.py`)と日次の fan-out(`runner.py`) |

`run_one` はこれを読んで `enqueue_predict(..., origin=…)` に渡す
(`manual_ui` → `manual_ui` / `daily_bulk` → `auto_after_refresh`)。
**`run_one` は `job.summary = summary` で全体代入する**ので、
`refresh_origin` もマージしないと滞留回収の再実行で消える。

---

### `predict_origin` と `capture_trigger` の対応(正本)

2 つの語彙は層が違う — `predict_origin` は**ジョブの由来**、
`capture_trigger` は**観測の契機**。写像は 1 対 1 で固定する。

| `predict_origin`(ジョブ) | `capture_trigger`(観測) | 意味 |
|---|---|---|
| `manual_ui` | `predict_manual` | 画面の予測ボタン / 単一レース更新ボタン = 利用者が選んだ |
| `auto_after_refresh` | `predict_auto` | データ更新に追随した自動予測 = 中立 |

この写像が崩れると FR-009 / FR-011 / SC-007 の選択バイアス推定が壊れる。

---

## 6. API 応答(純追加)

`JobRow` に 1 フィールド追加。値は転記のみで、API 側で解釈・再計算しない(021 規律)。

**射影は fail-soft である** — `summary["capture"]` が想定の形でない
(必須項目が無い・`state` / `outcome` が未知の値)ときは
**`capture=None` にして `summary` は従来どおり返す**。例外にしない。
遡及行・旧版の記録・部分書き込みで `GET /jobs` 全体が 500 になるのを防ぐ。

**`summary.capture` の全キーを射影するわけではない** — `started_at` と `retried` は
運用の再試行制御に使う内部状態であり、公開契約には出さない。
射影するのは下の 7 フィールドだけである(意図的な非対称)。

```python
class JobCaptureRow(BaseModel):
    state: Literal["started","launched","done"]  # 進行中を注意色にしないため +
                                     # 取得前クラッシュ(started)と取得後クラッシュ(launched)の区別
    outcome: Literal["captured","skipped","rejected","failed","unknown"]
    reason: str | None = None        # 既知値は description に列挙するが**閉じない**。
                                     # `started` / `launched` の間は理由がまだ無いので None
    capture_strength: str | None = None
    confirmation_eligible: bool | None = None
    seconds_to_post: int | None = None
    chaos_snapshot_id: str | None = None

class JobRow(BaseModel):
    ...
    summary: dict | None = None            # 生の転記
    capture: JobCaptureRow | None = None   # summary["capture"] の機械的な射影
```

**`reason` を閉じた列挙型にしてはならない** — 運用画面は「未知の理由をそのまま出す」
(握りつぶさない)ことを要求しており(FR-022 の系)、閉じると未知値で 422 / 500 になるか
値が消える。`outcome` だけを列挙型にする。

`capture` は `summary["capture"]` の**機械的な射影**であり、
値の解釈も再計算もしない(021 規律は保たれる)。
既存フィールドの型・順序・名前は不変。OpenAPI は純追加(削除ゼロ)。

---

## 7. 不変条件

| ID | 不変条件 | 検証方法 |
|---|---|---|
| INV-1 | 1 レースに `status='active'` は最大 1 行 | 既存の部分 unique index(084)。**index と trigger は migration 0012 にのみ存在し ORM の `__table_args__` には無い**ので、ORM metadata からスキーマを作る経路では強制されない → integration テストで担保する |
| INV-2 | **1 レースのオッズ入り行は生涯ちょうど 1 行** | **DB の無条件 `UNIQUE(race_id)`**(0013)。加えて行が存在すれば理由を問わず `already_captured` で取得 0 回。全シナリオ(取消 / 再取消 / A→B→A / 窓外→窓内)で行数 1 を assert |
| INV-3 | `source` は契機で上書きされない | 捕捉経路のテストで `source == fetcher.source` を assert |
| INV-4 | 窓を持たない artifact はどの経路でも読めない | ローダの型付きエラーを 3 経路(api / live / training)で検証 |
| INV-5 | 適格でないレースでは外部取得が 0 回 | 取得回数を数えるスパイ fetcher で assert(理由文字列だけを見ない) |
| INV-6 | 捕捉の失敗が予測ジョブの status を変えない | 捕捉を例外にしても predict が SUCCEEDED |
| INV-7 | 荒れ度の値・バンド境界・λ が 084 と同一 | 旧 artifact(**ファイルを独立に読む**)と新 artifact で readout を比較。**比較対象はバンド / 全確率 / `expected_s`** — **`artifact_digest` だけが必ず変わる**(`version` は `chaosbands-v1` のまま据え置く —
`chaos_readouts.artifact_version` に永続化される値なので、bump は SC-010 の例外に無い出力変更)。
`artifact_digest` は必ず変わるので比較の射影から除く |
