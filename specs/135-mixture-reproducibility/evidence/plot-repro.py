"""Static plot of the sealed135 Step2 summary; no fitting or recalculation."""
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
source = ROOT/'artifacts/135-mixture-reproducibility/repro/summary.json'
assert hashlib.sha256(source.read_bytes()).hexdigest() == 'ab907ade63391296343486ad2ccc3c8b138ced9d960674d5822ff7b5ffd892c9'
summary = json.loads(source.read_text())
out = Path(__file__).parent
assert not (out/'repro-nll-ci.png').exists()

plt.rcParams.update({'font.family':'DejaVu Sans', 'font.size':11, 'axes.spines.top':False,
                     'axes.spines.right':False, 'axes.spines.left':False})
fig, ax = plt.subplots(figsize=(9.5, 5.1), layout='constrained')
fig.set_facecolor('#fbfcfe'); ax.set_facecolor('#fbfcfe')
for regime, offset, color, label in [('preweight', .12, '#235dc3', 'Before body weight (primary)'),
                                    ('full', -.12, '#8793a3', 'Full information')]:
    for index, cohort in enumerate(('2024','2026','pooled')):
        data = summary['reports'][regime][cohort]['seed_mean_loss_difference']['winner_nll']
        p = data['point']*1000; lo, hi = np.array(data['ci'])*1000
        ax.errorbar(p, 2-index+offset, xerr=[[p-lo],[hi-p]], fmt='o', color=color,
                    capsize=4, markersize=7, linewidth=2.1,
                    label=label if index == 0 else None)
ax.axvline(0, color='#29303d', linewidth=1.1, linestyle='--')
ax.set_yticks([2,1,0], ['2024', '2026 through Aug 23', 'Both years'])
ax.tick_params(axis='y', length=0, pad=13)
ax.set_ylim(-.6,2.65); ax.set_xlim(-1.4,.8)
ax.set_xlabel('Candidate minus baseline: winner NLL (×10⁻³; lower is better)', labelpad=12)
ax.grid(axis='x', color='#e5e9f0', linewidth=.7)
ax.set_axisbelow(True)
ax.legend(loc='upper right', frameon=False, fontsize=10)
fig.suptitle('Adding a 1/7 colsample 0.7 candidate', fontsize=17, fontweight='bold', x=.04, ha='left')
fig.supxlabel('Mean of per-race losses across seeds 42 / 43 / 44. Bars: 98.75% day-cluster CI.\n'
              'Previously examined years; conditional uncertainty. No production adoption.',
              fontsize=9, color='#536071', ha='left', x=.04)
fig.savefig(out/'repro-nll-ci.png', dpi=180, facecolor=fig.get_facecolor())
fig.savefig(out/'repro-nll-ci.svg', facecolor=fig.get_facecolor())
print(out/'repro-nll-ci.png')
