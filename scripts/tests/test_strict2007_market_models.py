import sys
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"roi_explore"))
import strict2007_market_models as m


def test_no_pre2007_or_target_year_training():
    y=np.array([2005,2006,2007,2008,2009,2010,2011])
    assert m.eligible_train(y,2010).tolist()==[False,False,True,True,True,False,False]


def test_support_and_return_gate_require_both():
    d=dict(n_hits=20,n_days=100,largest_hit_share=.5,roi=1.)
    assert m.passes(d)
    assert not m.passes({**d,"n_days":99})
    assert not m.passes({**d,"roi":.999})
    assert not m.passes({**d,"largest_hit_share":.51})
