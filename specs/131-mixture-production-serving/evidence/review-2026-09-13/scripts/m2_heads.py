"""M2: top2/top3 derivation comparison on the 130 OOS cache (2020-2026, both regimes).
(a) research: mean of member Harville(λ=1) heads  (b) Harville(mean win) λ=1
(c) Harville(mean win) λ walk-forward fit on the mixture's own prior-year OOS mean-win samples
(d) Harville(mean win) fixed production λ (2026-09-12 values)  (e) oracle: λ fit on the same year (reference only)."""
import os, sys, json, math, pickle, time
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
sys.path.insert(0,'/Users/kuwatawaku/workspace/horseracing/scripts')
import mixture_preweight_walkforward as w
from horseracing_eval.dataset import population_masks
from horseracing_eval.baselines import harville_topk
from horseracing_eval.stage_discount import StageDiscount, discounted_topk, fit_stage_discount, TopkSample
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
mc = w.mc
OUT='/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/f61fd026-62ef-4c84-a63c-2295fc83511d/scratchpad/m/m2_heads.json'
PROD_L = (0.86534, 0.71235)
frozen = w.verify()
matrix, races, folds = w.inputs()
frame = matrix.frame; KEYS = mc.KEYS
years = pd.to_datetime(frame.race_date).dt.year
target = frame.loc[years.isin(w.YEARS), KEYS + ['days_since_last', 'sex']]
inputs_frame = mc.build_correction_inputs(target, frame[KEYS])
per_race = {rid: g.set_index('horse_id', drop=False) for rid, g in inputs_frame.groupby('race_id', sort=False)}
del target, inputs_frame
e = create_engine(os.environ['DATABASE_URL'])
plac = {}
with e.connect() as c:
    for rid, hid, fo in c.execute(text("""select rr.race_id, rr.horse_id, rr.finish_order from race_results rr join races r using(race_id)
        where r.race_date between '2020-01-01' and '2026-12-31' and rr.result_status='finished' and rr.finish_order<=3""")).all():
        plac.setdefault(rid, {}).setdefault(int(fo), []).append(hid)
def placings(rid):
    p = plac.get(rid, {})
    return tuple(p[k][0] if len(p.get(k, [])) == 1 else None for k in (1, 2, 3))
caches = {(m['id'], y): pickle.load(w.cache_path(m['id'], y).open('rb')) for m in w.MEMBERS for y in w.YEARS}

def ll(p, y):
    p = np.clip(p, 1e-9, 1 - 1e-9); return -(y * np.log(p) + (1 - y) * np.log(1 - p))
def ece(p, y, bins=10):
    order = np.argsort(p, kind='stable'); p, y = p[order], y[order]
    parts = np.array_split(np.arange(len(p)), bins)
    return float(sum(len(i) * abs(p[i].mean() - y[i].mean()) for i in parts) / len(p))

result = {'variants': ['a_mean_heads', 'b_harville_l1', 'c_harville_wf_lambda', 'd_harville_prod_lambda', 'e_harville_oracle_lambda'],
          'prod_lambda': PROD_L, 'regimes': {}}
for reg in ('preweight', 'full'):
    t0 = time.time()
    # pass 1: mixture mean win + research heads per race
    races_out = {}   # year -> list of dict(rid, day, ids, win, heads_a, placings)
    for year in w.YEARS:
        for er in folds[year].valid:
            ctx = er.context; pop = population_masks(er)
            if not pop.eligible: continue
            ids = [h.horse_id for h in ctx.started_horses]
            corr = per_race[ctx.race_id].loc[ids].reset_index(drop=True)
            members = []
            for m in w.MEMBERS:
                heads = caches[(m['id'], year)]['predictions'][reg][ctx.race_id]
                base = np.array([heads[h][0] for h in ids])
                terms = mc.JOINT_TERMS if m['branch'] == 'pruning' else ['gap_log']
                members.append(mc.correct_member_predictions(ids, base, corr, terms, frozen['coefficients'][m['id']][str(year)]))
            mix = mc.average_member_predictions(members)
            races_out.setdefault(year, []).append(dict(rid=ctx.race_id, day=str(ctx.race_date), ids=ids,
                win=np.array([mix[h].win for h in ids]), t2a=np.array([mix[h].top2 for h in ids]), t3a=np.array([mix[h].top3 for h in ids]),
                plac=placings(ctx.race_id)))
    # λ fits: walk-forward (years < Y) and oracle (year Y)
    def samples(yrs):
        out = []
        for y in yrs:
            for r in races_out.get(y, []):
                i1, i2, i3 = r['plac']
                if i1 is None or i1 not in r['ids']: continue
                sid = sorted(r['ids']); pos = {h: k for k, h in enumerate(sid)}; wd = dict(zip(r['ids'], r['win']))
                out.append(TopkSample(win=tuple(wd[h] for h in sid), i1=pos[i1], i2=pos.get(i2) if i2 in pos else None, i3=pos.get(i3) if i3 in pos else None))
        return out
    lam_wf, lam_or = {}, {}
    for y in w.YEARS:
        sd = fit_stage_discount(samples([z for z in w.YEARS if z < y])) if y > w.YEARS[0] else StageDiscount(fallback=True)
        lam_wf[y] = sd
        lam_or[y] = fit_stage_discount(samples([y]))
    # pass 2: metrics
    per_year = {}; pooled = {v: {'ll2': [], 'll3': [], 'p2': [], 'y2': [], 'p3': [], 'y3': [], 'byday2': {}, 'byday3': {}} for v in result['variants']}
    for y in w.YEARS:
        acc = {v: {'ll2': [], 'll3': [], 'p2': [], 'y2': [], 'p3': [], 'y3': []} for v in result['variants']}
        for r in races_out.get(y, []):
            i1, i2, i3 = r['plac']; ids = r['ids']; win = r['win'].tolist()
            var = {'a_mean_heads': (r['t2a'], r['t3a'])}
            t2, t3 = harville_topk(win); var['b_harville_l1'] = (np.array(t2), np.array(t3))
            for key, sd in (('c_harville_wf_lambda', lam_wf[y]), ('d_harville_prod_lambda', StageDiscount(lambda2=PROD_L[0], lambda3=PROD_L[1], n_races_l2=1, n_races_l3=1)), ('e_harville_oracle_lambda', lam_or[y])):
                if sd.is_identity: var[key] = var['b_harville_l1']
                else:
                    t2, t3 = discounted_topk(win, sd); var[key] = (np.array(t2), np.array(t3))
            ok2 = i1 is not None and i2 is not None and i1 in ids and i2 in ids
            ok3 = ok2 and i3 is not None and i3 in ids
            y2 = np.array([h in (i1, i2) for h in ids], float); y3 = np.array([h in (i1, i2, i3) for h in ids], float)
            race_ll = {}
            for v, (p2, p3) in var.items():
                if ok2:
                    l2 = ll(p2, y2); acc[v]['ll2'].append(l2.mean()); acc[v]['p2'].append(p2); acc[v]['y2'].append(y2); race_ll.setdefault(v, {})['2'] = l2.mean()
                if ok3:
                    l3 = ll(p3, y3); acc[v]['ll3'].append(l3.mean()); acc[v]['p3'].append(p3); acc[v]['y3'].append(y3); race_ll[v]['3'] = l3.mean()
            for v in result['variants']:
                if v == 'a_mean_heads' or v not in race_ll: continue
                if '2' in race_ll[v]: pooled[v]['byday2'].setdefault(r['day'], []).append(race_ll[v]['2'] - race_ll['a_mean_heads']['2'])
                if '3' in race_ll[v]: pooled[v]['byday3'].setdefault(r['day'], []).append(race_ll[v]['3'] - race_ll['a_mean_heads']['3'])
        per_year[y] = {}
        for v in result['variants']:
            a = acc[v]
            if not a['ll2']: continue
            p2 = np.concatenate(a['p2']); y2 = np.concatenate(a['y2']); p3 = np.concatenate(a['p3']); y3 = np.concatenate(a['y3'])
            per_year[y][v] = dict(n_races2=len(a['ll2']), n_races3=len(a['ll3']), top2_logloss=float(np.mean(a['ll2'])), top3_logloss=float(np.mean(a['ll3'])),
                                  top2_ece=ece(p2, y2), top3_ece=ece(p3, y3), top2_brier=float(np.mean((p2 - y2) ** 2)), top3_brier=float(np.mean((p3 - y3) ** 2)),
                                  top2_mean_pred=float(p2.mean()), top2_rate=float(y2.mean()), top3_mean_pred=float(p3.mean()), top3_rate=float(y3.mean()))
            for k in ('ll2', 'll3', 'p2', 'y2', 'p3', 'y3'): pooled[v][k].extend(a[k])
    pooled_out = {}
    for v in result['variants']:
        a = pooled[v]; p2 = np.concatenate(a['p2']); y2 = np.concatenate(a['y2']); p3 = np.concatenate(a['p3']); y3 = np.concatenate(a['y3'])
        d = dict(n_races2=len(a['ll2']), n_races3=len(a['ll3']), top2_logloss=float(np.mean(a['ll2'])), top3_logloss=float(np.mean(a['ll3'])),
                 top2_ece=ece(p2, y2), top3_ece=ece(p3, y3))
        if v != 'a_mean_heads':
            for k, bd in (('top2_diff_vs_a', a['byday2']), ('top3_diff_vs_a', a['byday3'])):
                ci = race_day_cluster_bootstrap_ci_v1(bd, b=2000, seed=20260913, alpha=0.05)
                d[k] = dict(point=ci.point, ci=[ci.ci_low, ci.ci_high], n_days=len(bd))
        pooled_out[v] = d
    result['regimes'][reg] = dict(per_year={str(y): v for y, v in per_year.items()}, pooled=pooled_out,
        lambda_walkforward={str(y): (None if sd.is_identity else [sd.lambda2, sd.lambda3, sd.n_races_l2]) for y, sd in lam_wf.items()},
        lambda_oracle={str(y): (None if sd.is_identity else [sd.lambda2, sd.lambda3, sd.n_races_l2]) for y, sd in lam_or.items()},
        elapsed_seconds=time.time() - t0)
    json.dump(result, open(OUT, 'w'), indent=1)
    print(reg, 'done', time.time() - t0, flush=True)
print('ALL DONE')
