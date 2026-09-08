# 129 結果レビュー(2026-09-09・Claude 実行)

codex が書いた spec/plan/契約と build・loader・補正・確認の実装を正として、未着手だった実行段(6 モデル本学習・bundle 確定・shadow・検出力・確認 preflight)を進めた。**候補は完成し shadow 専用 bundle として使える状態**。**採用判定は未実施(NOT_READY)**で、本番 active は不変。

## 完成したもの

| 項目 | 結果 |
|---|---|
| 6 member 本学習 | 全 6 本 COMPLETE。fit 934〜1,024 秒/本(2 worker 並列・実 wall 約 55 分)、ピーク RSS 7.8〜9.9 GB/worker |
| 学習母集団 | 2007-01-06〜2026-08-23・67,923 レース・957,997 行(111 snapshot)・OOF 8 ブロック・weight mask 0.5・TE 10 |
| 保存往復パリティ | 6 member × 60 probe レース × 3 head=2,649 値ずつ、最大差 0.0(receipt) |
| bundle | `artifacts/129-candidate-mixture-serving/bundle.json` sha256 `28f39715a9c1…`・bundle_id `129-125_new_joint_mixed6_v1`・係数は 125/118 の保存 2026 係数(fit 窓 2025 末)を出典 SHA つきで固定 |
| anchor 凍結 | lgbm-094-cap900(active)+ stage 割引 λ2=0.85839/λ3=0.71566(`before_date=2026-08-24` で一度だけ fit・n2=9,170)。`anchor/anchor.json` sha256 `80fd7734…` |
| 旧研究演算の再現 | annual-serving-parity: 23,030 レース・全 head 最大差 1.9e-15(codex 実行済・PASS) |

## rehearsal(締切後 4 開催日・preweight・bundle にとって真の OOS)

同一 as-of 入力(同日体重 3 列を両者 NaN)で候補と anchor を並走。144 レース・全レース入力 SHA 一致・normalised 0。

| 開催日 | レース | 候補 − anchor(winner NLL) |
|---|---:|---:|
| 2026-08-29 | 36 | −0.01273 |
| 2026-08-30 | 36 | −0.00370 |
| 2026-09-05 | 36 | +0.01540 |
| 2026-09-06 | 36 | +0.00705 |
| 合計 | 144 | **+0.00151**(候補 2.0784 / anchor 2.0769) |

日クラスタ influence SD は 0.0123。4 日では符号すら定まらない(素朴 SE 0.0106)。1 位一致は 128/144。**研究の −0.009(対 raw138 seed42・全情報・2020-2026)を、実提供 anchor・発走前条件・将来レースにそのまま持ち込めるとは言えない**。これは判定ではなく、確認窓の設計に使う分散の実測に過ぎない。

## 検出力計画(`evidence/power-plan.json`)

移送ノイズ sd_fold 0.001816(k=1)を固定したまま、日 SD 0.0123 から必要開催日数を求めた。

| 効果 | 95% | 98.75% |
|---:|---:|---:|
| ≤0.002 | 床未満 | 床未満 |
| 0.005 | 137 日 | 884 日(上限超) |
| 0.010 | 15 日 | 24 日 |

112 の枠(98.75% CI 上限<0)では **0.01 級の効果しか 2026 年内(残り約 32 開催日)に確認できない**。研究の −0.009 が実提供でも出るなら 24 日で足りるが、rehearsal の +0.0015 はその仮定を支持していない。係数が 2026 用なので窓は 2026-12-28 までに限る。

## 確認 preflight

- `confirmation/manifest-draft.json`(窓なし)→ NOT_READY(窓 3 項 + 独立レビュー 3 件)
- `confirmation/manifest-window-draft.json`(9/12〜12/28・min 24 日・power_effect 0.01)→ **NOT_READY: 独立レビュー 3 件のみ未充足**(`evidence/confirmation-readiness-window.json`)
- 予約は行っていない。共通台帳は不変。

未充足の 3 件は `reviewer_role: independent` の adequacy review。**codex は使用上限(2026-09-15 まで)で起動できず**、偽の review は作らない。復旧後に power/noise/compatibility の各 evidence をレビューさせ、PASS なら `reserve` へ進む。

## 将来レースの待機状態

DB の最新開催は 2026-09-06 で将来レース 0 件(`shadow/waiting-state.json`)。次の開催日(9/12・13)の出馬表取込後、体重公表前に `capture --classification prospective` を手動で流す。発走後・結果ありは prospective として拒否される。

## 実装中に直した欠陥

1. `mixture_model._load_member` が `train_from == '2007-01-01'` を要求していたが、population は実際の初日 `2007-01-06` を記録する → 2007 年 1 月内に有界化。組み立ては `.partial` に書いて全 member 検証後に rename する形に変更(検証前に名前を確定しない)。
2. serving の特徴行列(140 列)に `race_date` が無く `prepare_race_inputs` が落ちた → shadow が検証済みの対象日を付与(既存列がある場合は一致を要求)。
3. `mixture_shadow.py`(T016)は test だけ存在し本体が無かった → 実装(freeze-anchor / capture / develop)。

## 言えないこと

- 候補が本番より良いこと。rehearsal 4 日は符号未定。
- rehearsal が「発走前の DB 状態」であること。as-of は結果確定後の DB から組んでおり、出馬表の訂正等の遡及を含みうる。
- 6 member の再構築分散。ノイズ移送は単一モデル SD をそのまま使う保守的な仮定。
