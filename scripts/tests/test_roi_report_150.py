"""The selected tickets are immutable; only the deployed amount changes."""
import copy
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

PATH=Path(__file__).resolve().parents[1]/'roi_explore/roi_improvement_150/report.py'
SPEC=importlib.util.spec_from_file_location('test_roi_report150',PATH)
M=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def sample():
    frame=pd.DataFrame(dict(race_id=['202001010101']*3+['202001010102']*3+['202001010103']*3,
        horse_id=['h1','h2','h3']*3,horse_number=[1,2,3]*3,
        race_date=['2020-01-01']*3+['2020-01-02']*3+['2020-01-03']*3,
        year=[2020]*9,odds=[3.,3.,3.]*3,won=[1,0,0]*3,field_size=[3]*9,
        p=[.5,.1,.1,.1,.1,.1,.5,.5,.5]))
    return M.common.prior.score(frame)


def test_same_tickets_fixed_amounts_cash_and_parent_unchanged():
    score,races,bets=sample()
    before_score,before_races,before_bets=copy.deepcopy(score),races.copy(deep=True),bets.copy(deep=True)
    flat,rs,bs=M.fixed_score(score,races,bets)
    pd.testing.assert_frame_equal(bs[M.KEYS],bets[M.KEYS])
    assert bs.stake_yen.tolist()==[100]*4
    assert bs.units.tolist()==[1]*4
    assert rs.stake_yen.tolist()==[100,0,300]
    assert rs.payout_yen.tolist()==[300,0,300]
    assert rs.budget_yen.tolist()==[1000]*3
    assert flat['policy']['stake_yen']==400
    assert flat['policy']['profit_yen']==200
    assert flat['policy']['unspent_cash_yen']==2600
    assert flat['quality']==score['quality']
    assert flat['ev_band_calibration']==score['ev_band_calibration']
    assert flat['policy']['n_hits']==score['policy']['n_hits']
    assert flat['by_year']['2020']['policy']==flat['policy']
    assert score==before_score
    pd.testing.assert_frame_equal(races,before_races)
    pd.testing.assert_frame_equal(bets,before_bets)


def test_zero_selection_keeps_full_cash_calendar():
    _,races,bets=sample()
    bets=bets.iloc[:0].copy()
    races['stake_yen']=0.
    races['payout_yen']=0.
    races['profit_yen']=0.
    score=sample()[0]
    score['policy']=M.common.prior.policies._point(races,bets)
    flat,rs,bs=M.fixed_score(score,races,bets)
    assert bs.empty and rs.stake_yen.eq(0).all()
    assert flat['policy']['unspent_cash_yen']==3000
    assert flat['policy']['deployed_roi'] is None


def test_outcome_changes_do_not_change_tickets_or_stakes():
    score,races,bets=sample()
    bets=bets.copy()
    bets['won']=0
    bets['payout_yen']=0.
    score['policy']['n_hits']=0
    _,_,actual=M.fixed_score(score,races,bets)
    assert actual[M.KEYS].equals(bets[M.KEYS])
    assert actual.stake_yen.eq(100).all()
    assert actual.payout_yen.eq(0).all()


@pytest.mark.parametrize('bad_kind',['duplicate_ticket','zero_stake','outside_calendar','duplicate_race','wrong_budget','cap'])
def test_invalid_frozen_contract_rejected(bad_kind):
    score,races,bets=sample()
    if bad_kind=='duplicate_ticket':
        bets=pd.concat([bets,bets.iloc[:1]])
    elif bad_kind=='zero_stake':
        bets=bets.copy()
        bets.iloc[0,bets.columns.get_loc('stake_yen')]=0
    elif bad_kind=='outside_calendar':
        bets=bets.copy()
        bets.iloc[0,bets.columns.get_loc('race_id')]='other'
    elif bad_kind=='duplicate_race':
        races=pd.concat([races,races.iloc[:1]])
    elif bad_kind=='wrong_budget':
        races=races.copy()
        races.loc[0,'budget_yen']=999
    else:
        bets=pd.concat([bets.iloc[:1]]*11).reset_index(drop=True)
        bets['horse_id']=[f'h{i}' for i in range(11)]
    with pytest.raises(ValueError):
        M.fixed_score(score,races,bets)


def test_seven_identical_contrasts_include_nonbet_days():
    _,rs,_=sample()
    arms=('current_reference',*M.research.CONTROLS,*M.research.CANDIDATES)
    tables={a:rs.copy() for a in arms}
    result=M.common.paired(tables,M.research.CONTRASTS,repetitions=60,family_size=7)
    assert result['family_size_per_metric']==7 and len(result['results'])==7
    for rec in result['results'].values():
        for metric in rec.values():
            assert metric['difference']==0
            assert metric['bonferroni_familywise95_ci']==[0,0]


def test_pairing_rejects_different_calendar_or_family():
    _,rs,_=sample()
    tables={a:rs.copy() for a in (*M.research.CONTROLS,*M.research.CANDIDATES)}
    tables[M.research.CANDIDATES[0]]=rs.iloc[:2]
    with pytest.raises(ValueError,match='populations'):
        M.common.paired(tables,M.research.CONTRASTS,repetitions=5,family_size=7)
    tables[M.research.CANDIDATES[0]]=rs
    with pytest.raises(ValueError,match='family'):
        M.common.paired(tables,M.research.CONTRASTS[:6],repetitions=5,family_size=7)
