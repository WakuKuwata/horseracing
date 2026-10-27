"""Direct timing metadata audit from stored DB rows; no primary imports."""
import hashlib
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'artifacts/158-prerace-roi-comparison'
OUT = Path(__file__).resolve().parent
j = pd.read_parquet(BASE / 'prepared/judgments.parquet').drop_duplicates('race_id')
runs = pd.read_parquet(BASE / 'snapshot/runs.parquet').set_index('prediction_run_id')
predictions = pd.read_parquet(BASE / 'snapshot/predictions.parquet')
features = pd.read_parquet(BASE / 'snapshot/features.parquet')
quote_age, old_age, post_gap = [], [], []
for row in j.itertuples():
    runid = row.old_prediction_run_id
    run = runs.loc[runid]
    stamps = [run[k] for k in ['computed_at', 'created_at', 'updated_at']]
    for frame in [predictions[predictions.prediction_run_id.eq(runid)], features[features.prediction_run_id.eq(runid)]]:
        for col in ['created_at', 'updated_at']:
            stamps.extend(frame[col].tolist())
    available = max(pd.to_datetime(v, utc=True) for v in stamps)
    decision = pd.to_datetime(row.decision_at, utc=True)
    assert available == row.old_available_at and available <= decision < row.post_time
    q_age = (decision-row.quote_observed_at).total_seconds()
    o_age = (decision-available).total_seconds()
    assert abs(q_age-row.quote_age_seconds) < 1e-9 and abs(o_age-row.old_age_seconds) < 1e-9
    quote_age.append(q_age); old_age.append(o_age); post_gap.append((row.post_time-decision).total_seconds())
def stats(values):
    x = pd.Series(values)
    return dict(min=float(x.min()), median=float(x.median()), max=float(x.max()))
result = dict(races=len(j), quote_age_seconds=stats(quote_age), old_available_age_seconds=stats(old_age), decision_before_post_seconds=stats(post_gap),
    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(OUT/'independent_times.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
