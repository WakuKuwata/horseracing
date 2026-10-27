"""Read frozen148 allocation changes; no new policy is scored."""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'artifacts/148-postprediction-normalization/comparison'
OUT=ROOT/'artifacts/roi-normalization-followup-20261025/allocation_detail.json'
KEYS=['race_id','horse_id']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    frames={}
    paths=[Path(__file__).resolve()]
    for label,arm in [('raw','weighted_baseline'),('normalized','normalized_weighted_baseline')]:
        pred_path=SOURCE/f'{arm}_ensemble.parquet'
        bets_path=SOURCE/f'{arm}_bets.parquet'
        paths += [pred_path,bets_path]
        frame=pd.read_parquet(pred_path)
        bets=pd.read_parquet(bets_path).set_index(KEYS)
        frame['stake']=bets.stake_yen.reindex(pd.MultiIndex.from_frame(frame[KEYS]),fill_value=0).to_numpy()
        frames[label]=frame
    digests={str(p.relative_to(ROOT)):sha(p) for p in paths}
    a,b=frames['raw'],frames['normalized']
    d=a.drop(columns=['p','stake']).copy()
    d['p_raw'],d['p_normalized']=a.p,b.p
    d['ev_raw'],d['ev_normalized']=a.p*a.odds,b.p*b.odds
    d['stake_raw'],d['stake_normalized']=a.stake,b.stake
    d['delta_yen']=(b.stake-a.stake)*(a.won*a.odds-1)
    changed=d.year.eq(2026)&d.stake_raw.gt(0)&d.stake_normalized.gt(0)&d.stake_raw.ne(d.stake_normalized)
    common=d.loc[changed]
    assert len(common)==10 and np.isclose(common.delta_yen.sum(),-22600)
    result=dict(new_fits=0,new_policies_scored=0,source_sha256=digests,
        common_stake_changed_2026=common.to_dict('records'),
        examples={rid:d[d.race_id.eq(rid)&(d.stake_raw.gt(0)|d.stake_normalized.gt(0)|d.won)].to_dict('records')
            for rid in ['202609010807','202610011209','202608030111']})
    for name,digest in digests.items():
        assert sha(ROOT/name)==digest
    with OUT.open('x') as f:
        json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.write('\n')
    print(json.dumps(result['examples'],ensure_ascii=False))


if __name__=='__main__':
    main()
