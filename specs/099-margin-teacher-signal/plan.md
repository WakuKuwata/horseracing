# Implementation Plan: margin-aware 教師信号(PL top-k ステージ損失の着差減衰)

**Branch**: `099-margin-teacher-signal` | **Date**: 2026-08-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/099-margin-teacher-signal/spec.md`

## Summary

PL top-3 教師信号のステージ 2,3 を着差で減衰する(V1・`g(m)=clip(m/0.2s, 0.25, 1.0)`・
spike で凍結)。production 実装は「spike で検証済みのパターンの移植」であって新設計ではない:
`pl_topk_objective` に `stage_scales=None` を追加(None=現行と勾配ビット一致)、margin は
`MKT_ODDS` と同じ**ラベル側 aux 列**契約で TrainingMatrix に載せ、`ModelRecipe.margin_teacher`
(None=hash 不変)で識別する。採否は v4 標準の confirmatory paired-eval(rounds=900・
全期間 walk-forward・両アーム arm E 構成・差は教師信号のみ)を事前登録して一度だけ決める。
ADOPT でも candidate 止まり・REJECT なら結線 revert + モジュール非結線保全。

## Technical Context

**Language/Version**: Python 3.12(training / eval のみ変更。features/serving/betting/api/front 無改修)

**Primary Dependencies**: LightGBM 4.x(custom objective)・pandas・numpy・SQLAlchemy 2.0

**Storage**: PostgreSQL 16(読み取りのみ。スキーマ変更ゼロ・migration なし)

**Testing**: pytest(training 単体+実 DB 統合)。既存スイートは無改修で緑が要件(SC-001)

**Target Platform**: ローカル CLI(学習・評価)。serving 経路は不変

**Project Type**: ML 学習パイプラインの教師信号拡張 + 事前登録採用ゲート

**Performance Goals**: 学習コスト増ゼロ近傍(スケールは fit 前に 1 回計算・fobj 内は既存の
乗算に係数が乗るだけ)。ゲート実行は既存 paired-eval と同オーダー(見積もりは実績比: 097 の
3 窓 2 アーム ≈2h に対し、全期間 19-fold・rounds 900・2 アームで**十数時間**を見込み nohup)

**Constraints**: margin-aware OFF は 1 バイトも変えない(勾配ビット一致・recipe_hash 不変・
既存テスト無改修)。margin はラベル側のみ(feature_cols / feature_hash / feature_snapshots
非混入)。FEATURE_VERSION 不変。V1 の形・定数は spike の凍結値で、ゲート時に調整不能

**Scale/Scope**: 変更 3 ファイル圏(cond_logit.py / win_model.py / predictor.py)+ recipe/
dataset/CLI の配線 + gate-config + driver assert。全期間評価 ~26k レース

## Constitution Check

- [x] **I. データ契約**: race_id 12 桁・2007 年以降のみ・id_mappings 経由。margin は
  `race_results.finish_time`(確定結果)由来でラベル側のみ — ID 契約に触れない。PASS
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: margin は結果由来だが **finish_rank と同一の
  ラベル側契約**(教師信号にのみ使用・特徴量に非混入・leak-guard テストで機械固定=FR-004)。
  予測経路は margin を一切読まない(教師信号は fit 時のみ)。walk-forward 境界は既存
  harness のまま。PASS
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 採否は v4 confirmatory paired-eval のみ
  (gate-config 事前凍結・hash 照合 fail-closed・組込み三値 verdict が正本)。spike は
  de-risk であって採用証拠ではないと spec に明記済み。PASS
- [x] **IV. 確率整合性**: 予測経路無変更(raw → softmax → 校正 → クリップ → 再正規化は
  現行のまま)。教師信号の重み変更は確率の導出規則を変えない。PASS
- [x] **V. 再現性・監査**: recipe_hash が margin-aware をモデル同一性として持つ(OFF は
  既存 hash 不変)。fit_info / artifact metadata にステージ別スケール統計を記録(FR-007)。
  gate-config は hash つきで凍結。PASS
- [x] **VI. feature 分割規律**: UI なし。スキーマ・API 契約不変。P0 未決なし。PASS
- [x] **品質ゲート(codex second opinion)**: **取得成功**(本日 6 回のインフラエラー死の
  後、対象を 6 ファイルに絞った再投入で完走)。指摘 8 件中 7 採用・1 部分採用 — うち P0 の
  3 件(arm E 系 recipe_hash の back-compat 破れ / `_make_base` 明示渡し / silent no-op
  検査)は設計に反映済み。全採否は research D9 の表

## Project Structure

### Documentation (this feature)

```text
specs/099-margin-teacher-signal/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0(D1-D9 設計決定+codex 採否)
├── data-model.md        # Phase 1(スケール/aux 列/統計の定義と不変条件)
├── contracts/
│   ├── teacher-signal.md    # INV-MT1..MT9(objective/aux 列/レシピ契約)
│   └── adoption-gate.md     # 凍結ゲートと verdict 分岐の契約
├── gate-config.json     # 凍結値(OOS 実行前・hash 照合)
├── quickstart.md        # 検証手順(selftest → 単体 → smoke → 本実行)
└── checklists/requirements.md
```

### Source Code (repository root)

```text
training/src/horseracing_training/
├── cond_logit.py        # pl_topk_objective に stage_scales=None(spike 実装の移植)
├── win_model.py         # fit/predict に margin_scales 配線(argsort 追随・予測側は不変)
├── predictor.py         # LightGBMPredictor: aux 列スライス→fit へ・fit_info に統計記録
├── dataset.py           # build_training_matrix: margin aux 列 2 本(MKT_ODDS 同型)
├── recipe.py            # ModelRecipe.margin_teacher(None=hash 不変)・hash 省略規則の一本化
├── calib_split.py       # disposition 追加・_make_base 明示渡し・recipe_hash を共有省略規則経由に
└── cli.py               # mteach= セグメント・OOF 分岐の carriage 修正・arms 注入+実効照合・実行前後 assert
training/tests/unit/     # selftest 移植・整列/マスク回帰・SQL バグ形回帰・leak-guard
training/tests/integration/  # 実 DB 形状のスケール統計・E2E 配線 smoke
scripts/margin_teacher_spike.py  # 変更なし(GO の証跡として凍結)
```

**変更しないもの**: features/(FEATURE_VERSION・registry・builder)・serving/・betting/・
api/・front/・db/(スキーマ・migration)・probability/。

## 設計核(research.md D1-D8 の要約)

1. **margin 供給 = ラベル側 aux 列**(D1): `build_training_matrix` が SQL 1 本(LEAD を
   **全完走馬**の CTE で計算し、外側で着順 1..3 に制限 = spike run 1 のバグ形を構造的に排除)
   で race→(s2,s3) を得て、`margin_scale_s2` / `margin_scale_s3` をレース内定数の aux 列と
   して frame に載せる。`MKT_ODDS` と同一契約(feature_cols / feature_hash / snapshots 非
   混入・INV 化)。**行と一緒にスライス・ソート・マスクされる**ので、weight mask / calib
   holdout / argsort との整列は既存機構が保証する
2. **objective**(D2): spike の `margin_pl_topk_objective` を `pl_topk_objective` の
   `stage_scales=None` 引数として本体へ移植。**既存の `offsets=None` を保存**した
   `(group_sizes, ranks, offsets=None, stage_scales=None)`(spike 版は offsets を持たない=
   そのまま写すと market-offset 対応を失う・codex P1)。None は現行と**勾配ビット一致**を
   offsets×weights の 4 象限で表明。発火・中立化・break 規則は不変(FR-005)
3. **WinModel / predictor 配線**(D3): predictor が model_df から aux 列 2 本を取り出し
   `WinModel.fit(margin_scales=(n_rows,2))`。`_fit_softmax` は既存 argsort で並べ、**group 内
   min==max・値域 [0.25,1]・有限・s1==1 を `ValueError` で検証してから**先頭行で
   (n_groups,3) を構成(codex P1: 検証なしの先頭行は破損を隠す)。predict 側は無変更
4. **レシピ**(D4): `ModelRecipe.margin_teacher: str | None = None`(None=省略・"v1" のみ
   受理)。**hash の canonical payload を ModelRecipe に一本化し両 Factory が共有**(codex
   P0-1: 現行 `CalibSplitFactory.recipe_hash` は `meta()` 全体を hash しており、フィールド
   追加だけで arm E 系の既存 hash が全て変わる — 一本化して初めて「既存 hash 全不変」が
   成立。既存 arm E hash のスナップショットテストで固定)。`_RECIPE_FIELD_DISPOSITION` に
   forward を追加し、**`_make_base()` で明示的に渡す**(codex P0-2: disposition は会計で
   あって配線ではない — 忘れると booster が教師信号だけ無視して正常学習する)
5. **ゲート**(D5): 標準 confirmatory paired-eval(068/073/v4)。アーム基底は spec 文字列
   `pl_topk:oof_isotonic[:mteach=v1]`、**rounds 900 / wmask / n_oof_blocks 8 / seed は
   confirmatory 時に gate-config `arms` から両 factory へ注入し、採点前に実効 recipe_meta が
   凍結値と一致しなければ fail-closed**(analyze C1: OOF 分岐は wmask=/params を捨てる穴が
   現存し、放置すると両アームが等しく別物の estimand を測る)。差は教師信号のみ。gate-config
   は本 plan で凍結(完全 hash は contracts/adoption-gate.md)。artifact_kind=
   full_walk_forward(昇格適格)
6. **構造 assert**(D6): 実行**前** = (a) アーム recipe_hash 同一 fail・(b) 実効 recipe の
   canonical payload 差分が厳密に margin_teacher 1 フィールドのみ(codex P0-3)。実行**後**
   (verdict 書き出し前)= 非ゼロ差レース ≥1・candidate fit_info の scale_lt1>0(T017a。
   fold ごとの実行中 assert は Protocol 契約上足さないが、run 後の post-hoc 検査は CLI が
   自分で構築した factory から読めるので契約違反でない)
7. **監査統計**(D7): fit_info / metadata に `margin_teacher` 統計 — **実際の booster fit 行
   ベース**でステージ別 source_available / scale<1 / fire∩scale<1 件数と fireable 平均
   (codex: scale=1.0 の「大差 cap」と「時計欠損中立」を分計)。spike のステージ 3 バグは
   この種の統計でしか発覚しなかった
8. **verdict 分岐**(D8): ADOPT → `--register-candidate` で candidate 登録(evaluate_promotion
   は既に自動 active を防ぐ多重ゲートを持つ)。REJECT → cli/recipe/predictor の結線 revert・
   objective 拡張+テストは非結線保全(062/070/090 同型)・数値を spec 転記

## 正直な限界(spec から再掲+plan 固有)

- 点 −0.00337(rounds=300)に対し総 CI 上限 ≈ −0.001 の際どいライン。097 型 REJECT はあり得る
- rounds=900 との相互作用は未測定(上振れ要因だが前提にしない)
- spike は fit/predict を script 内で複製した(paired 差からは複製の系統誤差が消える設計)。
  本実装は production 経路そのものなので、spike の数値がそのまま再現される保証はない —
  ゲートが唯一の判定
- ゲート実行は十数時間想定。実行中に DB が動く影響は materialized snapshot の pin で排除

## Constitution Check(Phase 1 後の再評価)

Phase 1 成果物(data-model / contracts / gate-config)を書いた後に再確認済み — 違反なし。
リーク境界(II)は contracts/teacher-signal.md の INV-MT 群として機械検証に落ちている。
