# 129 Quickstart(実行済みコマンド)

リポジトリルートで実行する。`TR=training/.venv/bin/python`、`SV=serving/.venv/bin/python`、`W=artifacts/129-candidate-mixture-serving`。DB は読み取り専用で使う(`DATABASE_URL` を export)。

```bash
# 1. テスト(scripts 側と serving 側は別プロセス)
$TR -m pytest scripts/tests/test_candidate_mixture_build.py scripts/tests/test_candidate_mixture_validate.py \
   scripts/tests/test_candidate_mixture_bundle.py scripts/tests/test_mixture_confirmation.py -q
$SV -m pytest serving/tests/unit/test_mixture_correction.py serving/tests/unit/test_mixture_model.py serving/tests/unit/test_mixture_shadow.py -q

# 2. 6 モデル(prepare→smoke→train・約 1.5 時間・2 worker)
$TR scripts/candidate_mixture_build.py prepare
$TR scripts/candidate_mixture_build.py smoke --workers 2
$TR scripts/candidate_mixture_build.py train --workers 2

# 3. bundle 組み立て(verify-members 再検証込み)
PYTHONPATH=serving/src $TR scripts/candidate_mixture_bundle.py assemble

# 4. anchor 凍結(active モデル + 8/24 以前の予測で fit した stage 割引)
$SV -m horseracing_serving.mixture_shadow freeze-anchor --model-version lgbm-094-cap900 --asof 2026-08-24 \
   --output $W/anchor/anchor.json --stage-discount-output $W/anchor/stage-discount.json

# 5. rehearsal(締切後の開催日・結果既知=採用証拠にならない)
$SV -m horseracing_serving.mixture_shadow capture --date 2026-08-29 --classification rehearsal --regime preweight \
   --bundle $W/bundle.json --bundle-sha256 <bundle sha> --anchor $W/anchor/anchor.json --out-dir $W/shadow

# 6. 検出力の development evidence と互換性 evidence
$SV -m horseracing_serving.mixture_shadow develop --record-dir $W/shadow/rehearsal --bundle-sha256 <sha> \
   --anchor-sha256 <sha> --regime preweight --output specs/129-candidate-mixture-serving/evidence/power-development.json
PYTHONPATH=serving/src $TR scripts/candidate_mixture_bundle.py compatibility --bundle $W/bundle.json --anchor $W/anchor/anchor.json \
   --record-dir $W/shadow/rehearsal --regime preweight --output specs/129-candidate-mixture-serving/evidence/serving-compatibility.json

# 7. 確認の下書き・検出力・preflight(予約はしない)
PYTHONPATH=serving/src $TR scripts/mixture_confirmation.py power --development specs/129-candidate-mixture-serving/evidence/power-development.json --output specs/129-candidate-mixture-serving/evidence/power-plan.json
PYTHONPATH=serving/src $TR scripts/mixture_confirmation.py draft --bundle $W/bundle.json --anchor $W/anchor/anchor.json --used-through 2026-09-06 \
   --start 2026-09-12 --end 2026-12-28 --min-days 24 --regime preweight --output $W/confirmation/manifest-window-draft.json
PYTHONPATH=serving/src $TR scripts/mixture_confirmation.py preflight --manifest $W/confirmation/manifest-window-draft.json --output <new path>

# 8. 将来レース(出馬表取込後・体重公表前・発走前)
$SV -m horseracing_serving.mixture_shadow capture --date 2026-09-12 --classification prospective --regime preweight \
   --bundle $W/bundle.json --bundle-sha256 <sha> --anchor $W/anchor/anchor.json --out-dir $W/shadow
```

出力はすべて排他作成(既存パスは拒否)。preflight が READY になるのは power/noise/compatibility の独立レビューが揃ったときだけで、その後に `reserve` が共通台帳の年 8 枠を 1 つ消費する。
