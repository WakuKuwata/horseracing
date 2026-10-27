# Implementation Plan: 精度改善の第一バッチ

**Branch**: main（並行作業維持） | **Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

## Summary

上限2026-09-06。SHA固定111 snapshot（実末日8/23）、130 annual cache、126 cacheを使用する。脚質Unknownの研究用8列overlayを作成し、138列の2024/2026 paired screenを実行。追加学習不要のtop2/top3三方式比較とcolsample4混合案を実測する。

## Technical Context

**Language/Version**: Python3.12、training/.venv。
**Primary Dependencies**: 既存numpy/pandas/LightGBM/scipy/pytestとeval/training/features。
**Storage**: artifacts/132-accuracy-first-batchへ追記。旧pickle/cacheはSHA検査後に読み取り。
**Testing**: cutoff拒否、全既知parity、未知5実走窓、取消/同日/未来不変、非8列厳密一致、mixed6既存NLL再現、年別校正境界、score/CI手計算、CLI smoke。
**Target Platform**: macOS 24GiB。
**Project Type**: 研究CLI。
**Performance Goals**: topk/colsample追加booster fit0。styleは2候補outer（baseline再利用が厳密に可能な場合）。再利用不能なら同条件baselineを追加。各outerは最終1 fitと8分割のうち過去データを持つ7 inner fit、合計8 booster fit。最初の学習実費で見積を更新。
**Constraints**: 9/6超データ禁止、最大2worker・各1thread。重いscorerと学習はRSSを見て時間差実行。既存source不変更。失敗cacheの黙示上書きなし。
**Scale/Scope**: 最大23,030評価レースの保存再採点、約96万行の特徴監査。seed42研究screenで将来優位や再学習分散を確定しない。

## Constitution Check

- [x] I: 12桁race ID、2007以降、同一source ID、3確率。
- [x] II: cutoff/SHAを入口で検査、target-day除外、OOF、年別過去係数、stage fit year<Y。
- [x] III: 同集合paired評価・baseline・ECE、採用なし、開発比較の範囲明示。
- [x] IV: started-all、取消除外、有限/順序/合計、Unknown≠0。
- [x] V: 入力/semantics/source SHA、結果/時間、全比較追記、旧成果物不変更。
- [x] VI: 外部API/DB/UIなし。CLIと研究artifact契約を先に固定。
- [x] Independent: challenge_features/model/historyの3意見をresearch.mdへ記録。

## Project Structure

scripts/accuracy_batch_common.py（cutoff/SHA/入力、mixed6、共通採点）
scripts/accuracy_topk_recheck.py（A/B/C、両regime、年別校正）
scripts/accuracy_colsample_recheck.py（126の4arm、full-only）
scripts/pace_style_unknown_features.py（既存関数共用overlay）
scripts/accuracy_style_recheck.py（素材監査・学習・比較）
scripts/tests/test_*.py（新規module限定テスト）
specs/132-accuracy-first-batch（設計・結果）

## Design

1. snapshot metadata末日8/23とSHAを検査してからpickleを読む。日付不明/範囲外raceと参照IDを拒否。DB/latest parquetへのfallbackなし。
2. mixed6は130年別raw winへ当該年joint/gap係数を適用し全head平均。2026係数の遡及禁止。旧NLL2.039080450(full)・2.040478743(preweight)照合。
3. Topk A=head平均、B=mean win Harville、C=過去年の同regime混合winからstage割引fit。2020warmup、2021〜2026一次比較。2020fallback込みは補助。win固定、complete_results started-allでtop2/top3、eligibleでwinner採点。λ不足fallbackはB。
4. Colsample126 seed42の既存校正後3head。4案はexperiment.json固定。移送係数は125 seed42年別係数であり126用推定ではない。全head同じ重み、full-only。
5. StyleはUnknownを保持し5実走窓を維持。8列以外・label・順序・dtype完全一致。全既知parity。脚質定義の全面統一は混ぜない。
6. Style screen=2024/2026、raw138/seed42/900/8OOF/TE10/mask.5。修正matrixをbaseと全OOFへ渡す。full/preweight同fit。cache keyへsemantics/matrix SHA。baseline130再利用は同一train/recipe/入力の厳密検査が前提。
7. CI=開催日paired再標本化、馬単位損失はloss sum/count比。alpha=.0125,b=4000,seed=20260907。保存モデル/固定係数に条件付けた標本CI。選択/再学習/係数再推定を全包含と呼ばない。
8. 全案・年・条件を報告。新しい本番採用なし。既存serving並行差分不変更。

## Independent decisions

全features変更より研究overlayを選択（旧hash/学習済み入力維持）。style138単独から変更効果を分離。126full cacheからpreweightを捏造しない。topk既存evaluate_stage_discountは再fit/finished-only/fallbackが違うため純関数のみ共用。2021+を主解析にし2020用の存在しないpreweight校正材料を補わない。
