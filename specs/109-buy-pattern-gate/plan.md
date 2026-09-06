# Implementation Plan: 買い目パターン採否ゲート

**Branch**: `109-buy-pattern-gate` | **Date**: 2026-09-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/109-buy-pattern-gate/spec.md`

## Summary

ユーザーの問い「精度が限界に近いとして、"何がどうなったら買う/買わない" の購入パターンで回収率 100% を超えるものがあるか」を、**判定ゲートを先に凍結し、自己検証で検出力と偽陽性率を測ってから、凍結した 393 本のパターン族を screening(発見期 2008-13 → 資格期 2014-18)→ 確認窓(2019-26)の順で一度だけ流す** null-is-success 型の測定 feature。材料は 108 の全史 OOF 束(64,542 レース・現行 active 系譜 arm E・strict-past)と DB の closing オッズ・結果で、**再学習ゼロ・新規取得ゼロ・製品差分ゼロ**。verdict は 5 状態(ADOPT_CLOSE / NOT_ADOPTED / RULED_OUT δ=0.02 / NO_DECISION / SCREENED_OUT)で、ADOPT_CLOSE は「closing の価格で選べば歴史的に回収率 1 を超えた」ことしか示さず「買える」の証明ではない(価格リーク=FR-023)。

**期待値の開示**: t/q の過去最良 1.11 に対し必要 1.257。確認窓の 80% MDE ≈ 0.075〜0.08(codex 概算)なので真の回収率 1.05 の候補があっても 3〜4 割しか検出できない。この限界は verdict に明記する。

## Technical Context

**Language/Version**: Python 3.12(既存 `eval/` と `scripts/` の環境。`cd training && uv run python ../scripts/…` の 107 型 driver)

**Primary Dependencies**: numpy(eval 側の純 scorer は numpy と dict のみ。**eval 環境に pandas/pyarrow は無く追加しない**)。pandas / pyarrow / SQLAlchemy は `training` 環境で動く `scripts/` の driver 側で使う。新規依存なし。`horseracing_eval.bootstrap`(ratio bootstrap 既存)・`horseracing_eval.decision.gate_config_hash`・`horseracing_eval.hashing.stable_hash`・`horseracing_eval.dispersion_bands.normalized_entropy`・`exotic_portfolio` の降格/Holm 規則を流用。

**Storage**: 読み取りのみ(DB `races / race_horses / race_results`、OOF 束 JSON)。書き出しは `specs/109-buy-pattern-gate/`(gate-config / patterns / population / summary JSON / survivors / selftest / verdict / 生存者と対照の確認窓 bets)と `artifacts/109/`(gitignore 済み: 行スナップショットと窓ごとの全賭け行 parquet=10⁷ 行級。`evidence_refs` の sha256 で参照)。DB 書き込みなし・migration なし。

**Testing**: pytest(`eval/tests/unit/` は numpy fixture のみ。CLI 統合テストは `scripts/tests/` に置き training 環境で実行、実 DB と束が無ければ skip=eval の testcontainer conftest と分離)。契約テスト: 凍結 hash の fail-closed・帰無中心化 p 値・Holm・降格・状態機械・結果並べ替え不変・第二集計一致・ブロック bootstrap(ブロック=1 日で既存関数と 1e-12 一致・同一入力の再呼び出しはビット一致)・合成注入のレース内排他性。

**Target Platform**: ローカル macOS + Docker postgres(既存 `scripts/stack.sh`)。

**Project Type**: 測定 CLI(scripts driver)+ eval ライブラリ 2 モジュール。

**Performance Goals**: screening 393 本 × 20,000 反復を数十秒〜数分(日別ベクトル化・research D11)。自己検証はサイズ 5,000 × 内側 20,000 × 5 本と検出力 6 点 × 5 本 × 2,000 × 5,000 ではなく、判定を本番と同じ 3 分析版で行うため **4 時間級**(research D11 で算術を訂正。外側 20 反復の実測から外挿し、対象ごとの checkpoint から再開可能)。ベクトル化した新 bootstrap 関数が唯一の実装。

**Constraints**: 選択リークの構造的封鎖(hash 4 種の照合前に実行不能・`freeze / screen / confirm` は clean tree 必須で各段の生成物を次段の前にコミット・確認窓は生存者集合が git に固定されてから一度のみ・verdict append-only)。DB は常時動くので `screen` が行スナップショットを固定し `confirm` は再読しない。製品コード差分ゼロ。

**Scale/Scope**: 母集団 ≈ 64,000 レース / 約 900,000 賭け候補行。パターン 393 + 対照 4。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: `race_id` 12 桁・2008 年以降のみ(束の最初の fold が 2008)。ID 横断結合なし(DB 内の `race_id, horse_id` 結合のみ)。賭けの正準キーは `(race_id, horse_number)`。ラベルは内部識別子のみ。PASS
- [x] **II. リーク防止**: 述語は結果を読まない(結果並べ替え不変テスト)。モデル p は 108 の strict-past OOF。過去走由来の述語(間隔・前走着順・叩き 2 走目)は `race_date` の厳密前(SQL lag over started rows)。closing オッズは結果リークではないが **価格リーク**として `available_at=closing` で明示し ADOPT_CLOSE の意味を限定(FR-023)。評価派生値(ROI・verdict)はモデル特徴に戻さない(製品差分ゼロ)。PASS
- [x] **III. 評価先行**: 本 feature はモデル/特徴を変更しない測定。ゲート(US1)をパターン(US2/3)より先に凍結し、合成注入で検出力/偽陽性率を先に測る。窓は日付で分割し確認窓は選択に不使用。baseline(対照 4 本)同走。ECE は本 feature の対象外(校正でなく回収率の判定)= N/A 明記。PASS
- [x] **IV. 確率整合性**: 束の p は生成時に Σ=1 検証済み。本 feature は p を再正規化せず(順位・比・分位のみ)、取消馬は started 限定で除外。PASS
- [x] **V. 再現性・監査**: 凍結設定・パターン族・母集団・生存者の hash、seed、反復数、束 digest を verdict と証拠に保持。コード SHA は 2 種を区別して記録する(凍結 `code_sha`=列挙コード / `run_code_sha`+dirty フラグ=判定を実行したコード)。証拠から点推定・CI・p 値をビット一致で再計算(D2)。オッズは DB の最新値(closing)で上書き方針と整合。疑似値なし(実オッズ×実結果)。PASS
- [x] **VI. feature 分割規律**: UI なし・API/DB 契約変更なし・migration なし。ADOPT_CLOSE でも製品結線は後続 feature(FR-019)。PASS
- [x] **品質ゲート**: spec 段階 codex 15 件全採用([codex-review.md](codex-review.md))。plan 段階の再レビュー(D4 族列挙 / D6 注入 / D10 状態機械)は research D14 に記録。PASS

## Project Structure

### Documentation (this feature)

```text
specs/109-buy-pattern-gate/
├── spec.md
├── plan.md              # this file
├── research.md          # D1-D14
├── data-model.md
├── quickstart.md
├── codex-review.md      # spec 段階(15 件)+ plan 段階の追記
├── contracts/
│   ├── gate-config.md   # 凍結設定の形と hash 規約
│   ├── pattern-spec.md  # パターン族の定義形式・述語・available_at
│   ├── evidence.md      # 証拠 parquet / verdict JSON の列と状態機械
│   └── cli.md           # driver のサブコマンドと fail-closed 順序
├── gate-config.json     # 凍結(実行前)
├── patterns.json        # 列挙生成物(hash を gate-config に転記)
├── population.json      # 母集団 hash と流れ図(実行時生成・凍結)
├── evidence/            # screening-discovery / screening-qualification / confirmatory / selftest
├── verdict.json         # append-only
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
eval/src/horseracing_eval/
├── buy_patterns.py          # NEW: 軸・帯・述語・列挙(patterns.json 生成)・available_at
├── buy_pattern_gate.py      # NEW: 純 scorer = 母集団固定・適用・集計・ratio bootstrap・
│                            #      帰無中心化 p・Holm・降格・状態機械・第二集計・自己検証の注入
└── bootstrap.py             # +1 関数: race_block_ratio_bootstrap_ci_v1(week|month)
eval/tests/unit/
├── test_buy_patterns.py     # 列挙件数 393・重複 9 本の不在・欠損→no bet・結果並べ替え不変・available_at
├── test_buy_pattern_gate.py # p 値・Holm・降格・状態機械・第二集計・hash fail-closed・注入の排他性
└── test_bootstrap_block.py  # ブロック=1 日で既存日クラスタ版と 1e-12 一致・再呼び出しビット一致
scripts/tests/
└── test_buy_pattern_gate_cli.py  # training 環境・実 DB と束が無ければ skip: 一時 --spec-dir + --smoke で fail-closed 8 点(T041 が正本)
scripts/
└── buy_pattern_gate.py      # NEW driver: freeze / selftest / screen / confirm / recompute(--spec-dir / --smoke)
```

**Structure Decision**: 純 scorer は `eval/`(betting/training 非 import。`policy_gate.py` と同じ規律)、DB と束のロードと窓・hash 照合・書き出しは `scripts/` の driver(107 と同型)。製品パッケージ(`db/ api/ front/ betting/ probability/ serving/ features/ ops/ admin/ training/src`)は差分ゼロ。

## Complexity Tracking

該当なし(憲法違反なし)。

## Phase 構成と中断点

| Phase | 内容 | 中断点 |
|---|---|---|
| 0 | Foundational: 行構築・母集団・bootstrap・`buy_patterns.py` 列挙 → コミット → `freeze`(`patterns.json`・gate-config 転記)→ コミット | freeze は clean tree 必須なので直前にコミット |
| A | US1: `buy_pattern_gate.py` 純 scorer + テスト、`selftest`(凍結済み 393 本の実マスク・本番と同じ判定関数)→ `evidence/selftest.json` → コミット | **自己検証でサイズの下側限界が 2.5% を超えるならゲートを直すまで先に進まない**(直せるのは判定統計と注入のみ・列挙と導出列は不可) |
| B | US2: `screen` 実行(clean tree)→ 行スナップショット固定 → 生存者 0〜5 → コミット | — |
| C | US3: `confirm` 実行(clean tree・hash 4 種照合・行スナップショット照合・対照は常に集計)→ 感度 → 状態機械 → `verdict.json` | 生存者 0 でも実行(対照の確認窓値と verdict のため) |
| D | 後始末: spec への結果転記・memory 更新・(ADOPT_CLOSE 時のみ)後続 feature への引き継ぎ条件の記録 | — |

**凍結の順序(選択リーク封鎖)**: gate-config と patterns(Phase 0 の freeze・列挙は結果を読まない)→ population(screen 実行時に生成し hash を固定)→ survivors(screen 完了時)。確認窓は survivors が固定されてからしか実行できず、実行後の再実行は verdict 既存パスの拒否で封鎖。

## codex(plan 段階)

14 件・採用 13 / 部分採用 1 / 不採用 0。採否表は [codex-review.md](codex-review.md) 第 2 表、反映先は research D14。最大の変更: 族 374→393(重複 9 除去・C×EV 28 追加)、厳密過去分位、RULED_OUT を futility 検定(帰無 ROI ≥ 1.02)として定義、サイズ検定の内側 bootstrap を 20,000 に、KL 最小傾きの命名と λ₀ の解き直し、状態の優先順位固定。
