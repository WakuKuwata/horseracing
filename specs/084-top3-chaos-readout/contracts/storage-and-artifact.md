# Contract: 永続化 (DB) と バンド artifact (rev2)

rev1 は凍結行を `artifacts/*.jsonl` に置いていたが、`artifacts/` は **git 非追跡**で
ローカルディスクと共に消えるため、数か月〜年単位の前向き研究の基盤にならない。
**migration 0012 で DB テーブル 2 つを追加**する(ユーザー決定)。

---

## 1. migration 0012

**追加のみ**。既存テーブル・既存列・既存契約を一切変更しない。

```text
chaos_snapshots   -- 凍結された表示時点の市場観測
chaos_readouts    -- そのとき実際に表示した値(結果確定前に書く)
```

列は [data-model.md](../data-model.md) §1 / §2 を正とする。

### 憲法 V との整合

憲法 V の「オッズはスナップショット履歴を保存せず最新値で上書きし `updated_at` のみ保持」は
**運用オッズ表 `race_horses.odds` の規約**である。判断時点の凍結値の保存は V 自身が要求する
監査要件であり、**`recommendations.market_odds_used` / `estimated_market_odds_used` が既に
同じことをしている前例**に載る。`chaos_snapshots` は `race_horses` を置き換えず、
serving / betting / features のどの経路からも参照されない。

### 書き手・読み手

| 役割 | パッケージ |
|---|---|
| 凍結行の書き手 | `live` のみ |
| 凍結行の読み手 | `api`(read-only)・`training`(診断 / 報告) |
| **artifact のロード + 検証** | **`probability/chaos_artifact.py`**(`api` と `live` が共有) |
| artifact の fit + publish | `training/chaos_bands.py` |

**ロード先の根拠**: artifact は `api`(表示)と `live`(readout 書き込み)の**両方**が必要だが、
`live` は `api` も `training` も import できない(実測: live の依存は db / serving / betting /
probability / scrape)。両者が共有するのは `db` と `probability` だけなので、
**ロード + 検証は `probability` に置く**。ここを誤ると `live` 側でローダを二重実装することになる。

API は `db` に既に依存しているので `live` を import する必要がない
(rev1 の設計は API → live の**禁止依存**を要求していた)。

---

## 2. 捕捉規律(065 の規律を流用・BLOCKER 修正)

`live` の捕捉は次を**すべて**満たしたときのみ `capture_strength='confirmatory'` とする。

| ID | 規約 |
|---|---|
| CAP-1 | **キャッシュを使わない新規取得**。DB の既存値の読み取りだけで済ませてはならない |
| CAP-2 | 取得**前**に result-pending(`race_results` 行なし)を確認 |
| CAP-3 | 取得**後**にも result-pending を再確認(取得中に確定した場合を排除) |
| CAP-4 | `post_time` が既知なら `captured_at < post_time` を必須 |
| CAP-5 | `seconds_to_post` を**数値**で記録(`pre_race`/`unknown` のラベルでは T−5分 と T−3時間 を区別できない) |
| CAP-6 | `source` は取得アダプタが返した実際の出所。呼び出し側の `--source` 自称を信用しない |
| CAP-7 | 上記を満たさない捕捉は `weak` / `unknown` として**記録はする**(表示には使える)が、**確認コホートからは除外**する |
| CAP-8 | 捕捉と `chaos_readouts` の書き込みは**同一トランザクション**。readout 書き込み時にも result-pending を再確認する |
| CAP-9 | **1 日 1 コマンドで起動可能**にし、カバレッジ閾値を US6 のゲートにする。憲法「初期は全て手動実行・スケジューラは将来スコープ」に従い**自動スケジューラは導入しない** — operator 手順(推奨実行時点 T−30 分・最大 staleness・網羅率目標)を本契約に明記する |
| CAP-10 | **`confirmatory` は `post_time` が既知のレースに限られる**。実測充足率 2023 年 0% / 2024 年 0% / 2025 年 22.9% / **2026 年 100%** → 確認コホートは netkeiba 期のみで成立する。カバレッジ報告に post_time 充足率を含める |

---

## 3. バンド artifact(content-addressed)

`artifacts/chaos_bands/{artifact_digest}.json`(git 非追跡)、承認 digest は
**`config/chaos_bands_approved.json`(コミット対象)** に pin する。
**`artifacts/` 配下に manifest を置いてはならない** — `.gitignore` の `/artifacts` が配下を全除外するため
コミットできず ART-2 が成立しない(この除外は過去の事故対策なので変更しない)。

| ID | 規約 |
|---|---|
| ART-1 | `artifact_digest` は **payload 全体**(λ・境界・fit 窓・入力ハッシュ・code SHA・事前登録・status を含む)の canonical SHA-256。事象定義だけのハッシュでは改ざんを止められない |
| ART-2 | **digest 名で publish** し、承認 digest を **`config/chaos_bands_approved.json`**(コミット対象)と照合する。gitignore された JSON を loader が無検証で信じてはならない |
| ART-3 | 発行は `O_EXCL` 等の**衝突しない原子作成**(check-then-rename は競合する) |
| ART-4 | **時間ゲート(全文書統一)**: 表示に使えるのは `target_date > fit_through` **かつ** `target_date >= valid_from`。artifact は `valid_from > fit_through` を必須とする。**`target_date > fit_through` を拒否条件にしてはならない**(rev1 の誤り。2024 年以降の全レースが消える) |
| ART-5 | `edges_basis = closing_history` 固定。「live snapshot の五分位」と表示してはならない |
| ART-6 | `calibration_status` の `confirmed` 化は**新 version の発行**でのみ。既存ファイルを書き換えない |
| ART-7 | 049 の stage discount artifact を読み込まない。λ は本 artifact 内の値のみ |
| ART-8 | 発行時に**数値安定性ゲート**を通す(代表 + 敵対フィールドで Σ=1 を検査し `numeric_stability_report` に記録)。`(0,5]` 全域が使えるとは主張しない |
| ART-9 | ロード失敗・digest 不一致・時間ゲート外は **fail-closed**。黙って既定値にフォールバックしない |

### ロード時検証

```text
1. JSON パース可能
2. 必須キーが揃っている
3. artifact_digest が payload から再計算した値と一致
4. 承認 manifest の digest と一致
5. quintile_edges が長さ 4 かつ狭義単調増加
6. lambda2 / lambda3 が (0, 5] かつ numeric_stability_report が緑
7. valid_from > fit_through
8. target_date > fit_through かつ target_date >= valid_from
```

いずれか失敗 → `unavailable_reason` を返し band と確率を出さない。

---

## 4. 前向き検証の証跡

**producer**(rev1 に欠けていた最大の穴 — reader だけがあった):

| 段階 | 誰が | 何を |
|---|---|---|
| 表示前 | `live` の捕捉 | `chaos_snapshots` + `chaos_readouts` を同一トランザクションで追記(CAP-8) |
| 結果確定後 | `training` の報告 | **`chaos_snapshots.field` の順位から** S を算出して集計。現在の `race_horses.popularity` は使わない |

`chaos_readouts` は UPDATE 禁止・結果確定後の INSERT 禁止。
これにより「結果を見てから表示値を直す」経路が構造的に存在しなくなる。

**分析単位**: **1 レース 1 行**(`status='active'`)。within-race の多点捕捉は SNAP-4 で禁止なので
反復測定の問題は生じない。horizon 別の層別は**レース間**で行う。開催日でクラスタリングする。
捕捉カバレッジと除外レースの特性を必ず報告する(選択バイアス検知)。
