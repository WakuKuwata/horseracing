# Implementation Plan: arm E 系モデルの OOF attestation 対応

**Branch**: `main`(既定 opt-in・production 挙動は既定でバイト不変) | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/108-arm-e-attestation/spec.md`

## Summary

074/076/078 の校正 manifest スタックは実装済みだが、生成済み manifest が旧世代(lgbm-063)に
束縛され、現行 active が arm E 系(lgbm-094-cap900)に交代したため activation loader が正しく
拒否し、本番は実行時 fit のまま動いている。原因は attestation 層が arm E を表現できないこと
(検証が `calib_frac ∈ (0,1)` と非空 `split_unit` を要求し、`n_oof_blocks`/weight mask の
置き場所がなく、再構成が単純構成の予測器しか組めない)。本 feature は attestation を
**方式別分岐 + 条件付きキー挿入**で拡張し(旧世代の digest はバイト不変)、現行 active の
manifest を生成して全経路の activation を実データで検証する。

**期待効果は小さい**(win バイト不変・精度と EV は不変・実体は表示 top2/top3 の校正正統化)。
やる理由は基盤が今後の全世代で恒久不活性になることの解消。**校正 verdict がどちらも
非採用でも成功**(FR-010)。

## Technical Context

**Language/Version**: Python 3.12(uv 管理)

**Primary Dependencies**: 既存のみ。`training`(legacy_attest / calib_split / oof_generate /
calib_manifest)・`probability`(calib_activation / oof_bundle)・`eval`(hashing / splits)・
`serving` / `betting` / `live` / `ops`(activation の消費側・**結線は 076 で完了済み**)

**Storage**: PostgreSQL 16(読み取り)+ content-addressed disk artifact(`artifacts/` 配下)。
**スキーマ変更・migration なし**

**Testing**: pytest + testcontainers。digest 不変の golden test・方式別検証の fail-closed・
再構成の忠実性・既定 OFF のバイト不変・活性化時の監査記録

**Target Platform**: ローカル macOS(OOF 再生成は nohup バックグラウンド)

**Project Type**: 基盤整備 feature(attestation 層の拡張 + 1 回の測定実行 + 実データ検証)

**Performance Goals**: OOF 再生成 3〜5 時間(D5 の見積・**1 fold 実測で確定してから本実行**)。
それ以外の経路に性能要件なし

**Constraints**: 旧世代 attestation の payload/digest はバイト不変・改竄検出と世代取り違え検出は
緩めない・既定は opt-in のまま・win 予測バイト不変

**Scale/Scope**: attestation 層の分岐追加(training 1 モジュールが主)+ OOF 再生成 1 回
(19 fold・171 booster fit)+ 実 DB での activation 検証

## Constitution Check

- [x] **I. データ契約**: raceId 契約・ID 結合の追加なし。attestation は既存 metadata を読むのみ
- [x] **II. リーク防止**: OOF 再生成は既存の strict-past fold 機構をそのまま使う(新しいリーク面
  ゼロ)。校正パラメータ・digest をモデル特徴に還流しない(076 の leak-guard が既に固定)。
  **weight mask を再現できない場合は fail-closed**(黙って mask なしで走らせない・FR-006)
- [x] **III. 評価先行**: 校正 verdict は 074 の凍結 gate-config で測る。**探索・調整・窓の
  選び直しを禁止**(FR-007/D6)。verdict は三値で記録し、非採用でも feature は完了(FR-010)
- [x] **IV. 確率整合性**: win はバイト不変(FR-017)。stage 割引は top2/top3 のみに作用し
  既存の適用経路(049)をそのまま使う。009 の導出規約は不変
- [x] **V. 再現性・監査**: attestation digest・manifest digest・logic_version への識別子記録は
  既存機構を維持。**旧世代 manifest と過去 verdict は append-only で不変**(FR-009)
- [x] **VI. feature 分割規律**: UI なし・スキーマ/API/OpenAPI 不変・migration なし。
  既定を有効化に切り替える判断は本 feature のスコープ外(FR-015)
- [x] **品質ゲート**: codex に設計レビューを依頼(D8・4 観点)。採否は research D8 に追記

## Project Structure

### Documentation (this feature)

```text
specs/108-arm-e-attestation/
├── spec.md
├── plan.md              # this file
├── research.md          # D1-D8(全て実コード確認済み)
├── data-model.md
├── quickstart.md
├── contracts/
│   └── attestation.md   # payload の方式別スキーマ・検証規則・再構成契約
├── evidence/            # 実行後: 1 fold ETA・OOF bundle 情報・verdict・活性化検証
└── tasks.md
```

### Source Code (repository root)

```text
training/src/horseracing_training/
├── legacy_attest.py     # 主変更: 方式別の payload 構築・検証・再構成分岐
│   #   - payload に arm E キーを条件付き挿入(legacy は既存とバイト同一 → digest 不変)
│   #   - internal_calibration の検証を校正方式で分岐(D2・緩和でなく方式別厳格化)
│   #   - 再構成を方式で分岐し arm E factory を組む(n_oof_blocks / mask rate・seed 必須)
└── calib_split.py       # 1 行是正: to_servable が calibrator_degenerate を明示的に上書き(D4)

training/tests/unit/     # digest golden・方式別検証・再構成忠実性
probability/ serving/ betting/ live/ ops/ api/   # 変更なし(076 の結線を消費するだけ)
db/ front/ admin/ features/                       # 差分ゼロ
```

**Structure Decision**: 変更は `training/legacy_attest.py` に集中する。recipe 層は既に
arm E を完全表現でき(`weight_mask_rate/seed` + hash 既定省略機構)、activation 側の結線は
076 で完了しているため、**足りないのは attestation の表現と分岐だけ**という研究結果に従う。

## 主要設計判断(research 参照)

| # | 決定 | 根拠 |
|---|---|---|
| D1 | arm E キーは条件付き挿入(legacy は payload バイト同一) | 無条件追加は旧世代 digest を壊し既存 manifest を参照不能にする |
| D2 | 検証を校正方式で分岐(OOF は calib_frac==0.0 かつ split_unit==null を**要求**) | 緩和すると「holdout ゼロなのに legacy を名乗る」不正形が通る |
| D3 | `n_oof_blocks` と mask rate/seed を必須記録 | 既定 3 ≠ 実際 8。記録しないと黙って別モデルになる |
| D4 | attestation は権威ある場所のみ読む + 報告バグを 1 行是正 | `calibrator_degenerate: true` は base の残留値。真値は構造的に False |
| D5 | 束の保存形式は変えず、1 fold 実測の中断点を置く | 保存量を変えると activation の消費側と食い違う |
| D6 | 窓・基準は 078/074 の凍結値を流用 | 窓の選び直しは結果に合わせた選択(FR-007 違反) |

## Complexity Tracking

なし(Constitution Check 全 PASS・migration なし・スキーマ/API 不変・変更は 1 パッケージに集中)。
