"""Supplement: zero price change with canonical popularity, separate immutable diagnostic."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import price_robustness as P


def main():
    out=P.OUT/"rank_control.json"
    if out.exists():raise RuntimeError("Refuse overwrite")
    scored=P.OUT/"scored_pairs.parquet"
    freeze={"source_sha256":P.R.sha(Path(__file__)),"scored_pairs_sha256":P.R.sha(scored),
            "recipe":"zero odds change; popularity=rank(min); all market features rebuilt; no policy retuning"}
    d=pd.read_parquet(scored).sort_values(["pair_id","horse_number"]).reset_index(drop=True)
    f=d.copy();f["odds"]=f.O_J.astype(float)
    market=P.D.market_block(f[["pair_id","horse_id","horse_number","odds"]],"pair_id")
    for c in P.D.MARKET_COLS:f[c]=market[c].to_numpy()
    f["popularity"]=f.groupby("pair_id").odds.rank(method="min")
    p,_=P.D.Scorer().score(f)
    ev=p*f.odds.to_numpy()
    parent=d.J_S3.to_numpy(bool);canonical=ev>1.2
    pert=pd.read_csv(P.OUT/"perturbed_targets.csv")
    e=pert.pivot(index=["pair_id","horse_id"],columns="mult",values="perturbed_ev")
    aligned=d[["pair_id","horse_id"]].merge(e.reset_index(),how="left",validate="one_to_one")
    guard=parent&(aligned[.9]>1.2).to_numpy()&(aligned[1.1]>1.2).to_numpy()
    result=dict(freeze=freeze,parent_n=int(parent.sum()),canonical_parent_n=int(canonical.sum()),
                parent_removed_by_zero_change_rank=int((parent&~canonical).sum()),
                parent_added_by_zero_change_rank=int((~parent&canonical).sum()),
                max_abs_ev_delta=float(np.max(np.abs(ev-d.EVe_J.to_numpy()))),
                mean_abs_ev_delta=float(np.mean(np.abs(ev-d.EVe_J.to_numpy()))),
                guard_n=int(guard.sum()),guard_survives_zero_rank_control=int((guard&canonical).sum()),
                interpretation="The guard changes both price and implied popularity; zero-price canonicalization is a control.")
    P.R.write(out,result)
    print(json.dumps(result,ensure_ascii=False,indent=1))


if __name__=="__main__":main()
