# Implementation Plan: ニックス(種牡馬×母父の配合相性)特徴

**Branch**: `090-nicks-cross-features` | **Date**: 2026-08-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/090-nicks-cross-features/spec.md`

## Summary

既存の血統特徴は主効果(種牡馬適性・母父適性・デビュー×血統・系統)しか持たず、「この父と
この母父の組み合わせが特別に走る/走らない」という配合相性が未測定。これを**主効果からの
残差**(独立性期待値に対する対数比)として 2 列だけ特徴化し、事前登録ゲートで一度だけ
決着させてこの軸を閉じる。**新規データ取得ゼロ**(父名・母父名は 100% 充足済み)。

計画段階の実測で、配合セル間のばらつきは**標本ノイズを 25〜39% 上回る実在成分**を持ち、
1 標準偏差が基準勝率の約 19% と判明した(research D2)。素材は実在する。未解決なのは
**既存の 130 以上の入力に対する増分の有無**であり、それは事前登録ゲートのみが決める。

**インブリードはスコープ外**(3 代到達率 2.1% で実装不能。血統データ取得の別 feature へ)。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: pandas / numpy(新規依存なし)。LightGBM は採否判定の学習のみ

**Storage**: PostgreSQL 16(読み取りのみ)+ 特徴の事前生成 parquet。**スキーマ変更なし**

**Testing**: pytest(features 単体・leak-guard・parity)、実 DB でのカバレッジ監査と
採用判定

**Target Platform**: ローカル(操作は CLI)

**Project Type**: 既存 monorepo の features パッケージ内で完結(training / eval / api / front は
無改修。serving は**本体無改修**で compat テストのみ追加)

**Performance Goals**: as-of 集計の pass 追加を最小に抑える(L0 1 pass + L1 1 pass +
marginal 3 pass)。**072 投影(`target_race_ids`)に対応**し、072 投影下の単一レース予測
(現状 ~24 秒)を悪化させない

**Constraints**: 新規ソース列ゼロ → `source_fingerprint` 不変・materialize-safe。定数は
モジュール定数(実行時引数にしない)= ビット parity の前提。既存血統特徴は不変

**Scale/Scope**: 特徴行列 952,862 行 × 2 列追加。新規モジュール 1 + 単体テスト 1 +
registry/build 結線 + FEATURE_VERSION bump

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: PASS — raceId 契約・ID 結合規約に触れない。集計キーは**名前**
  (父名・母父名)であり、026 で確立した規約(ID 列は 4.1% しか無いため名前キー + NFKC 正規化)
  を継承する。推測結合は行わない。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — strictly-before + 同日除外 + **自馬除外**
  (026 の機構を交差セルへ適用)。オッズ・対象レース結果・未来レースは非入力。
  leak-guard テストで機械固定(INV-N1/N2)。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — 判定は既存の評価契約(068)+ 部分集団ガード
  (069)を**実装前に凍結**して 1 回実行。定数(λ / 閾値)は実装前固定で結果を見た調整を禁止
  (INV-N5)。本番 pl_topk paired 評価で判定(binary 打ち切りは採否に用いない)。
- [x] **IV. 確率整合性**: PASS — 確率の導出・正規化には触れない。特徴の追加のみ。
- [x] **V. 再現性・監査**: PASS — 定数はモジュール定数で凍結、カバレッジ監査を出力、
  判定設定をハッシュ照合、不採用時も負の結果としてモジュールとテストを保全。
- [x] **VI. feature 分割規律**: PASS — スキーマ・API・画面いずれも不変。FEATURE_VERSION の
  bump は純加算 + compat pin で正当化(058/061 方式)。
- [x] **品質ゲート(codex second opinion)**: PASS(3 回目で取得成功)— 1〜2 回目は
  AGENTS.md の並走指示による derail と読み込み時間切れで SIGTERM。**「あなた自身が呼ばれた
  codex でありサブレビュー禁止」+「リポジトリを読まず、必要情報はプロンプトに埋め込み、
  質問を 2 問に絞る」**で完走した(再現手順は research D10)。**指摘 2 件を採用し当初案を
  撤回**: λ=5.0 の流用を廃し実測導出の λ=350 へ、単段縮約を入れ子部分プーリング
  (leave-child-out 親)へ。採否は research D10 の対応表。

## Project Structure

### Documentation (this feature)

```text
specs/090-nicks-cross-features/
├── plan.md              # This file
├── research.md          # D1-D10(実測 + 設計判断 + セルフレビュー)
├── data-model.md        # 2 列の定義・算出手順・版管理
├── quickstart.md        # 検証手順(SC 対応表つき)
├── checklists/requirements.md
├── contracts/
│   └── feature-columns.md   # 列契約・INV-N1..N10・採用判定契約・判定コマンド(正本)
├── gate-config.json     # T013 が作成し凍結する判定設定
└── tasks.md             # (/speckit-tasks で生成)
```

### Source Code (repository root)

```text
features/src/horseracing_features/nick_cross_features.py   # 新規: 算出の唯一の正本
features/src/horseracing_features/registry.py              # 2 列 + group `nick_cross` 登録
                                                            # FEATURE_VERSION 018 → 021
features/src/horseracing_features/materialize.py           # build_asof_features へ 1 箇所結線
features/src/horseracing_features/cli.py                   # nick-coverage-audit サブコマンド追加
features/tests/unit/test_nick_cross_features.py            # 新規: 手計算 fixture / リーク / 決定性
features/tests/unit/test_projection_blocks.py              # 072 投影 parity を追加(cross-entity 群)
serving/tests/unit/test_nick_compat.py                     # 新規: features-018 が 021 下で serve 可能

# 参照するのみ(変更しない)
features/src/horseracing_features/pedigree_features.py     # _other_offspring(自馬除外)を再利用
features/src/horseracing_features/pm_conditioned.py        # λ 縮約の前例
```

**Structure Decision**: features パッケージ内で完結する。as-of 特徴の単一源
(`build_asof_features`)に 1 箇所だけ結線し、事前生成経路と逐次計算経路が同じ関数を通る
(025 の規約)。training / eval / serving / api / front は無改修。

## 設計の核(research.md 要約)

1. **残差の定義(D3)**: `expected = p_sire × p_damsire / p_overall`(独立性ベースライン)、
   `nick_lift_log = log(縮約済み交差率) − log(expected)`。実測で父実績とほぼ無相関
   (−0.126)。ただしこれは乗法的独立性からの乖離であり**既存モデル全体への直交性では
   ない**(codex #3)。生の交差率は主効果と強相関(0.453)で 032 の「単純積は冗長」を
   繰り返すため不採用。
2. **入れ子の部分プーリング(D5/D5a・codex #2 で当初案を撤回)**: L0(父×母父)を L1
   (父×母父系統)の推定値へ、L1 を独立性期待値へ、**閾値ではなく連続的に**寄せる。
   親は **leave-child-out**(子セルの観測を除く)。硬い閾値 `MIN_CELL` は廃止。
   **λ は 070 の 5.0 を流用せず、実測の分散分解から導出した 350**(λ=5 では n=20・0 勝の
   セル[帰無確率 22.8%]に −1.609 が付き左裾がノイズ化する)。系統×系統の階層は設けない。
3. **列は 2 列(D7/D10)**: `nick_lift_log` + `nick_obs_count`。当初案の `nick_level` は
   obs_count と強相関のため却下。070 で bundle が全 REJECT された前例を踏まえ帰属面を最小化。
4. **頭数交絡は残差で消える(D4)**: 残差と平均頭数の相関 +0.004(生の交差率では −0.059)。
   成果指標は勝利のままとし、頭数正規化への変更は不要。
5. **欠損と 0 の区別(D6/INV-N9)**: キーが無い行のみ NaN。観測が薄い行は親縮約値 +
   `obs_count = 0`(NaN にするとカバレッジの穴が 0.07% → 3.2% に広がり、木が「情報なし」を
   学習する機会を失う)。
6. **版管理(D9)**: features-018 → **features-021**(019 は焼却済み・020 は 088 が予約)。
   新規ソース列ゼロで `source_fingerprint` 不変 = materialize-safe。

## 実装順序(MVP と撤退の設計)

**null-is-success 型のため、判定に最短で到達する順序を採る**:

1. 算出モジュール + 単体テスト(定義・リーク・決定性を固定)
2. 結線 + FEATURE_VERSION bump + parity 検証 + カバレッジ監査
3. 判定設定を凍結 → 本番 pl_topk paired 評価を 1 回
4. 判定に応じた後始末(不採用なら bump と結線のみ revert・モジュールは保全)

**撤退の手順を先に決めてある**ことが重要で、判定結果を見てから「どう扱うか」を考える余地を
残さない(憲法 III)。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
違反なし(codex second opinion は 3 回目で取得済み・research D10)。

その他の違反なし(スキーマ変更なし・新規パッケージなし・新規依存なし・API/画面不変)。
