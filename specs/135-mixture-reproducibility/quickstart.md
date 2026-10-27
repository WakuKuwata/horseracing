# Validation guide

2026-09-20の最新の独立再現は専用実行 `54586a4bf86c`。結果と来歴は
[専用実行の報告](result-review-owned-54586a4bf86c.md)、監査記録は
[専用evidence](evidence/owned-54586a4bf86c/)を参照する。
`evidence/completion.json` と下記の汎用CLI手順は9/13の元実行に対応する。
共有ソースには後続修正があるため、最新実行の構成確認には保存した隔離環境を使う。

リポジトリ直下で、追加学習を伴わないpreflightを実行する:

```bash
training/.venv/bin/python artifacts/135-mixture-reproducibility/execution-environment-54586a4bf86c/scripts/run_recent_owned_v2_135_54586a4bf86c.py preflight
```

既存の完了記録・凍結証拠は保存したままにする。以降は元実行の設計手順。

runtime=training/.venv/bin/python。新規3moduleのsynthetic testsを先に通す。
Step1 calibration_serving_recheck135.py→source/入力SHAと完了結果。
Step2 mixture_reproducibility.pyでfreeze→smoke→固定6fit→summarize。親がStep1完了を確認して学習GO。max2worker each1thread。
Step3 mixture_recent_check_135.pyで時点metadata→候補/手順固定→Step2完了後に入力/結果取得→129基準と同じ8/23学習候補で直近比較。
旧入力・結果は上書きしない。欠損cache/時点不明でlatest DBや新fitへ黙ってfallbackしない。
最後に保存予測から独立採点しresult-review.mdへ全seed/年/条件/直近/実費/限界を記載。
