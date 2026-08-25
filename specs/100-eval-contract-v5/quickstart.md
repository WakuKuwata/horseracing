# Quickstart: 評価契約 v5 の検証手順

前提: postgres が起動していること(`scripts/stack.sh start postgres`)。
実行はパッケージの env で行う(`cd training && uv run ...` / `cd eval && uv run ...`)。

---

## 0. すでに済んでいる検証(再現手順)

### US2 を殺した測定(D1)

```bash
cd training && uv run python ../scripts/cv_rho_probe.py
```

期待: 多変量 R² ≈ 0.029、CI 幅の削減 ≈ 1.5%。
→ **これが足切り値を下回るので US2 は実装しない。** 証跡は `evidence/cv-rho-probe.txt`。

---

## Phase A: US1 — per-race 証拠の保存

### A-1. 証拠が出ることを確認する(SC-001)

判定を 1 回走らせ、per-race 証拠 artifact が生成されることを確認する。

期待:
- 行数が verdict の `n_races` と一致する(INV-E1)
- 各行に `race_id` / `race_day` / 両アームの winner NLL / `diff` / 共変量がある
- `diff == candidate − active` が浮動小数点で厳密一致(INV-E3)

### A-2. 証拠だけから判定を再現する(SC-001 の中核・INV-A1)

証拠 artifact **だけ**(モデルも DB も使わない)を入力に、点推定・sampling CI・total CI を
再計算する。

期待: verdict.json が報告した値と**ビット一致**。

> 一致しなければ、再現に必要な依存が artifact に載っていない。
> **その依存を artifact 側に足す**のが正しい対処であり、再現要件を緩めてはならない。

### A-3. 後方互換(SC-002)

094〜099 の凍結済み gate-config を v5 のコードで実行する。

期待: verdict の**既存キーの値がすべてビット一致**。証拠 artifact は追加されるが既存の形は不変。

### A-4. 符号規約(INV-E4)

期待: `diff` の向きを反転させた mutation でテストが落ちる。

---

## Phase B: US4 — δ の再導出

### B-1. δ が `sd_fold` から独立であること(SC-007)

`sd_fold` の値を変えて判定を回す。

期待: **δ は変化しない**。

### B-2. 過去 verdict の不可侵(SC-008)

期待:
- 過去の v4 verdict のファイルが 1 つも書き換わらない
- 過去 verdict の表示に**当時の** δ・provenance が使われる
- δ が解決できない場合は **fail-closed**(v5 の δ で補わない)

---

## Phase C: US3 スパイク ★中断点★

### C-1. 足切り値を凍結する(実行前)

スパイクを回す**前**に、winner NLL の改善幅と `sd_fold` の縮小幅の足切り値を凍結する。
結果を見てから閾値を動かすことは禁止。

### C-2. スパイクを回す

```bash
cd training && uv run python ../scripts/ensemble_spike.py
```

k=3〜5 で実際に学習し、次を測る:

- レース内確率平均によるアンサンブルの winner NLL 改善幅
- **独立な k-seed バンドルを複数作った**バンドル間 sd(D7 —`sd/√k` を使ってはならない)

### C-3. 判定

| 結果 | 次 |
|---|---|
| 足切り通過 | Phase D へ |
| **足切り不通過** | **US3 は採用しない。** 非結線保全(062/070/090 同型)+ 結果を spec に転記して終了 |

---

## Phase D/E: US3 本実装(C-2 通過時のみ)

### D-1. Σ=1 の不変条件(SC-006 の前提・INV-M4・憲法 IV)

期待:
- `Σ p̄ = 1` がレース内で成立
- **校正後も** `h(p̄_i)/Σ_j h(p̄_j)` の形で `Σ = 1`
- 単調写像を各馬に単純適用する mutation(正規化を外す)でテストが落ちる

### D-2. 予測の再現性(SC-006・INV-M2/M3)

期待:
- 同一入力に対する予測が**ビット一致**
- member の順序を変えた manifest が同一性検査で弾かれる

### D-3. 演算順序のパリティ

期待:
- 評価経路と serving 経路が golden fixture で **bit 一致**
- 「校正 → 平均」に入れ替える mutation が落ちる

### D-4. 欠落時の fail-closed(INV-M1/M5)

期待:
- 宣言 `k` と実ロード数の不一致で **学習時も serving ロード時も**止まる
- member を 1 つ壊す / timeout させる → **部分平均せずに止まる**
- k 個すべての hash 照合が終わるまで serving が ready にならない

### D-5. 判定手順の正しさ(SC-003 / SC-004)— **結果を見ずに検証する**

> 分散が減る方向の変更は、バグがあっても「良い結果」に見えて気づけない。
> **実データで CI が狭くなったことを成功の証拠にしてはならない。**

| 種別 | 内容 | 合格条件 |
|---|---|---|
| 配線 | 完全 clone(候補 = 基準、差が恒等的に 0) | ADOPT が原則ゼロ |
| **境界帰無** | 真の効果が**ちょうど δ** の合成データ | ADOPT 率がゲート定義に対応する名目率(片側なら 2.5%)以下 |
| 被覆率 | 既知の効果(0, ±δ, δ の直上/直下, 実務的な小・中効果)を注入 | 真値の被覆率が 95% を下回らない |

DGP には最低限、開催日内相関・開催日ごとのレース数不均一・異分散・heavy tail を含める。

**診断であり合否根拠にしてはならないもの**: v4 に対する CI 幅の比、点推定の差、
fold ごとの係数や CI 幅、bootstrap SE と経験 SD の比、placebo 共変量、leave-one-date-out、
seed を変えた感度分析。**狭い CI も安定した係数も、正しさを証明しない。**

### E-1. 昇格(FR-027)

採否ゲート(winner NLL・top2/top3・ECE の非劣化)を通す。
**CI が狭くなることを昇格根拠にしない。**

報告には必ず [ensemble.md](./contracts/ensemble.md) の但し書きを添える。

---

## 全 Phase 共通: 回帰

期待:
- eval / training / serving の既存スイートが緑
- ruff クリーン
- **DB マイグレーションが発生しない**(スキーマ不変)
- API / OpenAPI / 買い目 / 確率導出(009)が不変
