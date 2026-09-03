# Implementation Plan: arm E 系モデルの OOF attestation 対応

**Branch**: `main`(既定 opt-in・production 挙動は既定でバイト不変) | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/108-arm-e-attestation/spec.md`

## Summary

074/076/078 の校正 manifest スタックは実装済みだが、生成済み manifest が旧世代(lgbm-063)に
束縛され、現行 active が arm E 系(lgbm-094-cap900)に交代したため activation loader が正しく
拒否し、本番は実行時 fit のまま動いている。原因は attestation 層が arm E を表現できないこと
(**実測では拒否ではなく「捏造して通る」silent fail-open** — 欠落値を既定で補完して
`calib_frac=0.3`/`split_unit=race_count_v1` を記録し、OOF ブロック数と weight mask は落とす。
つまり arm E の証明書が旧世代モデルだと主張する)。本 feature は attestation を
**方式別分岐 + 条件付きブロック挿入**で拡張し(旧世代の digest はバイト不変)、現行 active の
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

**Performance Goals**: OOF 再生成 **5〜8 時間**(D5・171 fit × 112 秒 = 5.32h + 後年 fold の
増分。**1 fold 実測と事前固定した外挿式で確定してから本実行**)。
それ以外の経路に性能要件なし

**Constraints**: 旧世代 attestation の payload/digest はバイト不変・改竄検出と世代取り違え検出は
緩めない・既定は opt-in のまま・win 予測バイト不変

**Scale/Scope**: attestation 層の分岐追加(training 4 ファイル)+ OOF 再生成 1 回
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
- [x] **品質ゲート**: codex 設計レビュー**取得成功**(是正案に対する 2 回目)。
  採用 6 / 部分採用 1 / 不採用 1 を research D8 に記録

## Project Structure

### Documentation (this feature)

```text
specs/108-arm-e-attestation/
├── spec.md
├── plan.md              # this file
├── research.md          # D1-D8(全て実コード確認済み・D2/D3 は analyze で全面訂正)
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
├── legacy_attest.py     # 主変更: 方式別の payload 構築・検証・再構成分岐(経路は完全分離)
│   #   - payload に arm E ブロックを条件付き挿入(legacy は既存とバイト同一 → digest 不変)
│   #   - 検証を 3 分岐(既知 legacy / arm E / 未知は拒否)+ 出荷ビューとの相互整合
│   #   - 欠落値の既定補完を arm E で禁止(silent fail-open の封鎖・D2)
│   #   - 再構成を方式で分岐し arm E factory を組む(protocol version / n_oof_blocks /
│   #     mask rate・seed / 容量 params が必須。calib_frac は requested/effective 分離)
├── oof_generate.py      # **必須変更**(D3a): 旧世代強制の入口から非 legacy 対応へ切替。
│   #   期待 model/feature version を必須引数で受け fail-closed
├── cli.py               # **必須変更**: oof-generate / generate-manifest の既定値と help、
│   #   および manifest 生成の clean-tree 判定の是正(G3)
└── calib_split.py       # 1 行是正: to_servable が calibrator_degenerate を権威値から転記(D4)

training/tests/unit/     # digest golden・方式別検証・相互整合・再構成忠実性
# **共有消費者(挙動が変わりうる・回帰対象)**: ev_weight_run(079)・segment_accuracy_run(082)
#   082 は現行世代で使用不能な既存欠陥がある(D3b)
probability/ serving/ betting/ live/ ops/ api/   # 変更なし(076 の結線を消費するだけ)
db/ front/ admin/ features/                       # 差分ゼロ
```

**Structure Decision**: 変更は `training/` の 4 ファイル(`legacy_attest.py` が主・
`oof_generate.py` と `cli.py` は入口と clean-tree 判定・`calib_split.py` は 1 行是正)。
当初「legacy_attest.py に集中」としたが、**旧世代を強制する入口(D3a)と無効な dirty ガード(G3)を
直さないと着手時点で止まる**ことが analyze で判明したため是正した。
activation 側(076)は結線済みで変更なし。**attestation を共有する 079/082 は挙動が変わりうるので
回帰対象に含める**(D3b)。

## 主要設計判断(research 参照)

| # | 決定 | 根拠 |
|---|---|---|
| D1 | arm E キーは条件付き挿入(legacy は payload バイト同一) | 無条件追加は旧世代 digest を壊し既存 manifest を参照不能にする |
| D2 | **silent fail-open の封鎖**(欠落を既定で埋めず型付きエラー・3 分岐で未知は拒否) | 現状は拒否でなく**捏造して通る**(実測)= 証明書が嘘をつく |
| D3 | **出荷ビューと構成を分離**し、protocol version・`n_oof_blocks`・mask・**容量**を必須記録 | 既定 3 ≠ 実際 8、容量既定 ≠ 900。記録しないと黙って別モデル |
| D3a | `oof_generate` を非 legacy 対応にする | 旧世代強制で現行世代は例外になり着手できない |
| codex | `calib_frac` は requested/effective 分離・protocol version で意味を固定・保証境界を明記 | 将来「効くようになった」を版更新で検知できる |
| D4 | attestation は権威ある場所のみ読む + 報告バグを 1 行是正 | `calibrator_degenerate: true` は base の残留値。真値は構造的に False |
| D5 | 束の保存形式は変えず、1 fold 実測の中断点を置く | 保存量を変えると activation の消費側と食い違う |
| D6 | 窓・基準は 078/074 の凍結値を流用 | 窓の選び直しは結果に合わせた選択(FR-007 違反) |

## Complexity Tracking

なし(Constitution Check 全 PASS・migration なし・スキーマ/API 不変・変更は training パッケージ内に集中)。
