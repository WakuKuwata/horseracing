# Validation guide

runtime=training/.venv/bin/python。新規3moduleのsynthetic testsを先に通す。
Step1 calibration_serving_recheck135.py→source/入力SHAと完了結果。
Step2 mixture_reproducibility.pyでfreeze→smoke→固定6fit→summarize。親がStep1完了を確認して学習GO。max2worker each1thread。
Step3 mixture_recent_check_135.pyで時点metadata→候補/手順固定→Step2完了後に入力/結果取得→129基準と同じ8/23学習候補で直近比較。
旧入力・結果は上書きしない。欠損cache/時点不明でlatest DBや新fitへ黙ってfallbackしない。
最後に保存予測から独立採点しresult-review.mdへ全seed/年/条件/直近/実費/限界を記載。
