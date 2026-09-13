# Implementation Plan: 追加混合の再現性と直近確認

**Branch**: main（切替なし） | **Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

## Summary

Step1保存予測で本番校正計算・材料変換を制御比較→Step2固定6 outerを再学習→Step3固定した8/29〜9/6を確認。実レース上限9/6、Step1/2はSHA固定111/130/132の8/23末まで。Step3の結果・特徴値は候補固定後に取得。事前metadataだけの調査は許可する。

## Technical Context

**Language/Version**: Python3.12、training/.venv。
**Primary Dependencies**: 既存numpy/pandas/LightGBM/scipy/pytest、features/training/eval/serving純関数。
**Storage**: artifacts/135-mixture-reproducibility/{calibration,repro,recent}追記。Step3 PostgreSQLは日付・列を制限したread-only SELECTのみ。
**Testing**: cutoff-before-read、時点、同λ演算等価性、OOF、seed42旧126parity、保存本体roundtrip、freeze/hash、母集団/指標/CI独立再計算。
**Target Platform**: macOS 24GiB、max2 worker each1thread。
**Project Type**: 研究CLI。本番・DB更新なし。
**Performance Goals**: Step1 booster0、Step2 6outer×8booster=48、Step3候補1outer×8=8。各step構造smokeは5本・2OOF等の小規模を別計上。既存基準の再学習を避ける。
**Scale/Scope**: Step1最大23,030レース×2条件、Step2 5,752レース×3seed×2条件、Step3は8/29〜9/6のみ。

## Constitution Check

- [x] I: 12桁race ID、2007以降、同source ID、3確率。
- [x] II: 実日付9/6上限・SHA・年別過去学習・同regime過去λ。候補固定前は直近ラベル未読。
- [x] III: 同条件baseline・ECE・時系列評価。採用不可、直近を少標本診断と明示。
- [x] IV: started-all、complete_resultsのみ採点、取消除外、Unknown維持、sum1/2/3 atol1e-8。
- [x] V: 各入力/source/runtime/recipe/係数/予測/本体SHA、全seed・期間・実費・研究判断を追記。
- [x] VI: UI/API新設なし。研究CLI契約を先に固定。DB read-onlyのみ。
- [x] 独立意見: model/features/historyの設計指摘と採用根拠をresearch.mdへ記録。

## Project Structure

scripts/calibration_serving_recheck135.py + scripts/tests/test_calibration_serving_recheck135.py
scripts/mixture_reproducibility.py + scripts/tests/test_mixture_reproducibility.py
scripts/mixture_recent_check_135.py + scripts/tests/test_mixture_recent_check_135.py
specs/135-mixture-reproducibility/{spec,plan,research,data-model,quickstart,tasks,result-review}.md

## Design

1. Step1:132 Cの同win/同λを現行131純演算で再計算し等価性を検証。現行sample変換・fitを同じ前年末までの132 OOS素材へ適用し、材料変換/結果不完備の差を制御比較。現在DBのlatest予測や9/6超を呼ばない。実運用daily/latest来歴とλが未保存なら、その直接比較は再現不能と報告する。
2. Step2:colsample.7/125cols/900/8OOF/mask.5/TE10。seed42/43/44×2024/2026、同fitでfull/preweight。候補補正は125同seed同年joint係数を移送し候補自身centered_logpを再計算。6/7 fixed M+1/7 C。seed42 fullの旧126raw予測/132補正混合を照合。
3. Step2 topk:混合winから131等価Harville/Benter。両armで132年度/同regimeのbaseline λを固定する。候補専用の全過年OOSが無いのでλをfitし直さない。旧132head平均は再現確認用に保存し、主head指標と分離。
4. 集約:seed別・年別・regime別を全報告。seed平均は同一レースのloss差平均であり、確率を平均した新ensembleではない。distinct race/day数を3倍にしない。preweight主、full副。CIは開催日の損失合計/母数のpaired ratio bootstrap、98.75%/4000/seed20260907。保存seed条件付きCIであり再学習分散の確定推定ではない。
5. Step3:130年次boosterが未保存のため、129 train_through8/23の6本を基準にし、候補seed42を同じ末日まで1outer fit。比率1/7・係数2026・λ<=2025末を両armで固定し、現行latestλの再現とは呼ばない。Step2の成績でseedや比率を選び直さない。
6. Step3入力:当時feature_snapshotsのmetadataをrace_date<=9/6かつcomputed_at<=9/6日末JSTで確認。値はStep2完了後に取得。候補と基準の同じ入力・取消集合を使い、旧特徴版の不足を未来情報で埋めない。全historyの現在mutable horsesを読む既存load_framesはそのまま使わない。必要な時点入力が無い場合、取得可能範囲と非再現理由を記録し、実運用精度を捏造しない。
7. 直近は少標本診断。既存参照状況・取得時点・入力版・実範囲を記録する。比較を一度採点して凍結し、今回の結果による再調整を行わない。
8. source hashは実依存だけを固定。既存source変更なし。全新研究結果はcan_adopt=false/eligible_for_verdict=false。

## Complexity Tracking

追加サービスなし。年次基準6本を再fitする48boosterの代わりに、Step3を明示的に8/23学習比較へ分けて候補8boosterだけを追加する。
