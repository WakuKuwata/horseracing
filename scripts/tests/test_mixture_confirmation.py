"""129 confirmation is a six-member contract, never a production switch."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

S=importlib.util.spec_from_file_location('mixture_confirmation_test',Path(__file__).parents[1]/'mixture_confirmation.py')
m=importlib.util.module_from_spec(S);S.loader.exec_module(m)


def save(path,value):
    path.write_text(json.dumps(value));return {'path':str(path),'sha256':m.digest(path)}


@pytest.fixture
def context(tmp_path,monkeypatch):
    now=dt.datetime(2026,9,8,12,tzinfo=dt.timezone.utc)
    monkeypatch.setattr(m,'now',lambda:now);monkeypatch.setattr(m,'LEDGER',tmp_path/'shared-ledger.json')
    bundle=tmp_path/'bundle.json';save(bundle,{'bundle_id':'bundle129','members':m.MEMBERS,'mode':'shadow'})
    monkeypatch.setattr(m,'validate_bundle',lambda path,sha: m.read(path))
    artifact=tmp_path/'calibration.pkl';artifact.write_bytes(b'fixture')
    anchor=tmp_path/'anchor.json';save(anchor,{'schema_version':1,'artifact_kind':'mixture_shadow_anchor','model_version':'lgbm-094-cap900','created_at':'2026-09-08T00:00:00Z','can_adopt':False,'files':{str(artifact):m.digest(artifact)},'calibration':{'mode':'frozen_stage_discount','artifact_path':str(artifact),'sha256':m.digest(artifact),'asof_date':'2026-09-08'}})
    manifest=m.draft(bundle,anchor,selection_used_through='2026-09-07',start='2026-09-10',end='2027-09-09',min_days=150)
    method=tmp_path/'independent.py';method.write_text('# Independent fixture method')
    def reviewed(kind,payload):
        payload={'artifact_kind':kind,**payload}
        proof=tmp_path/f'{kind}.json';ref=save(proof,payload)
        audit={'status':'PASS','reviewer_role':'independent','method':{'path':str(method),'sha256':m.digest(method)},'evidence':ref,'bundle_manifest_sha256':manifest['bundle_manifest_sha256'],'anchor_model_sha256':manifest['anchor_model_sha256'],'primary_regime':'preweight','adequate_for_confirmation':True,'justification':'Synthetic fixture only'}
        ref['review']=save(tmp_path/f'{kind}-review.json',audit);return ref
    dev={'artifact_kind':'mixture_power_development','bundle_manifest_sha256':manifest['bundle_manifest_sha256'],'anchor_model_sha256':manifest['anchor_model_sha256'],'primary_regime':'preweight','rows':[{'race_id':str(i),'race_day':f'2026-08-{i+1:02d}','candidate_winner_nll':2.+.002*(i%2),'anchor_winner_nll':2.01} for i in range(20)]}
    devref=save(tmp_path/'development.json',dev)
    power=m.power_plan(devref,effects=[.01],max_days=150)
    manifest['evidence']['power']=reviewed('mixture_power_plan',power)
    manifest['power_effect']=.01
    manifest['evidence']['noise']=reviewed('mixture_noise_justification',{'noise':m.NOISE,'measurement':'single_model_transfer','bundle_variance_reduction_claimed':False,'justification':'Conservative transfer assessed in fixture'})
    manifest['evidence']['compatibility']=reviewed('mixture_serving_compatibility',{'bundle_manifest_sha256':manifest['bundle_manifest_sha256'],'anchor_model_sha256':manifest['anchor_model_sha256'],'primary_regime':'preweight','all_six_members':True,'all_heads_roundtrip':True,'same_asof_inputs':True,'no_postaverage_transform':True,'pre_result_capture_supported':True})
    return manifest,reviewed


def test_ready_is_not_adoption_and_no_ledger_write(context):
    manifest,_=context;r=m.preflight(manifest)
    assert r['state']=='READY' and r['can_adopt'] is False and not m.LEDGER.exists()


@pytest.mark.parametrize('name',['power','noise','compatibility'])
def test_missing_evidence_not_ready(context,name):
    manifest,_=context;manifest['evidence'][name]=None
    assert m.preflight(manifest)['state']=='NOT_READY'
    with pytest.raises(m.ConfirmationError):m.reserve(manifest)
    assert not m.LEDGER.exists()


@pytest.mark.parametrize('field',['selected_seed','seed_check'])
def test_fake_selected_seed_rejected(context,field):
    manifest,_=context;manifest[field]=42
    with pytest.raises(m.ConfirmationError):m.preflight(manifest)


@pytest.mark.parametrize('mutation',['order','missing','replicate','folds','k','past','too_many_days','replay'])
def test_contract_boundaries(context,mutation):
    manifest,_=context
    if mutation=='order':manifest['member_seed_pairs'].reverse()
    if mutation=='missing':manifest['member_seed_pairs'].pop()
    if mutation=='replicate':manifest['independent_bundle_replicates']=6
    if mutation=='folds':manifest['noise']['n_effective_folds']=7
    if mutation=='k':manifest['noise']['k_seeds']=6
    if mutation=='past':manifest['eval_window']['from']='2026-09-08'
    if mutation=='too_many_days':manifest['eval_window']['min_eval_days']=999
    if mutation=='replay':manifest['primary_regime']='full_information_replay'
    with pytest.raises(m.ConfirmationError):m.preflight(manifest)


def test_review_and_data_hash_tampering_rejected(context):
    manifest,_=context;ref=manifest['evidence']['power'];Path(ref['path']).write_text('{}')
    with pytest.raises(m.ConfirmationError):m.preflight(manifest)


def test_power_not_effect_floor_and_noise_floor():
    out=m.required_days(.025,.001,confidence=.9875,target_power=.8,max_days=500)
    assert out['required_days'] is None and out['reason']=='BELOW_TRANSFERRED_NOISE_FLOOR'
    other=m.required_days(.025,.01,confidence=.9875,target_power=.8,max_days=500)
    assert other['required_days']>0 and other['practical'] is True


def test_reserve_common_ledger_and_no_duplicate(context):
    manifest,_=context;before=copy.deepcopy(manifest);r=m.reserve(manifest)
    assert r['slot']==1 and r['ensemble_contract']=='mixture_confirmation_v1' and r['can_adopt'] is False
    ledger=m.budget._read_ledger(m.LEDGER,m.budget._hash(m.policy_api.load_policy()))
    assert len(ledger['reservations'])==1 and ledger['reservations'][0]['manifest']==manifest==before
    with pytest.raises(m.ConfirmationError,match='already reserved'):m.reserve(manifest)


def test_existing_eight_legacy_slots_not_reset(context):
    manifest,_=context;policy=m.policy_api.load_policy();entries=[]
    for i in range(8):
        old={'bundle_id':f'old{i}','bundle_manifest_sha256':f'{i:064x}','eval_window':{'from':'2026-09-09'}}
        entries.append({'bundle_id':old['bundle_id'],'bundle_manifest_sha256':old['bundle_manifest_sha256'],'registration_sha256':m.budget._hash(old),'year':2026,'slot':i+1,'manifest':old})
    save(m.LEDGER,{'schema_version':1,'policy_sha256':m.budget._hash(policy),'reservations':entries});before=m.LEDGER.read_bytes()
    with pytest.raises(m.ConfirmationError,match='exhausted'):m.reserve(manifest)
    assert m.LEDGER.read_bytes()==before


def test_future_readout_stays_unadopted(context):
    manifest,_=context;receipt=m.reserve(manifest)
    assert m.final_readout(manifest,receipt,None)['state']=='COLLECTING'


def test_finish_missing_evidence_stays_no_decision(context,monkeypatch):
    manifest,_=context;receipt=m.reserve(manifest)
    monkeypatch.setattr(m,'now',lambda:dt.datetime(2027,9,11,tzinfo=dt.timezone.utc))
    assert m.final_readout(manifest,receipt,None)['state']=='NO_DECISION'


def final_fixture(context,tmp_path,monkeypatch,mutation=None):
    manifest,reviewed=context;receipt=m.reserve(manifest)
    monkeypatch.setattr(m,'now',lambda:dt.datetime(2027,9,11,tzinfo=dt.timezone.utc))
    rows=[]
    for i in range(150):
        day=str(dt.date(2026,9,10)+dt.timedelta(days=i))
        rows.append({'race_id':str(i),'race_day':day,'input_captured_at':day+'T00:00:00Z','predicted_at':day+'T00:01:00Z','scheduled_start':day+'T01:00:00Z','pre_result':True,'classification':'prospective','candidate_input_sha256':'a'*64,'anchor_input_sha256':'a'*64,'candidate_started_ids':['a','b'],'anchor_started_ids':['a','b'],**{k:manifest[k] for k in ('bundle_manifest_sha256','anchor_model_sha256','primary_regime')}})
    point=-.01;pad=m.statistics.NormalDist().inv_cdf(.99375)*.001816
    metrics={'winner_nll_diff':point,'sample_ci_low':-.011,'sample_ci_high':-.009,'total_ci_low':point-m.math.hypot(.001,pad),'total_ci_high':point+m.math.hypot(.001,pad),'top2_diff':0.,'top3_diff':0.,'candidate_ece':.01,'anchor_ece':.01}
    groups={k:{'point':0.,'ci_low':-.0002,'ci_high':.0002,'margin':.005 if k=='recent_year_only' else .001,'n_days':150,'decision':'PASS'} for k in ('canonical','nk','recent_year_only')}
    if mutation:mutation(rows,metrics,groups)
    ref=save(tmp_path/'future-records.json',rows)
    report={'registration_sha256':m.budget._hash(manifest),'eval_window':manifest['eval_window'],'noise':m.NOISE,'prospective_records':ref,'all_scheduled_eligible_race_ids':sorted(r['race_id'] for r in rows),'all_pair_coverage':True,'metrics':metrics,'critical_subgroups':groups,'bootstrap':{'b':4000,'alpha':.0125,'seed':20260907,'block':'race_day'}}
    evidence=reviewed('mixture_confirmation_final_evidence',report)
    return manifest,receipt,evidence


def test_valid_final_pass_still_does_not_activate(context,tmp_path,monkeypatch):
    args=final_fixture(context,tmp_path,monkeypatch)
    out=m.final_readout(*args)
    assert out['state']=='CONFIRMATION_PASS' and out['can_adopt'] is False and out['eligible_for_verdict'] is True


@pytest.mark.parametrize('mutation',[
    lambda rows,metrics,groups:rows[0].update(pre_result=False),
    lambda rows,metrics,groups:rows[0].update(classification='rehearsal'),
    lambda rows,metrics,groups:rows[0].update(predicted_at=rows[0]['scheduled_start']),
    lambda rows,metrics,groups:rows[0].update(anchor_input_sha256='b'*64),
    lambda rows,metrics,groups:rows[0].update(anchor_started_ids=['a','c']),
    lambda rows,metrics,groups:rows[0].update(bundle_manifest_sha256='b'*64),
    lambda rows,metrics,groups:rows[0].update(race_day='2026-09-09'),
    lambda rows,metrics,groups:metrics.update(total_ci_high=-.02),
    lambda rows,metrics,groups:groups['nk'].update(ci_high=.02),
])
def test_final_semantic_tampering_even_with_new_receipts_rejected(context,tmp_path,monkeypatch,mutation):
    args=final_fixture(context,tmp_path,monkeypatch,mutation)
    with pytest.raises(m.ConfirmationError):m.final_readout(*args)


def test_final_short_days_no_decision(context,tmp_path,monkeypatch):
    args=final_fixture(context,tmp_path,monkeypatch,lambda rows,metrics,groups:rows.pop())
    assert m.final_readout(*args)['state']=='NO_DECISION'


def test_final_quality_failure_rejected(context,tmp_path,monkeypatch):
    args=final_fixture(context,tmp_path,monkeypatch,lambda rows,metrics,groups:metrics.update(top3_diff=.0006))
    assert m.final_readout(*args)['state']=='REJECT'


def test_inconclusive_critical_is_not_pass(context,tmp_path,monkeypatch):
    args=final_fixture(context,tmp_path,monkeypatch,lambda rows,metrics,groups:groups['nk'].update(ci_low=-.002,ci_high=.002,decision='INCONCLUSIVE_LOW_PRECISION'))
    assert m.final_readout(*args)['state']=='NO_DECISION'


def test_review_insufficient_is_not_ready(context):
    manifest,_=context;ref=manifest['evidence']['noise']['review'];value=m.read(ref['path']);value['adequate_for_confirmation']=False
    manifest['evidence']['noise']['review']=save(Path(ref['path']),value)
    assert m.preflight(manifest)['state']=='NOT_READY'


def test_missing_review_file_is_not_ready(context):
    manifest,_=context;Path(manifest['evidence']['power']['review']['path']).unlink()
    assert m.preflight(manifest)['state']=='NOT_READY'


def test_receipt_policy_or_prefix_cannot_be_forged(context):
    manifest,_=context;receipt=m.reserve(manifest);receipt['ledger_sha256_at_reservation']='0'*64
    with pytest.raises(m.ConfirmationError):m.final_readout(manifest,receipt,None)


def test_changed_confirmation_method_rejected(context):
    manifest,_=context;manifest['confirmation_method_sha256']='a'*64
    with pytest.raises(m.ConfirmationError,match='method changed'):m.preflight(manifest)


def test_boolean_noise_count_rejected(context):
    manifest,_=context;manifest['noise']['k_seeds']=True
    with pytest.raises(m.ConfirmationError,match='counts'):m.preflight(manifest)


def test_forged_power_plan_rejected_after_review_reissued(context):
    manifest,reviewed=context;plan=m.read(manifest['evidence']['power']['path']);plan['day_cluster_influence_sd']*=.01
    manifest['evidence']['power']=reviewed('mixture_power_plan',plan)
    with pytest.raises(m.ConfirmationError,match='Power plan differs'):m.preflight(manifest)
