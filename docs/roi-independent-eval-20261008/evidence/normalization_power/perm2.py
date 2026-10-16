"""Count-matched permutation null: top-n by EV_ens / sum_p_perm (n = |N1|) vs top-n by EV_ens/sum_p (N1 top-n)."""
import json, sys, numpy as np, pandas as pd
OUT = sys.argv[1]
d = pd.read_parquet(f"{OUT}/pop_applied.parquet")
ev = d.EV_ens.to_numpy(); sp = d.sum_p.to_numpy(); pay = d.payout.to_numpy(); yr = d.year.to_numpy(); post = yr >= 2019
rid, race_idx = pd.factorize(d.race_id)
race = d.drop_duplicates("race_id").set_index("race_id").loc[race_idx]
sp_race = race.sum_p.to_numpy(); yr_race = race.year.to_numpy()
n1 = ev > 1.2 * sp
n_all = int(n1.sum()); n_post = int((n1 & post).sum())
def topn(score, mask, n):
    idx = np.where(mask)[0]
    order = idx[np.argsort(-score[idx], kind="stable")][:n]
    m = np.zeros(len(score), bool); m[order] = True
    return m
def roi(m): return float(pay[m].sum() / (100 * m.sum()))
# observed: top-n by EV_norm within ALL and within POST (count matched to N1 in each window)
obs = {"ALL_topn_EVnorm": roi(topn(ev / sp, np.ones(len(ev), bool), n_all)),
       "POST_topn_EVnorm": roi(topn(ev / sp, post, n_post)),
       "ALL_topn_EVens": roi(topn(ev, np.ones(len(ev), bool), n_all)),
       "POST_topn_EVens": roi(topn(ev, post, n_post))}
rng = np.random.default_rng(23); R = 500
pa, pp = [], []
for r in range(R):
    spp = sp_race.copy()
    for y in np.unique(yr_race):
        ix = np.where(yr_race == y)[0]; spp[ix] = sp_race[rng.permutation(ix)]
    sc = ev / spp[rid]
    pa.append(roi(topn(sc, np.ones(len(ev), bool), n_all))); pp.append(roi(topn(sc, post, n_post)))
pa, pp = np.array(pa), np.array(pp)
res = {"n_all": n_all, "n_post": n_post, "obs": obs,
       "perm_ALL": {"mean": float(pa.mean()), "sd": float(pa.std(ddof=1)), "q025": float(np.quantile(pa, .025)), "q975": float(np.quantile(pa, .975)),
                    "p_ge_obs": float((1 + (pa >= obs["ALL_topn_EVnorm"]).sum()) / (R + 1))},
       "perm_POST": {"mean": float(pp.mean()), "sd": float(pp.std(ddof=1)), "q025": float(np.quantile(pp, .025)), "q975": float(np.quantile(pp, .975)),
                     "p_ge_obs": float((1 + (pp >= obs["POST_topn_EVnorm"]).sum()) / (R + 1))}}
json.dump(res, open(f"{OUT}/perm2.json", "w"), indent=1); print(json.dumps(res, indent=1))
