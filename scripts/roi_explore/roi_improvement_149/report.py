"""Score the fixed p/sqrt(S) candidate using the unchanged single-win policy."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    sys.modules[name]=obj
    spec.loader.exec_module(obj)
    return obj


research=load('roi149_run',HERE/'run.py')
common=load('roi149_source148_report',HERE.parent/'roi_improvement_148/report.py')
models=research.models
LABELS=dict(current_reference='現行保存予測（参照）',baseline='現行77特徴・同条件基準',
    weighted_baseline='③・正規化前',normalized_weighted_baseline='③・完全正規化',
    half_normalized_weighted_baseline='③・半補正 p/√S')


def half_correct(frame):
    # Frozen148 validation checks the complete field, duplicates, p and positive S.
    common.normalize(frame)
    mass=frame.groupby('race_id',sort=False).p.transform('sum').to_numpy(float)
    result=frame[models.PRED_KEYS].copy()
    result['p']=frame.p.to_numpy(float)/np.sqrt(mass)
    if not np.isfinite(result.p).all() or not result.p.between(0,1).all():
        raise ValueError('Invalid corrected score')
    expected=np.sqrt(frame.groupby('race_id').p.sum().to_numpy(float))
    if not np.allclose(result.groupby('race_id').p.sum(),expected,atol=1e-14,rtol=0):
        raise ValueError('Partial score sum differs')
    return result


def markdown(result):
    lines=['# 149 固定した半補正 p/√S','',
        '2019〜2026-06-21、24,855レース・343,883頭・807日。単勝EV>1.2、1レース予算1,000円。',
        '③の15seed raw平均pを先に作り、全出走馬の合計Sの平方根で割って購入判断。指数0.5固定。',
        '半補正は購入用スコアで、合計1の勝率ではない。確定オッズ由来の仮想精算。案2の固定100円配分は保留。','',
        '| 条件 | 購入数 | 投資額 | 払戻 | 利益 | 回収率 | winner NLL |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for arm,record in result['arms'].items():
        p=record['policy']
        roi=f"{p['deployed_roi']*100:.2f}%" if p['deployed_roi'] is not None else '—'
        lines.append(f"| {LABELS[arm]} | {p['n_bets']:,} | {p['stake_yen']:,.0f}円 | {p['payout_yen']:,.0f}円 | {p['profit_yen']:+,.0f}円 | {roi} | {record['quality']['winner_nll']:.6f} |")
    lines += ['', '## 3固定比較','', '全開催日4000回paired bootstrap、各指標内3比較Bonferroni補正。主指標は共通予算利益差。']
    for key,c in result['paired']['results'].items():
        a,b=key.split('_minus_')
        lo,hi=c['profit_per_budget']['bonferroni_familywise95_ci']
        lines.append(f"- {LABELS[a]} − {LABELS[b]}: 予算利益差 {c['profit_per_budget']['difference']*100:+.4f}pt、補正95%区間 [{lo*100:+.4f}, {hi*100:+.4f}]pt。")
    lines += ['', '## 年別','', '| 年 | 条件 | 利益 | 回収率 |', '|---|---|---:|---:|']
    for year in result['arms']['baseline']['by_year']:
        for arm,record in result['arms'].items():
            p=record['by_year'][year]['policy']
            roi=f"{p['deployed_roi']*100:.2f}%" if p['deployed_roi'] is not None else '—'
            lines.append(f"| {year} | {LABELS[arm]} | {p['profit_yen']:+,.0f}円 | {roi} |")
    lines += ['', '## 感度・リスク','']
    for key,r in result['sensitivity'].items():
        lines.append(f"- {key}: 既知202608030111を双方から除外した利益差 {r['without_known_race_delta_yen']:+,.0f}円。最大差レース {r['top_difference_race']} を除いた差 {r['without_top_difference_yen']:+,.0f}円。診断のみで購入除外ルールではない。")
    for arm,r in result['arms'].items():
        p=r['policy']
        lines.append(f"- {LABELS[arm]}: 最大DD {p['max_race_settlement_drawdown_yen']:,.0f}円、上位5払戻0の利益 {p['profit_without_top5_payouts_yen']:+,.0f}円。")
    lines += ['', '③のraw/完全正規化/半補正で、評価用正規化winner NLLは不変。微小な差は浮動計算による。', '',
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
    frames[research.CANDIDATE]=half_correct(frames['weighted_baseline'])
    dest=out/'comparison'
    dest.mkdir(exist_ok=False)
    scores,tables={},{}
    previous=json.loads((research.PREVIOUS/'comparison/summary.json').read_text())
    for arm,frame in frames.items():
        pd.testing.assert_frame_equal(frames['baseline'][models.PRED_KEYS],frame[models.PRED_KEYS],check_exact=True)
        scores[arm],tables[arm],bets=common.prior.score(frame)
        if arm != research.CANDIDATE and models.canonical(scores[arm])!=models.canonical(previous['arms'][arm]):
            raise ValueError('Frozen148 control score differs')
        frame.to_parquet(dest/f'{arm}_ensemble.parquet',index=False)
        tables[arm].to_parquet(dest/f'{arm}_races.parquet',index=False)
        bets.to_parquet(dest/f'{arm}_bets.parquet',index=False)
    quality=scores[research.CANDIDATE]['quality']
    if quality['races']!=24855 or quality['rows']!=343883:
        raise ValueError('Frozen population differs')
    if not np.isclose(quality['winner_nll'],scores['weighted_baseline']['quality']['winner_nll'],atol=5e-15,rtol=0):
        raise ValueError('Winner NLL must be invariant')
    boot=proto['bootstrap']
    result=dict(arms=scores,
        paired=common.paired(tables,proto['contrasts'],boot['repetitions'],boot['seed'],boot['family_size']),
        sensitivity=common.sensitivity(tables,proto['contrasts']),
        mass_diagnostics={a:common.mass_diagnostic(f) for a,f in frames.items()},
        correction=proto['correction'],new_fits=0,reused_control_fits=240,proposal2='held',
        protocol_sha256=models.sha(out/'protocol.json'),
        ensemble_rule='mean15 raw probabilities FIRST, then fixed p/sqrt(S) for original weighted baseline only',
        settlement='closing_odds_proxy',can_adopt=False,untouched_holdout=False,eligible_for_verdict=False,
        limitations=proto['limitations'],wall_seconds=time.monotonic()-started)
    research.verify(out)
    research.write(dest/'summary.json',result)
    (dest/'report.md').write_text(markdown(result))
    print(json.dumps(dict(status='complete',out=str(dest),paired=result['paired']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
