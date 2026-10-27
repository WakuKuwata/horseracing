import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"roi_explore"))
import explore_new_patterns_20260930 as m


def test_unknown_values_and_day_boundaries_do_not_qualify():
    arr={"race_id":np.array(["a","a","b","c"]),"days_since_last":np.array([13.,14.,56.,np.nan]),
         "market_er":np.array([1.3,1.2,1.21,1.4])}
    p={"conditions":[{"field":"days_since_last","op":"ge","value":14},
                      {"field":"market_er","op":"gt","value":1.2}]}
    assert m.select(arr,p,{}).tolist()==[False,False,True,False]
    with pytest.raises(ValueError):m.condition_mask({"won":np.ones(4)}, {"field":"won","op":"eq","value":1})


def test_near_miss_ranking_cannot_read_later_period_results():
    def row(name,d,q,c):
        s={"n_bets":400,"n_hits":30,"n_days":150,"largest_hit_share":.1}
        return {"id":name,"family":"x","screen_pass":False,"periods":{"D":dict(s,roi=d),"Q":dict(s,roi=q),"C":{"roi":c}}}
    a=[row("b",1.1,.9,10),row("a",1.1,.9,.1),row("c",1.5,.8,99)]
    assert m.shortlist(a)==([],['a','b'])
    for r in a:r['periods']['C']['roi']=1000
    assert m.shortlist(a)==([],['a','b'])


def test_bootstrap_includes_zero_bet_days_and_preserves_break_even():
    df=pd.DataFrame({"race_date":["2020-01-01","2020-01-01"],"ret":[0.,0.]})
    result=m.evidence(df,["2020-01-01","2020-01-02"],400)
    assert result['ci95']=={'lo':1.,'hi':1.}
    assert result['zero_stake_repetitions']>0


def test_old_band_and_new_bounds_canonicalize_identically():
    assert m.canonical([{'field':'odds','op':'in_band','lo':3,'hi':10}])==m.canonical([
        {'field':'odds','op':'ge','value':3.},{'field':'odds','op':'lt','value':10}])
