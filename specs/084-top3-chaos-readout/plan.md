# Implementation Plan: 上位3着ベースの荒れ度読み出し (top-3 chaos readout) — rev2

**Branch**: `084-top3-chaos-readout`(未作成 — spec/plan は `083-segment-accuracy-viewer` 上に純追加)
| **Date**: 2026-07-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/084-top3-chaos-readout/spec.md`

## Summary

066 の荒れ度(市場 q の正規化エントロピー H の五分位 5 段)は**本命強度計**であって配当の跳ねを
測っていない(fit 窓外 2024+ で単勝20倍+勝利 AUC 0.513・50倍+ 0.473 = コイン以下)。
ユーザーの荒れ定義は**上位3着の人気構成**なので、事後ラベルを
`S = 凍結スナップショットの popularity による 1-3 着馬の人気順位合計` に変え、
**既存 `joint_probabilities(q, stage_discount=...)` の順序三つ組同時分布から
S の PMF と全事象質量を同一走査で導出**する。
新しい学習モデルは作らない(レース単位 LightGBM は市場 1 数値に log loss 11/11 年負け)。
モデル p も使わない(同一レース集合で q 0.779 > √(pq) 0.766 > p 0.733)。
生 Harville の荒れ過小評価は 049 の stage discount を**市場 q で fit した新 artifact**
(λ2=0.8304 / λ3=0.7111)で補正する。**ただし λ は総崩れを一切変えられない**(実測 5.6e-17)。

**rev2 の主な変更**(codex 4 並列レビュー反映):

1. **バンド軸を E[S] → `P(S≥20)`**(期待値は裾を隠す。実測でも P(S≥20) が全ラベルで上回る)
2. **事象は順序三つ組の走査中に評価**(S の PMF からはヒモ荒れ・総崩れを復元できない)
3. **migration 0012 を追加**(前向き証跡を DB 化。`artifacts/` は git 非追跡で消える)
4. **時間ゲートの逆転を修正**(`target_date > fit_through` かつ `>= valid_from` で表示可)
5. **捕捉に 065 の規律**(新規取得・前後の result-pending・post_time・数値 seconds_to_post)
6. **構造的ゼロは `0.0` + フラグ**(`null` は確定陰性を評価から消す)
7. **配置を是正**(fit/diagnose は `training`・凍結は DB なので API は `live` 非依存)

## Technical Context

**Language/Version**: Python 3.12 (db / probability / eval / training / api / live)、
TypeScript + React 19 (front)

**Primary Dependencies**: 既存のみ。`probability/engine.py::joint_probabilities`、
`eval/stage_discount.py`、`eval/baselines.py::harville_topk`、`live/guards.py`(065 の
result-pending 規律)、FastAPI / pydantic / SQLAlchemy 2.0 / Alembic、Vitest。**新規外部依存なし**。

**Storage**: **migration 0012 で `chaos_snapshots` / `chaos_readouts` を追加**(追加のみ・
既存テーブル不変)。バンド artifact は `artifacts/chaos_bands/{digest}.json` + コミット済み
manifest に承認 digest を pin。

**Testing**: pytest(db / probability / eval / training / api / live)、Vitest(front)。
property 検算は固定 seed の決定論テスト(hypothesis は新規依存になるため不採用)。
**加えて凍結 fixture による outcome 回帰**(SC-008)。

**Target Platform**: ローカル運用(API :8000 / front :5173)。既存 deploy 構成に変更なし。

**Project Type**: 既存の複数パッケージ monorepo(web-service + SPA + CLI)。

**Performance Goals**: 予算は **engine + 三つ組集約 + DB 読み + 直列化**の合計で定める
(engine 呼び出しのみの見積りは実装必須の集約ループを落とす)。実測(n=18・本 repo):
engine 1.38 ms + 三つ組集約 1.91 ms = **3.3 ms / provenance** → 2 provenance で **6.6 ms(warm)**、
**cold 初回 17.7 ms**。

> **T086 のフルパス実測による更新**: 上の 17.7 ms は **engine + 集約のみ**の部分計測で、
> DB 読み・直列化・`bet_type` 指定時の 009 呼び出しを含んでいなかった。
> FastAPI 経路全体の実測(n=18・`bet_type=place`)は
> **cold p50 17.3 ms / p95 30.7 ms、warm p50 8.3 ms / p95 10.2 ms**。
> cold は `(content_digest, artifact_digest)` のキャッシュキーごとに 1 回だけ発生し、
> 利用者が繰り返し見るときの体感は warm 側である。
>
> **T086 は SLA ゲートではなくベンチマークとする**。同じテストを連続 2 回走らせただけで
> warm p95 が 10.2 ms → 16.3 ms に振れた(開発機の負荷依存)。壁時計 p95 を厳密な合否条件に
> すると **flaky テスト**になり、スイート全体の信頼を損なう方が害が大きい。
> よって実測 p50/p95 を**常に出力**し、assert は**アルゴリズム的回帰だけが破る緩い上限**
> (cold 150 ms / warm 60 ms)に留める。
`bet_type` 指定時は既存の p ベース 009 呼び出しが**追加で**走る(入力分布が違うため再利用不可)。
`(content_digest, artifact_digest)` をキーにキャッシュすることで 2 回目以降は warm 経路に入る。
**単一 engine 呼び出しでなくフルパス p95 を計測する**。

**Constraints**:
- API は全 path GET・書き込み経路ゼロ(既存 import-graph / AST 境界テストで機械固定)
- API は `live` を import しない(DB を直接読む)
- OpenAPI 純追加・drift-check 緑・**CI で `app.openapi()` を両 snapshot と比較**
- 066 の `race_dispersion` 応答と `dispbands-v1.json` は**バイト不変**
- 本 feature の派生値は feature registry / materialized columns / model recipe に現れない

**Scale/Scope**: 実 DB 67,636 レース(2007-2026)。2025 実績 **109 開催日 / 3,455 レース**。
λ / 境界の fit は 2020-2023 の 13,788 レース(オフライン 1 回)。

## Constitution Check

*GATE: Phase 0 前に PASS。Phase 1 設計後に再チェック。*

- [x] **I. データ契約**: `raceId` 12 桁契約に変更なし。2007+ のみ。`id_mappings` 経由の結合規約
  不変。ラベル名不変。凍結行は既存 `race_id` + `horse_id` で参照する。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 荒れ度・S・凍結行・バンド・事象確率を
  モデル特徴 / 校正 / 買い目に**一切流入させない**。S は結果由来なので leak-guard 必須(SC-005)。
  表示は市場 q のみを入力とし対象レースの結果を読まない。`chaos_snapshots` は
  serving / betting / features のどの経路からも参照されない。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 事象定義・スコア式・λ・境界・**閾値 20**・
  **昇格規則**・**最終判定日**を結果前に artifact へ凍結。spec の 2024+ 数値は **discovery**。
  確認は `valid_from` 以降・`capture_strength='confirmatory'` のコホートのみ。
  最小陽性 100 / 最小開催日 60。未達は NO_DECISION。**本 feature はモデル/特徴量を変更しない**
  ため walk-forward 採用ゲートの対象外だが、計器自身の前向き検証を US5 で事前登録する。**PASS**
- [x] **IV. 確率整合性**: モデル p・win 予測・009 導出は不変(SC-004)。
  本 feature の確率は **provenance ごとに**単一の同時分布から導出され、入れ子事象の不等式が
  構成上成立し、Σ(順序三つ組) = 1 を不変条件として検査する。取消馬は canonical field から除外。
  **PASS**
- [x] **V. 再現性と監査**: `chaos_snapshots` / `chaos_readouts` は append-only。
  **`recommendations.market_odds_used` が既に判断時点の凍結オッズを永続化している前例**に載る
  — V の「オッズ履歴を持たない」は運用オッズ表 `race_horses.odds` の規約であり、
  提示済み判断の事後検証可能性は V 自身の要求。市場由来値は pseudo、補正後は provisional。**PASS**
- [x] **VI. feature 分割規律 / スキーマ変更の正当化**: front 着手前に API / DB 契約を確定
  (Phase 1 の contracts/)。P0 未決なし。**migration 0012 を追加する**(rev1 の
  「スキーマ変更ゼロ」を撤回)。理由: 前向き証跡を git 非追跡の `artifacts/` に置くと
  ローカルディスクと共に消え、数か月〜年単位の前向き研究の基盤にならない。
  012(0005)/040(0008)/054(0009)/057(0011)と同じ「追加のみ・既存契約不変」の前例に従う。
  予測・推奨系テーブルの契約に変更なし。**PASS(正当化済み)**
- [x] **品質ゲート**: codex second opinion を **6 回**取得(単独 2 + 4 並列レンズ)。
  **全採用・不採用ゼロ**。差分と採用根拠は spec.md「レビュー記録」と
  [research.md](./research.md) D1-D19 に記録。**PASS**

**Gate result: 全項目 PASS。VI のみ「正当化を伴う変更」→ Complexity Tracking に記載。**

## Project Structure

### Documentation (this feature)

```text
specs/084-top3-chaos-readout/
├── spec.md              # rev2
├── plan.md              # This file (rev2)
├── research.md          # Phase 0 output (D1-D19)
├── data-model.md        # Phase 1 output (rev2: DB テーブル)
├── quickstart.md        # Phase 1 output (rev2)
├── contracts/
│   ├── chaos-distribution.md   # 導出コアの純関数契約 + 不変条件 + outcome 回帰
│   ├── storage-and-artifact.md # migration 0012 / 捕捉規律 / artifact
│   ├── api-additions.md        # 純追加 GET フィールド
│   └── cli.md                  # live / training CLI 契約
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
db/
├── migrations/versions/0012_chaos_readout.py   # 新規: chaos_snapshots / chaos_readouts
└── src/horseracing_db/models/chaos.py          # 新規: ORM(全パッケージから参照可)

probability/src/horseracing_probability/
├── chaos_events.py              # 新規: 事前登録イベント定義(実行可能な型付き述語)
├── chaos_distribution.py        # 新規: 同時分布 → S の PMF + 事象質量(純関数・順序走査)
└── chaos_artifact.py            # 新規: artifact の**ロードと検証**(api と live が共有する唯一の層)

eval/src/horseracing_eval/
├── stage_discount.py            # 既存(再利用のみ・無改修)
├── dispersion_bands.py          # 既存 066(無改修・dispbands-v1 は不変)
└── chaos_lambda.py              # 新規: 市場 q での λ fit(q だけで完結・probability 不要)

training/src/horseracing_training/
├── chaos_bands.py               # 新規: 境界 fit / 診断 / 前向き報告 / カバレッジ
│                                #       (probability + eval の両方に依存できる唯一の層)
└── cli.py                       # 拡張: chaos-bands サブコマンド群

live/src/horseracing_live/
├── chaos_capture.py             # 新規: 065 規律の捕捉 + 同一トランザクション書き込み
└── cli.py                       # 拡張: capture-chaos

api/src/horseracing_api/
├── chaos.py                     # 新規: DB 読み + 導出の組み立て(ロードは probability に委譲)
├── dispersion.py                # 既存 066(無改修)
├── schemas.py                   # 拡張: RaceChaos 他を純追加(extra="forbid")
└── routers/predictions.py       # 拡張: race_chaos を run 選択より前に構築

front/src/
├── lib/chaosLabels.ts           # 新規
├── components/RaceChaosPanel.tsx      # 新規: 主枠(hasPreds と独立に描画)
├── components/RaceDispersionPanel.tsx # 改修: 「市場の支持集中度」へ格下げ・折り畳み
└── pages/RaceDetailPage.tsx     # 改修: 結線

artifacts/chaos_bands/
└── {artifact_digest}.json       # content-addressed(git 非追跡)

config/
└── chaos_bands_approved.json    # ★ コミットする: 承認 digest の pin
                                 #   (`artifacts/` は .gitignore の `/artifacts` で全除外され
                                 #    コミットできないため — この除外は過去の
                                 #    「artifacts symlink 誤追跡でモデル実体喪失」の再発防止なので触らない)
```

**Structure Decision**: 配置は**依存方向の実測**で決まる(`probability → eval` の一方向依存を
確認済み):

| 置き場 | 理由 |
|---|---|
| `probability/chaos_distribution.py` | `joint_probabilities` を使うので `eval` には置けない |
| `eval/chaos_lambda.py` | λ fit は q の条件付き NLL だけで完結し `probability` を必要としない |
| **`training/chaos_bands.py`** | 境界 fit と診断は `probability` が要る。**`training` は `eval` と `probability` の両方に依存する唯一の層**(rev1 が `eval` に置いていたのは実装不能だった) |
| `live/chaos_capture.py` | 書き込みは `live` のみ(API は read-only 境界) |
| `db/models/chaos.py` | 凍結行を DB に置くことで **API は `live` を import せずに読める** |
| **`probability/chaos_artifact.py`** | artifact のロードは **`api`(表示)と `live`(readout 書き込み)の両方**が必要。`live` は `api` も `training` も import できない(実測: live の依存は db/serving/betting/probability/scrape)ので、**両者が共有する `probability` に置くしかない**。fit / publish は `training`(書き込み側)|

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| migration 0012(スキーマ追加) | 前向き証跡は数か月〜年単位で蓄積する必要があり、消えると研究が成立しない | `artifacts/*.jsonl` は git 非追跡でローカルディスクと共に消える。git にコミットする案も検討したが、書き込みが日次運用の一部になるため commit 運用に載せるのは脆い |
| 凍結観測の DB 保存(憲法 V「オッズ履歴を保存しない」との関係) | 提示済み判断の事後検証可能性は V 自身の要求。`recommendations.market_odds_used` が既に「1 判断 1 凍結値」を永続化している前例に載る | **within-race の多点捕捉(horizon 時系列)は V に正面から抵触するのでスコープ外にした**(1 レース 1 有効行・SNAP-4)。多点化には憲法改定を別変更セットで先に行う必要がある |
| FR-009 が日次運用を要求(憲法「初期は全て手動実行・スケジューラは将来スコープ」との関係) | 捕捉が走らないと表示も検証も成立しない | **自動スケジューラは導入しない**。「1 日 1 コマンドで起動可能 + カバレッジ閾値をゲートにする」に緩め、operator 手順を contracts/cli.md に明記した |
