"""M6: after the fixes, re-predict whole race days through the production functions (no DB write) and
compare WIN with the persisted _raw_win of the existing runs (same regime), plus consistency + guard."""
import os, sys, json, numpy as np, pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.model_loader import load_serving_model
from horseracing_serving.mixture_serving import mixture_context, predict_mixture_race
from horseracing_serving.predictor import race_weight_availability
from horseracing_eval.consistency import check_consistency
e=create_engine(os.environ['DATABASE_URL'])
out=[]
with Session(e) as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    model=load_serving_model(s,'mix-129-nj6')
    for day in sys.argv[1:]:
        day=pd.Timestamp(day).date()
        rids=[r[0] for r in s.execute(text("select race_id from races where race_date=:d order by race_id"),dict(d=day)).all()]
        fr=build_feature_matrix(s, end_date=day, wanted=frozenset(model.feature_cols), target_race_ids=frozenset(rids))
        present=set(fr.race_id.unique())
        fr, hist=mixture_context(s, fr, rids, day)
        worst=0.0; n=0; regs={}
        for rid in rids:
            if rid not in present: continue
            avail=race_weight_availability(fr, rid, model=model)
            reg='full_info' if avail.n_weighed==avail.n_started else 'serving'
            preds,_,_,audit=predict_mixture_race(model, rid, fr, hist, stage_discount=None)
            check_consistency(preds)
            pers=s.execute(text("""select rp.horse_id, (fs.features->>'_raw_win')::float raw from prediction_runs pr
              join race_predictions rp on rp.prediction_run_id=pr.prediction_run_id join feature_snapshots fs on fs.prediction_run_id=pr.prediction_run_id and fs.horse_id=rp.horse_id
              where pr.model_version='mix-129-nj6' and pr.race_id=:r and pr.logic_version like :w
              and pr.computed_at=(select max(computed_at) from prediction_runs where race_id=:r and model_version='mix-129-nj6' and logic_version like :w)"""),
              dict(r=rid,w=f'%wregime={reg}%')).all()
            if not pers: regs[reg]=regs.get(reg,0); continue
            d=max(abs(preds[h].win-raw) for h,raw in pers); worst=max(worst,d); n+=1; regs[reg]=regs.get(reg,0)+1
        out.append(dict(day=str(day), n_races_compared=n, regimes=regs, max_abs_diff_vs_persisted_raw_win=worst, n_history_rows=int(len(hist))))
    s.rollback()
print(json.dumps(out,indent=1))
