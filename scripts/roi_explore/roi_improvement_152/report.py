"""Normalize the saved unweighted pace91 ensemble and compare four fixed controls."""
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


research=load('roi152_run',HERE/'run.py')
common=load('roi152_source148_report',HERE.parent/'roi_improvement_148/report.py')
models=research.models
normalize=common.normalize
LABELS=dict(current_reference='現行保存予測（参照）',baseline='現行77特徴・同条件基準',
    weighted_baseline='③・正規化前',normalized_weighted_baseline='③・完全正規化',
    pace='重みなし展開91特徴・正規化前',normalized_pace='重みなし展開91特徴・完全正規化')


def markdown(result):
    lines=['# 152 重みなし展開91特徴の完全正規化','',
        '2019〜2026-06-21、24,855レース・343,883頭・807日。単勝EV>1.2、1レース予算1,000円。',
        'pace91の15seed raw確率平均を先に作り、全starter合計で割って購入判断。均等floor配賦を維持。',
        '確定オッズ由来の仮想精算。市場3列除去の案2は保留。','',
        '| 条件 | 購入数 | 投資額 | 払戻 | 利益 | 回収率 | winner NLL |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for arm,r in result['arms'].items():
        p=r['policy']
        roi=f"{p['deployed_roi']*100:.2f}%" if p['deployed_roi'] is not None else '—'
        lines.append(f"| {LABELS[arm]} | {p['n_bets']:,} | {p['stake_yen']:,.0f}円 | {p['payout_yen']:,.0f}円 | {p['profit_yen']:+,.0f}円 | {roi} | {r['quality']['winner_nll']:.6f} |")
    lines += ['', '## 4固定比較','', '4000回開催日paired bootstrap、各指標内family4 Bonferroni。主指標は共通予算利益差。']
    for key,r in result['paired']['results'].items():
        a,b=key.split('_minus_')
        lo,hi=r['profit_per_budget']['bonferroni_familywise95_ci']
        lines.append(f"- {LABELS[a]} − {LABELS[b]}: 共通予算利益差 {r['profit_per_budget']['difference']*100:+.4f}pt、補正95%区間 [{lo*100:+.4f}, {hi*100:+.4f}]pt。")
    lines += ['', '## 年別','', '| 年 | 条件 | 利益 | 回収率 |', '|---|---|---:|---:|']
    for year in result['arms']['baseline']['by_year']:
        for arm,r in result['arms'].items():
            p=r['by_year'][year]['policy']
            roi=f"{p['deployed_roi']*100:.2f}%" if p['deployed_roi'] is not None else '—'
            lines.append(f"| {year} | {LABELS[arm]} | {p['profit_yen']:+,.0f}円 | {roi} |")
    lines += ['', '## 感度・リスク','']
    for key,r in result['sensitivity'].items():
        lines.append(f"- {key}: 既知202608030111双方除外の利益差 {r['without_known_race_delta_yen']:+,.0f}円、最大正差レース{r['top_difference_race']}双方除外の差 {r['without_top_difference_yen']:+,.0f}円。診断のみ。")
    for arm,r in result['arms'].items():
        p=r['policy']
        lines.append(f"- {LABELS[arm]}: 最大DD {p['max_race_settlement_drawdown_yen']:,.0f}円、上位5払戻0の利益 {p['profit_without_top5_payouts_yen']:+,.0f}円。")
    lines += ['', 'paceの正規化前後で評価用winner NLLは不変。raw pace改善と③完全正規化超過を区別する。','',
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
    frames[research.CANDIDATE]=normalize(frames['pace'])
    dest=out/'comparison'
    dest.mkdir(exist_ok=False)
    scores,tables={},{}
    for arm,frame in frames.items():
        pd.testing.assert_frame_equal(frames['baseline'][models.PRED_KEYS],frame[models.PRED_KEYS],check_exact=True)
        scores[arm],tables[arm],bets=common.prior.score(frame)
        if arm!=research.CANDIDATE:
            folder=research.CONTROL_SOURCES[arm]
            previous=json.loads((folder/'summary.json').read_text())
            if models.canonical(scores[arm])!=models.canonical(previous['arms'][arm]):
                raise ValueError('Frozen control score differs')
            saved=pd.read_parquet(folder/f'{arm}_bets.parquet')
            pd.testing.assert_frame_equal(bets.reset_index(drop=True),saved.reset_index(drop=True),check_exact=True)
        frame.to_parquet(dest/f'{arm}_ensemble.parquet',index=False)
        tables[arm].to_parquet(dest/f'{arm}_races.parquet',index=False)
        bets.to_parquet(dest/f'{arm}_bets.parquet',index=False)
    quality=scores[research.CANDIDATE]['quality']
    if quality['races']!=24855 or quality['rows']!=343883:
        raise ValueError('Frozen population differs')
    if not np.isclose(quality['winner_nll'],scores['pace']['quality']['winner_nll'],atol=5e-15,rtol=0):
        raise ValueError('Pace winner NLL must be invariant')
    boot=proto['bootstrap']
    result=dict(arms=scores,
        paired=common.paired(tables,proto['contrasts'],boot['repetitions'],boot['seed'],boot['family_size']),
        sensitivity=common.sensitivity(tables,proto['contrasts']),
        mass_diagnostics={a:common.mass_diagnostic(f) for a,f in frames.items()},
        normalization=proto['normalization'],new_fits=0,reused_control_fits=360,proposal2='held',
        protocol_sha256=models.sha(out/'protocol.json'),
        ensemble_rule='mean15 raw unweighted pace91 probabilities FIRST, then divide by all-starter race sum before EV',
        settlement='closing_odds_proxy',can_adopt=False,untouched_holdout=False,eligible_for_verdict=False,
        limitations=proto['limitations'],wall_seconds=time.monotonic()-started)
    research.verify(out)
    research.write(dest/'summary.json',result)
    (dest/'report.md').write_text(markdown(result))
    print(json.dumps(dict(status='complete',out=str(dest),paired=result['paired']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
