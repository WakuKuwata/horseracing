"""Fixed100-yen ticket stakes; predictions and existing parent selections stay fixed."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys
import time

import pandas as pd

HERE=Path(__file__).resolve().parent


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    sys.modules[name]=obj
    spec.loader.exec_module(obj)
    return obj


research=load('roi150_run',HERE/'run.py')
common=load('roi150_source148_report',HERE.parent/'roi_improvement_148/report.py')
models=research.models
KEYS=['race_id','horse_id']
LABELS=dict(current_reference='現行保存予測（参照）',baseline='現行77特徴・同条件基準',
    weighted_baseline='③・従来配分',normalized_weighted_baseline='③完全正規化・従来配分',
    flat_weighted_baseline='③・同買い目100円固定',
    flat_normalized_weighted_baseline='③完全正規化・同買い目100円固定')


def fixed_score(parent,races,bets):
    if bets.duplicated(KEYS).any() or not bets.stake_yen.gt(0).all():
        raise ValueError('Invalid frozen parent bets')
    if bets.groupby('race_id').size().gt(10).any():
        raise ValueError('Frozen ticket cap differs')
    if races.duplicated('race_id').any() or not races.budget_yen.eq(1000).all():
        raise ValueError('Invalid race calendar/budget')
    if not bets.race_id.isin(races.race_id).all():
        raise ValueError('Ticket outside race calendar')
    new_bets=bets.copy(deep=True)
    new_bets['units']=1
    new_bets['stake_yen']=100
    new_bets['payout_yen']=100*new_bets.won.astype(float)*new_bets.odds.astype(float)
    totals=new_bets.groupby(['race_date','race_id']).agg(stake_yen=('stake_yen','sum'),payout_yen=('payout_yen','sum'))
    new_races=races.copy(deep=True)
    amounts=totals.reindex(pd.MultiIndex.from_frame(races[['race_date','race_id']]),fill_value=0)
    new_races['stake_yen']=amounts.stake_yen.to_numpy(float)
    new_races['payout_yen']=amounts.payout_yen.to_numpy(float)
    new_races['profit_yen']=new_races.payout_yen-new_races.stake_yen
    if (new_races.stake_yen>new_races.budget_yen).any():
        raise ValueError('Fixed stakes exceed race budget')
    result=copy.deepcopy(parent)
    result['policy']=common.prior.policies._point(new_races,new_bets)
    for year,record in result['by_year'].items():
        rs=new_races.loc[new_races.race_date.str[:4].eq(year)]
        bs=new_bets.loc[new_bets.race_id.isin(rs.race_id)]
        record['policy']=common.prior.policies._point(rs,bs)
    for name in ('n_bets','n_hits','n_betting_races'):
        if result['policy'][name]!=parent['policy'][name]:
            raise ValueError('Frozen selected ticket counts differ')
    pd.testing.assert_frame_equal(new_bets[KEYS],bets[KEYS],check_exact=True)
    return result,new_races,new_bets


def examples(tables,ids):
    result={}
    for rid in ids:
        result[rid]={}
        for arm,table in tables.items():
            row=table.loc[table.race_id.eq(rid)]
            if len(row)!=1:
                raise ValueError('Missing fixed allocation example')
            result[rid][arm]=row[['stake_yen','payout_yen','profit_yen']].iloc[0].to_dict()
    return result


def markdown(result):
    lines=['# 150 同じ買い目を1頭100円固定','',
        '2019〜2026-06-21、24,855レース・343,883頭・807日。単勝EV>1.2、最大10頭・予算1,000円/レース。',
        '③raw/③完全正規化の既存買い目を維持し、各馬100円に固定。未投資予算は現金、予測と選別を変えない。確定オッズ由来の仮想精算。','',
        '| 条件 | 購入数 | 投資額 | 払戻 | 利益 | 回収率 |',
        '|---|---:|---:|---:|---:|---:|']
    for arm,r in result['arms'].items():
        p=r['policy']
        roi=f"{p['deployed_roi']*100:.2f}%" if p['deployed_roi'] is not None else '—'
        lines.append(f"| {LABELS[arm]} | {p['n_bets']:,} | {p['stake_yen']:,.0f}円 | {p['payout_yen']:,.0f}円 | {p['profit_yen']:+,.0f}円 | {roi} |")
    lines += ['', '## 7固定比較','', '4000回開催日paired bootstrap、各指標内family7 Bonferroni。投資規模差を含む利益差と実投資回収率を区別。']
    for key,r in result['paired']['results'].items():
        a,b=key.split('_minus_')
        lo,hi=r['profit_per_budget']['bonferroni_familywise95_ci']
        lines.append(f"- {LABELS[a]} − {LABELS[b]}: 共通予算利益差 {r['profit_per_budget']['difference']*100:+.4f}pt、補正95%区間 [{lo*100:+.4f}, {hi*100:+.4f}]pt。")
    lines += ['', '## 年別','', '| 年 | 条件 | 投資額 | 利益 | 回収率 |','|---|---|---:|---:|---:|']
    for year in result['arms']['baseline']['by_year']:
        for arm,r in result['arms'].items():
            p=r['by_year'][year]['policy']
            roi=f"{p['deployed_roi']*100:.2f}%" if p['deployed_roi'] is not None else '—'
            lines.append(f"| {year} | {LABELS[arm]} | {p['stake_yen']:,.0f}円 | {p['profit_yen']:+,.0f}円 | {roi} |")
    lines += ['', '## 感度とリスク','']
    for key,r in result['sensitivity'].items():
        lines.append(f"- {key}: 既知レース双方除外の利益差 {r['without_known_race_delta_yen']:+,.0f}円、最大正差レース{r['top_difference_race']}双方除外の差 {r['without_top_difference_yen']:+,.0f}円。診断のみ。")
    for arm,r in result['arms'].items():
        p=r['policy']
        lines.append(f"- {LABELS[arm]}: 最大DD {p['max_race_settlement_drawdown_yen']:,.0f}円、上位5払戻0の利益 {p['profit_without_top5_payouts_yen']:+,.0f}円。")
    lines += ['', '予測・NLL・買い目・的中数は各親と同一。利益とDDの減少には実投資減少の影響がある。同投資額への後付け拡大を行わない。','',
        *[f'- {x}' for x in result['limitations']], '', f"Protocol SHA256: `{result['protocol_sha256']}`", '']
    return '\n'.join(lines)


def main():
    started=time.monotonic()
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--out',type=Path,default=research.OUT)
    args=parser.parse_args()
    out=args.out
    proto=research.verify(out)
    frames,_=research.references()
    dest=out/'comparison'
    dest.mkdir(exist_ok=False)
    scores,tables,betsets={},{},{}
    previous=json.loads((research.PREVIOUS/'comparison/summary.json').read_text())
    for arm,frame in frames.items():
        pd.testing.assert_frame_equal(frames['baseline'][models.PRED_KEYS],frame[models.PRED_KEYS],check_exact=True)
        scores[arm],tables[arm],betsets[arm]=common.prior.score(frame)
        if models.canonical(scores[arm])!=models.canonical(previous['arms'][arm]):
            raise ValueError('Frozen148 control score differs')
        saved=research.PREVIOUS/f'comparison/{arm}_bets.parquet'
        pd.testing.assert_frame_equal(betsets[arm].reset_index(drop=True),pd.read_parquet(saved).reset_index(drop=True),check_exact=True)
    for parent,candidate in zip(research.PARENTS,research.CANDIDATES,strict=True):
        scores[candidate],tables[candidate],betsets[candidate]=fixed_score(scores[parent],tables[parent],betsets[parent])
        frames[candidate]=frames[parent].copy(deep=True)
        if not betsets[candidate].stake_yen.eq(100).all() or not betsets[candidate].units.eq(1).all():
            raise ValueError('Fixed100 stake differs')
    for arm,frame in frames.items():
        frame.to_parquet(dest/f'{arm}_ensemble.parquet',index=False)
        tables[arm].to_parquet(dest/f'{arm}_races.parquet',index=False)
        betsets[arm].to_parquet(dest/f'{arm}_bets.parquet',index=False)
    if scores['baseline']['quality']['races']!=24855 or scores['baseline']['quality']['rows']!=343883:
        raise ValueError('Frozen population differs')
    boot=proto['bootstrap']
    result=dict(arms=scores,
        paired=common.paired(tables,proto['contrasts'],boot['repetitions'],boot['seed'],boot['family_size']),
        sensitivity=common.sensitivity(tables,proto['contrasts']),
        allocation_examples=examples(tables,proto['sensitivity']['allocation_examples']),
        allocation=proto['allocation'],new_fits=0,reused_control_fits=240,proposal2='executed',
        protocol_sha256=models.sha(out/'protocol.json'),
        ensemble_rule='unchanged mean15 raw and full-normalized parent probabilities; fixed100yen on frozen ticket keys',
        settlement='closing_odds_proxy',can_adopt=False,untouched_holdout=False,eligible_for_verdict=False,
        limitations=proto['limitations'],wall_seconds=time.monotonic()-started)
    research.verify(out)
    research.write(dest/'summary.json',result)
    (dest/'report.md').write_text(markdown(result))
    print(json.dumps(dict(status='complete',out=str(dest),paired=result['paired']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
