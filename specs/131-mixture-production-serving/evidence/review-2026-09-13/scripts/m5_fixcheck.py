"""M5: after the eps fix, re-predict the sub-clip races through the production function (no DB write)
and compare with the persisted _raw_win (mixture mean) and the persisted win_prob (pre-fix clipped)."""
import os, json, numpy as np, pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.model_loader import load_serving_model
from horseracing_serving.mixture_serving import mixture_context, predict_mixture_race
from horseracing_eval.consistency import check_consistency
e=create_engine(os.environ['DATABASE_URL'])
out=[]
with Session(e) as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    model=load_serving_model(s,'mix-129-nj6')
    races=s.execute(text("""select distinct pr.race_id, r.race_date from prediction_runs pr join races r using(race_id)
      join race_predictions rp on rp.prediction_run_id=pr.prediction_run_id join feature_snapshots fs on fs.prediction_run_id=pr.prediction_run_id and fs.horse_id=rp.horse_id
      where pr.model_version='mix-129-nj6' and (fs.features->>'_raw_win')::float < 1e-6 order by 2 limit 3""")).all()
    for rid, day in races:
        fr=build_feature_matrix(s, end_date=day, wanted=frozenset(model.feature_cols), target_race_ids=frozenset([rid]))
        fr, hist=mixture_context(s, fr, [rid], day)
        preds, snaps, expl, audit=predict_mixture_race(model, rid, fr, hist, stage_discount=None)
        check_consistency(preds)
        pers=s.execute(text("""select rp.horse_id, (fs.features->>'_raw_win')::float raw, rp.win_prob::float win from prediction_runs pr
          join race_predictions rp on rp.prediction_run_id=pr.prediction_run_id join feature_snapshots fs on fs.prediction_run_id=pr.prediction_run_id and fs.horse_id=rp.horse_id
          where pr.model_version='mix-129-nj6' and pr.race_id=:r"""),dict(r=rid)).all()
        d_raw=max(abs(preds[h].win-raw) for h,raw,_ in pers); d_old=max(abs(preds[h].win-w) for h,_,w in pers)
        out.append(dict(race_id=rid, day=str(day), n=len(pers), min_win=min(p.win for p in preds.values()),
                        max_diff_vs_persisted_raw_win=d_raw, max_diff_vs_persisted_prefix_win_prob=d_old,
                        sum_win=sum(p.win for p in preds.values()), sum_top2=sum(p.top2 for p in preds.values()), sum_top3=sum(p.top3 for p in preds.values())))
    s.rollback()
print(json.dumps(out,indent=1))
