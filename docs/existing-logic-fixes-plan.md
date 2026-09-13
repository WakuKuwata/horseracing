# 既存ロジックの修正計画

2026-09-13。ユーザーの「あなたの推奨で修正を進めて」に基づく、既存不具合の修正。
作業場所は `codex/existing-logic-fixes` の隔離worktree。元mainで進行する135の固定source・結果・入力は変更しない。

## 今回の実装境界

1. 探索結果／採用不可結果を昇格根拠にできないようにする。候補と基準の実学習契約、登録した本体を昇格証拠へ結び付ける。旧凍結report・数値閾値・理由付きoverrideは維持する。
2. 出走馬集合が変わった予測runをAPIから提供しない。取消だけでなく同じ頭数の馬ID入替も検査する。既存のtyped empty／explicit model 404を使い、古い別runへ戻らない。自動backfillも同じ失効を認識し、forceなしの再計算で復旧する。
3. 校正必須設定でmanifestが無いとき、予測・推奨・期間更新の起動前に明示エラーにする。
4. HPOのtraining TEを最終学習と同じOOF処理にする。validation用encoderはそのCV trainだけでfitする。
5. 新たなDB読込で結果完全性を数えるとき、race/horse両IDで現在STARTEDの結果だけに結合する。旧pickleのEvalRaceレイアウトは変えない。
6. 自動登録で既存ACTIVEがある場合は候補として保存し、通常の明示昇格手順へ渡す。

現行6モデルの特徴量値を同じfeature versionのまま変更しない。脚質定義の統一、馬主の当時値、体重条件別校正は再学習・OOS比較が必要なモデル変更として扱う。Unknownのみの修正は132で最新年が悪化しているので即適用しない。legacyの校正材料選択問題は別モデルの校正を流用して覆い隠さず、保存校正の来歴と条件が揃うことを配布条件にする。

## 独立意見と選択

- 昇格: walk-forwardのboosterと最終全期間fitの本体SHA一致を要求すると正しい手順まで拒否する。実学習契約の一致を検査し、登録時の本体SHAと配布時SHAを別途照合する案を採用した。自動の旧ACTIVE降格より、候補保存して明示promoteへ誘導する案を採用。
- 取消: SQLで「適合する過去run」を探索すると古いrunが復活する。規約で選んだ1runだけを現在出走集合と検査し、合わなければ失効する案を採用。topkの定数倍補正は不可。
- 結果完全性: EvalRaceのレイアウト追加より、新しいDB loaderの複合ID JOINでstarted結果件数を正しくする案を採用。元111素材の正常出力を維持できる。旧pure constructor/pickleは信頼された旧入力として扱う。
- HPO: validationへの直接TE漏洩ではなく、train内での自ラベル利用と最終OOF学習の手順差を直す。共通OOF実装を再利用する。

## 検証

Python3.12、各packageの独立 `.venv` を `uv sync --frozen --offline` で構築。元checkoutのeditable依存を使わない。
対象unit、実SQLの境界確認、取消→失効→再推論回復、探索→拒否と正規確認→成功、HPO/final TE parityを検査する。DB integrationは一時testcontainerに限定し、稼働DBへ書かない。
ML検証は小さい合成模型のみ、最大1thread。モデル追加、最適化閾値変更、新しい入力取得、旧実験の結果書換えは含まない。

## 憲法確認

12桁IDとsource ID契約を維持。取消後の確率を誤提供しない。結果・TEのtrain境界を強化する。モデルの採用にOOS証拠を要求し、過去研究を確認済みへ読み替えない。既存APIの返却契約とDB schemaを維持し、昇格理由と本体の来歴を保存する。

## 完了記録

上記の実装を完了。独立レビューで見つかった同ID ACTIVEの上書き、保存と昇格の競合、未対応派生builderの契約混同、別regimeの古いrunによる再計算skipも修正範囲へ含めた。
対象テスト・実fitからの通常昇格・取消後の実推論による復旧を確認した。詳細な結果と、再学習による精度検証が残る項目は [修正結果](existing-logic-fixes-result.md) を参照。
