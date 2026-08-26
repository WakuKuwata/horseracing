# Implementation Plan: 評価契約 v5 — 判定の証拠保全と、seed 分散の縮小

**Branch**: `main`(直接コミット運用) | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/100-eval-contract-v5/spec.md`

---

## Summary

採否ゲートの検出力を上げようとして spec を書き、**書いている途中で中心仮説を実測で殺した** feature。
残ったのは次の 3 つで、確実なのは 1 つだけである。

| US | 状態 | 実装コスト |
|---|---|---|
| **US1 per-race 証拠の保存** | **確実**。今は CI 計算後に捨てている | 小 |
| ~~US2 control variate~~ | **T0 測定で棄却**(R²=0.029・CI 幅 −1.5%) | 0(実装しない) |
| **US3 k-seed アンサンブル** | **利得未測定**。スパイク中断点つき | 中〜大(スパイク後に確定) |
| **US4 δ の再導出** | 多重検定予算から導出 | 小(文書 + 定数) |

技術的アプローチ:

- **US1** は `PairedReport` に既にある `diffs_by_day`(開催日→差のリスト)を、**race_id と両アームの
  loss と共変量を持つ per-race 行**に格上げし、driver が verdict を書くときに落とさないようにする。
  `to_dict()` は `asdict` なので serialize 自体は既に通っている — 落としているのは **driver 側**。
- **US3** は `k` 個の booster を学習し、**レース内 softmax 後の確率を平均**して `log p̄` を
  アンサンブルのスコアとする。これは既存 raw race-softmax スコアと同じく**レース内の加法定数を
  除いて定まる**量なので、**既存の strict-past OOF isotonic 手続きがそのまま適用できる**
  (校正器はアンサンブルの OOF で再 fit)。
- **US4** は `MIN_EFFECT_DELTA` 相当の値を gate-config で凍結し、導出根拠を文書化する。

**進め方の骨格**: US1 → **US3 スパイク(中断点)** → 判定 → 通れば US3 本実装 / 落ちれば記録して終了。
US4 は US1/US3 と独立に進められる。

---

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: LightGBM(custom objective・PL top-3 listwise)、numpy / pandas / scikit-learn
(isotonic)、SQLAlchemy 2.0 + psycopg3、pytest

**Storage**: PostgreSQL 16(**本 feature ではスキーマを変更しない**)+ ディスク artifact(JSON)

**Testing**: pytest。合成データによる被覆率・偽陽性率テストを新規に置く

**Target Platform**: ローカル CLI(`uv run --project training` / `--project eval`)

**Project Type**: ML 評価ハーネス + 学習パッケージ(Web/UI は非関与)

**Performance Goals**: 判定 1 回の所要時間を**現状より悪化させない**。US1 は artifact 書き出しのみ
なので無視できる。US3 は学習が k 倍になるので**スパイク段では k=3〜5 に限定**する。

**Constraints**:
- v4 で凍結済みの gate-config(094〜099)を v5 のコードで実行したとき verdict がビット一致
- DB スキーマ・API・OpenAPI・買い目・確率導出(009)は不変
- 新規スクレイピングゼロ

**Scale/Scope**: 標準窓 2019-01-01..2026-08 で約 26,400 レース / 800+ 開催日。
957,061 学習行。触るのは `eval/` と `training/` の 2 パッケージ。

---

## Constitution Check

*GATE: Phase 0 前に PASS。Phase 1 後に再チェック。*

- [x] **I. データ契約**: **N/A**。`raceId` の扱いも ID 結合もラベルも変更しない。証拠 artifact は
  既存の `race_id` をそのまま記録するだけ。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: **PASS**。新規特徴量を追加しない。証拠行に載せる共変量は
  レース属性と市場由来の量のみで、結果は「勝ち馬の特定」以外の形で持ち込まない(FR-011)。
  証拠 artifact をモデル特徴に還流させない(FR-012・leak-guard テスト)。US3 の OOF 校正は
  既存の strict-past 手続きをそのまま使う。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: **PASS**。US3 は採否ゲート(winner NLL・top2/top3・ECE
  非劣化)を通す(FR-027)。**さらに本実装の前にスパイク足切りを課す**(FR-016)。
  US2 は spec を書いた当日に測定で棄却済み — この feature 自身が「評価先行」の実例になっている。
- [x] **IV. 確率整合性**: **PASS**。US3 の確率平均は Σ=1 を保つ。ただし**単調写像を各馬に単純適用
  すると総和 1 が崩れる**ため、`h(p̄_i)/Σ_j h(p̄_j)` とレース内正規化まで含めて評価し、
  不変条件テストで固定する(FR-022b)。取消・除外の扱いは変更しない。
- [x] **V. 再現性・監査**: **PASS**。証拠 artifact は append-only(FR-010)。アンサンブルの
  同一性 hash は seed 集合では不十分で、member の**順序つき** hash・前処理・校正器・集約演算・
  dtype・runtime を含める(FR-025)。報告には estimand の但し書きを必須化(FR-026c)。
- [x] **VI. feature 分割規律**: **PASS**。UI 非関与。DB スキーマ変更なし。P0 未決事項なし
  (Q1=δ の出所は spec 段階で多重検定予算に確定済み)。
- [x] **品質ゲート**: **PASS**。codex を 2 レンズ並走 + 1 回再試行で取得し、**採用 14 / moot 3 /
  不採用 0** を [codex-review.md](./codex-review.md) に記録。1 回目のハングは
  `codex unavailable: 40 分無出力` として記録済み。

**違反ゼロ** → Complexity Tracking は不要。

---

## Project Structure

### Documentation (this feature)

```text
specs/100-eval-contract-v5/
├── spec.md                  # 完了
├── plan.md                  # 本ファイル
├── research.md              # Phase 0
├── data-model.md            # Phase 1
├── contracts/
│   ├── paired-evidence.md   # US1 の証拠 artifact 契約
│   ├── ensemble.md          # US3 の合成・校正・同一性契約
│   └── delta-derivation.md  # US4 の δ 導出契約
├── quickstart.md            # Phase 1
├── codex-review.md          # 完了(R1-R17)
├── evidence/
│   └── cv-rho-probe.txt     # US2 を殺した測定
└── tasks.md                 # Phase 2(/speckit-tasks)
```

### Source Code (repository root)

```text
eval/src/horseracing_eval/
├── paired.py          # US1: PairedReport に per-race 証拠を追加(diffs_by_day の格上げ)
├── regime_paired.py   # US1: 複数窓 driver でも証拠を落とさない
├── bootstrap.py       # US1: 証拠からの再計算経路 / US3: seed 成分の扱い(FR-026b)
├── decision.py        # 契約版 v5・gate_config_hash の版別経路(FR-002a)・δ(FR-030)
└── foldfit.py         # US3: predict_over_folds のアンサンブル版

training/src/horseracing_training/
├── calib_split.py     # US3: arm E builder に k-seed を通す(_RECIPE_FIELD_DISPOSITION 登録)
├── win_model.py       # US3: k 個の booster の保持と確率平均
├── artifacts.py       # US3: 同一性 hash に member 順序・dtype・集約演算を含める
├── adoption.py        # 契約版の下限比較(既に lower bound・回帰テストで固定)
└── cli.py             # 証拠出力・スパイク driver

serving/src/horseracing_serving/
└── predictor.py       # US3: k 個ロード + 部分平均禁止(FR-024)

scripts/
├── cv_rho_probe.py         # US2 を殺した測定(保全)
└── ensemble_spike.py       # US3 の足切りスパイク(新規)

tests/                      # 各パッケージの tests/unit・tests/integration
```

**Structure Decision**: 既存の 2 パッケージ(`eval/`・`training/`)に閉じる。US3 が採用まで
進んだ場合のみ `serving/` に波及する。**スパイクが落ちれば `serving/` は一切触らない**。
新規パッケージ・新規ディレクトリ階層は作らない。

---

## Phase 構成(中断点つき)

```
Phase A: US1 — per-race 証拠の保存           ← 単独で価値が出る。ここだけで止めてよい
   ↓
Phase B: US4 — δ の再導出                    ← US1/US3 と独立。文書 + 定数 + テスト
   ↓
Phase C: US3 スパイク  ★★★ 中断点 ★★★
   k=3〜5 で実際に学習し、winner NLL の改善幅と sd_fold の縮小幅を測る。
   事前登録した足切り値に届かなければ **US3 は採用せず、記録して終了**。
   ↓ (通った場合のみ)
Phase D: US3 本実装(学習・校正・artifact 同一性)
   ↓
Phase E: US3 の serving 結線 + 採否ゲート
```

**中断点の根拠**: 091 が「Phase C の screening を中断点に置く」、088 が「T0 spike で中断」と
した前例に従う。加えて本 feature では **US2 が実測で死んでいる**ため、US3 だけ「理屈で効くはず」で
通すのは二重基準になる(checklist に明記済み)。

**Phase C を落ちたときの後始末**: 062/070/090 と同じ「非結線保全」。スパイクのコードとテストは
残し、結線・bump・レシピフィールドは revert する。結果は spec に転記する。

---

## Complexity Tracking

Constitution 違反なし。記入不要。
