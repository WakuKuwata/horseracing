"""Outcome-blind definitions for a new, fixed 480-rule exploratory search.

This module reads no race rows, predictions, returns, or previous result files.
It returns only AND conditions; every rule buys every matching horse for a flat
100 yen win stake and requires at least 14 days since the previous start.
The names and categorical values of existing fields were checked only against
existing definition JSONs. New feature semantics were agreed with their builder.

`market_er` is fresh-model predicted payout/stake ratio, available from
2010 onward. The 90 model rules test interactions with specified contexts; they
are never standalone ER-threshold/odds-cap strategies.
"""
from __future__ import annotations

from collections import Counter
import json

STAKE_YEN = 100
SELECTION = 'all'
BET_TYPE = 'win'
MAX_PATTERNS = 800
MAX_MODEL_PATTERNS = 120

FAMILY_HYPOTHESES = {
    'routine_form': {
        'label': '通常間隔と前走の状態',
        'hypothesis': '前走の着順だけでは捉えにくい状態の継続・立て直しが、通常の出走間隔により異なる。',
        'definition': '14–28/29–56/57–112/113–224日の4区分 × 5つの事前固定した前走状態 × 3つの単勝オッズ帯。',
        'count': 60,
    },
    'rest_cycle': {
        'label': '長期休養後の2走目・3走目',
        'hypothesis': '84日以上の休養後に14–56日の間隔で使われる馬は、直近1走の凡走や適性実績との組合せで評価が変わる。',
        'definition': 'rest_second/rest_third × 8つの文脈 × 3つのオッズ帯。両フラグとも今走間隔14–56日、thirdは前走間隔も14–56日。',
        'count': 48,
    },
    'surface_return': {
        'label': '過去実績のある芝・ダートへの変更',
        'hypothesis': '直近の異なる馬場種別での凡走が重視され、今回戻る芝またはダートの過去実績が十分評価されない場合がある。',
        'definition': '今回芝/ダート × 当該種別の勝利実績/未勝利の複勝実績 × 3つの前走状態 × 3つのオッズ帯。return_to_surface単独では復帰を保証しないので対象別startsを必須とする。',
        'count': 36,
    },
    'course_distance': {
        'label': '競馬場・距離帯の実績と条件変更',
        'hypothesis': '同じ競馬場×芝ダ、または同じ芝ダ×距離帯での実績と今回の条件変更に相互作用がある。',
        'definition': '競馬場復帰・距離短縮/延長など8文脈 × 14–56/57–112日の2区分 × 3つのオッズ帯。距離帯実績だけの条件は「距離帯復帰」とは呼ばない。',
        'count': 48,
    },
    'repriced_favourite': {
        'label': '前走で支持された馬のオッズ上昇',
        'hypothesis': '前走で人気または低オッズだった馬の不振後に、今回のオッズが上がる局面で市場の反応が過大になる場合がある。',
        'definition': '前走の支持・成績6文脈 × 現保存単勝オッズ/前走単勝オッズが1.5倍以上または2倍以上 × 14–56/57–112日 × 3オッズ帯。',
        'caveat': '支持低下はオッズ比という単位で定義する。人気順位差ではなく、頭数差や控除率で調整した市場勝率比でもない。',
        'count': 72,
    },
    'pace_context': {
        'label': '前走脚質から数えた先行馬の密度',
        'hypothesis': '前走で逃げ・先行だった相手の少なさ、または多さと、自馬の前走脚質・距離変更に相互作用がある。',
        'definition': '6つの過去脚質による展開文脈 × 3つの頭数/脚質既知数区分 × 3オッズ帯。今走間隔14–84日。',
        'caveat': '当該レースの実走脚質は使わない。先行は逃げ/先行のみ、shareの分母は全出走頭数。10–12頭では既知>=10、13–16頭では>=13、17–18頭では>=15を要求する。',
        'count': 54,
    },
    'age_career_rest': {
        'label': '年齢・観測キャリアと休養',
        'hypothesis': '同じ休養期間でも、若く出走数が少ない馬と高齢で出走数が多い馬では状態回復や前走評価の残り方が異なる。',
        'definition': '6つの年齢/キャリア組合せ × 57–112/113–224日の2区分 × 前走3着以内/6着以下 × 3オッズ帯。',
        'caveat': 'career_startsは2007年以降に観測した出走数。2007年以前の通算実績を復元しない。',
        'count': 72,
    },
    'market_er_context': {
        'label': '今回再学習モデルの期待収益と具体的文脈',
        'hypothesis': '市場と馬履歴を使う新規再学習モデルの正の期待収益が、休養・実績条件への変更・先行密度・価格再評価の文脈で安定するかを調べる。',
        'definition': 'market_er>1.0/1.1/1.2倍 × 10文脈 × 3オッズ帯、2010年以降。モデル入力・学習履歴も2007年以降のfresh baselineのみを使う。',
        'count': 90,
    },
}

# Disjoint current-price bands, fixed before evaluation. The upper bound is open.
ODDS_BANDS = (
    ('o3_8', '3倍以上8倍未満', 3.0, 8.0),
    ('o8_20', '8倍以上20倍未満', 8.0, 20.0),
    ('o20_40', '20倍以上40倍未満', 20.0, 40.0),
)
FRONT = ['逃げ', '先行']
BACK = ['中団', '後方', '差し', '追込']


def c(field: str, op: str, value) -> dict:
    return {'field': field, 'op': op, 'value': value}


def between(field: str, low, high) -> list[dict]:
    return [c(field, 'ge', low), c(field, 'le', high)]


def signature(conditions: list[dict]) -> str:
    return json.dumps(sorted(conditions, key=lambda x: (x['field'], x['op'], json.dumps(x['value'], ensure_ascii=False))), ensure_ascii=False, sort_keys=True)


def patterns() -> list[dict]:
    """Return fresh dict/list objects in deterministic family/definition order."""
    out: list[dict] = []

    def add(family: str, key: str, label: str, conditions: list[dict]) -> None:
        # Retain an explicit >=14 guard even when another lower bound is tighter.
        combined = [c('days_since_last', 'ge', 14)] + conditions
        unique = []
        seen = set()
        for condition in combined:
            token = json.dumps(condition, ensure_ascii=False, sort_keys=True)
            if token not in seen:
                unique.append(json.loads(token))
                seen.add(token)
        out.append({'id': f'new26.{family}.{key}', 'family': family,
                    'label': label, 'conditions': unique})

    def with_prices(family: str, key: str, label: str, conditions: list[dict]) -> None:
        for oid, olab, low, high in ODDS_BANDS:
            add(family, f'{key}.{oid}', f'{label} × 単勝{olab}',
                conditions + [c('odds', 'ge', low), c('odds', 'lt', high)])

    # 1. Four broad gap bands rather than a fine threshold search.
    form_states = (
        ('winner_pop3p', '前走勝ち・前走3番人気以下', [c('prev_finish', 'eq', 1), c('prev_popularity', 'ge', 3)]),
        ('close_2_3', '前走2–3着・着差0–0.5秒', between('prev_finish', 2, 3) + between('prev_margin_sec', 0, 0.5)),
        ('fav_flop', '前走3番人気以内・6着以下', [c('prev_popularity', 'le', 3), c('prev_finish', 'ge', 6)]),
        ('front_flop', '前走逃げ/先行・6着以下', [c('prev_running_style', 'in', FRONT), c('prev_finish', 'ge', 6)]),
        ('recovered', '前2–3走6着以下から前走3着以内', [c('prev3_finish', 'ge', 6), c('prev2_finish', 'ge', 6), c('prev_finish', 'le', 3)]),
    )
    for low, high in ((14, 28), (29, 56), (57, 112), (113, 224)):
        for key, label, cond in form_states:
            with_prices('routine_form', f'g{low}_{high}.{key}', f'間隔{low}–{high}日 × {label}', between('days_since_last', low, high) + cond)

    # 2. Features encode the >=84-day break, then successive 14..56-day gaps.
    rest_contexts = (
        ('course_placer', '今回競馬場×芝ダで3走以上・複勝率40%以上', [c('course_starts', 'ge', 3), c('course_top3_rate', 'ge', 0.4)]),
        ('distance_placer', '今回芝ダ×距離帯で4走以上・複勝率40%以上', [c('distband_starts', 'ge', 4), c('distband_top3_rate', 'ge', 0.4)]),
        ('last_flop', '前走6着以下', [c('prev_finish', 'ge', 6)]),
        ('last_close', '前走2–4着・着差0–0.6秒', between('prev_finish', 2, 4) + between('prev_margin_sec', 0, 0.6)),
        ('young', '3–4歳・観測キャリア3–12走', between('age', 3, 4) + between('career_starts', 3, 12)),
        ('shorten', '距離200m以上短縮', [c('dist_change', 'le', -200)]),
        ('lengthen', '距離200m以上延長', [c('dist_change', 'ge', 200)]),
        ('surface_return', '実績ある芝ダへ変更', [c('return_to_surface', 'eq', True), c('surface_starts', 'ge', 3), c('surface_wins', 'ge', 1)]),
    )
    for stage, label in (('rest_second', '長期休養後2走目'), ('rest_third', '長期休養後3走目')):
        for key, context, cond in rest_contexts:
            with_prices('rest_cycle', f'{stage}.{key}', f'{label} × {context}', [c(stage, 'eq', True), c('days_since_last', 'le', 56)] + cond)

    # 3. Returning to a surface needs positive prior exposure, not a change flag alone.
    surface_evidence = (
        ('winner', '当該芝ダ3走以上・1勝以上', [c('surface_starts', 'ge', 3), c('surface_wins', 'ge', 1)]),
        ('placer_no_win', '当該芝ダ5走以上・未勝利・複勝率40%以上', [c('surface_starts', 'ge', 5), c('surface_wins', 'eq', 0), c('surface_top3_rate', 'ge', 0.4)]),
    )
    surface_previous = (
        ('flop6p', '前走6着以下', [c('prev_finish', 'ge', 6)]),
        ('fav4p', '前走3番人気以内・4着以下', [c('prev_popularity', 'le', 3), c('prev_finish', 'ge', 4)]),
        ('near4_8', '前走4–8着・着差0–1秒', between('prev_finish', 4, 8) + between('prev_margin_sec', 0, 1.0)),
    )
    for sid, surface in (('turf', '芝'), ('dirt', 'ダ')):
        for evid, evlab, evidence in surface_evidence:
            for prev, prevlab, previous in surface_previous:
                with_prices('surface_return', f'{sid}.{evid}.{prev}', f'{surface}への実績復帰 × {evlab} × {prevlab}',
                            [c('return_to_surface', 'eq', True), c('track_type', 'eq', surface), c('days_since_last', 'le', 112)] + evidence + previous)

    # 4. Venue change and distance change each require the relevant past support.
    course_contexts = (
        ('course_winner', '勝利経験のある競馬場×芝ダへ復帰', [c('return_to_course', 'eq', True), c('course_starts', 'ge', 3), c('course_wins', 'ge', 1)]),
        ('course_placer', '未勝利だが複勝実績ある競馬場×芝ダへ復帰', [c('return_to_course', 'eq', True), c('course_starts', 'ge', 5), c('course_wins', 'eq', 0), c('course_top3_rate', 'ge', 0.4)]),
        ('course_shorten', '複勝実績ある競馬場×芝ダへ復帰・200m以上短縮', [c('return_to_course', 'eq', True), c('course_starts', 'ge', 3), c('course_top3_rate', 'ge', 0.4), c('dist_change', 'le', -200)]),
        ('course_lengthen', '複勝実績ある競馬場×芝ダへ復帰・200m以上延長', [c('return_to_course', 'eq', True), c('course_starts', 'ge', 3), c('course_top3_rate', 'ge', 0.4), c('dist_change', 'ge', 200)]),
        ('band_shorten', '勝利経験のある今回距離帯へ200m以上短縮', [c('distband_starts', 'ge', 4), c('distband_wins', 'ge', 1), c('dist_change', 'le', -200)]),
        ('band_lengthen', '勝利経験のある今回距離帯へ200m以上延長', [c('distband_starts', 'ge', 4), c('distband_wins', 'ge', 1), c('dist_change', 'ge', 200)]),
        ('band_flop', '距離帯6走以上・複勝率50%以上だが前走6着以下', [c('distband_starts', 'ge', 6), c('distband_top3_rate', 'ge', 0.5), c('prev_finish', 'ge', 6)]),
        ('course_and_band', '競馬場と距離帯の両方で複勝実績を持ち競馬場復帰', [c('return_to_course', 'eq', True), c('course_starts', 'ge', 3), c('course_top3_rate', 'ge', 0.4), c('distband_starts', 'ge', 5), c('distband_top3_rate', 'ge', 0.4)]),
    )
    for low, high in ((14, 56), (57, 112)):
        for key, label, cond in course_contexts:
            with_prices('course_distance', f'g{low}_{high}.{key}', f'間隔{low}–{high}日 × {label}', between('days_since_last', low, high) + cond)

    # 5. The price ratio is current saved odds / previous saved odds, with missing
    # or nonpositive operands treated as unavailable by the feature builder.
    repriced_contexts = (
        ('fav1_flop6p', '前走1番人気・6着以下', [c('prev_popularity', 'eq', 1), c('prev_finish', 'ge', 6)]),
        ('fav3_finish4_5', '前走3番人気以内・4–5着', [c('prev_popularity', 'le', 3)] + between('prev_finish', 4, 5)),
        ('won_then_fav_flop', '前々走勝利後、前走3番人気以内で6着以下', [c('prev2_finish', 'eq', 1), c('prev_popularity', 'le', 3), c('prev_finish', 'ge', 6)]),
        ('fav_two_flops', '前2走6着以下で前走は3番人気以内', [c('prev2_finish', 'ge', 6), c('prev_popularity', 'le', 3), c('prev_finish', 'ge', 6)]),
        ('shortprice_flop8p', '前走4倍未満・8着以下', [c('prev_odds', 'gt', 0), c('prev_odds', 'lt', 4), c('prev_finish', 'ge', 8)]),
        ('fav_close', '前走3番人気以内・2–3着・着差0–0.3秒', [c('prev_popularity', 'le', 3)] + between('prev_finish', 2, 3) + between('prev_margin_sec', 0, 0.3)),
    )
    for low, high in ((14, 56), (57, 112)):
        for ratio_id, ratio in (('r15', 1.5), ('r20', 2.0)):
            for key, label, cond in repriced_contexts:
                with_prices('repriced_favourite', f'g{low}_{high}.{ratio_id}.{key}', f'間隔{low}–{high}日 × オッズ比{ratio}倍以上 × {label}',
                            between('days_since_last', low, high) + [c('odds_ratio_prev', 'ge', ratio)] + cond)

    # 6. Coarse race-size strata keep the minimum known-style fraction >=81.25%.
    pace_contexts = (
        ('only_front', '前走逃げ/先行・他の前走先行馬ゼロ', [c('prev_running_style', 'in', FRONT), c('pace_front_others', 'eq', 0)]),
        ('one_front_rival', '前走逃げ/先行・他の前走先行馬1頭', [c('prev_running_style', 'in', FRONT), c('pace_front_others', 'eq', 1)]),
        ('leader_few', '前走逃げ・他の前走先行馬1頭以下', [c('prev_running_style', 'eq', '逃げ'), c('pace_front_others', 'le', 1)]),
        ('crowded_back', '前走中団/後方/差し/追込・先行4頭以上かつ頭数の3分の1以上', [c('prev_running_style', 'in', BACK), c('pace_front_count', 'ge', 4), c('pace_front_share', 'ge', 1 / 3)]),
        ('front_shorten', '前走逃げ/先行・先行相手1頭以下・200m以上短縮', [c('prev_running_style', 'in', FRONT), c('pace_front_others', 'le', 1), c('dist_change', 'le', -200)]),
        ('front_lengthen', '前走逃げ/先行・先行相手1頭以下・200m以上延長', [c('prev_running_style', 'in', FRONT), c('pace_front_others', 'le', 1), c('dist_change', 'ge', 200)]),
    )
    for size_low, size_high, known in ((10, 12, 10), (13, 16, 13), (17, 18, 15)):
        for key, label, cond in pace_contexts:
            with_prices('pace_context', f'n{size_low}_{size_high}.{key}', f'{size_low}–{size_high}頭・過去脚質既知{known}頭以上 × {label}',
                        [c('days_since_last', 'le', 84), c('pace_known_count', 'ge', known)] + between('field_size', size_low, size_high) + cond)

    # 7. Six meaningful age/exposure pairs instead of the full age x career grid.
    age_profiles = (
        ('a3_4_c3_8', '3–4歳・観測3–8走', between('age', 3, 4) + between('career_starts', 3, 8)),
        ('a3_4_c9_20', '3–4歳・観測9–20走', between('age', 3, 4) + between('career_starts', 9, 20)),
        ('a5_6_c5_15', '5–6歳・観測5–15走', between('age', 5, 6) + between('career_starts', 5, 15)),
        ('a5_6_c16p', '5–6歳・観測16走以上', between('age', 5, 6) + [c('career_starts', 'ge', 16)]),
        ('a7p_c10_25', '7歳以上・観測10–25走', [c('age', 'ge', 7)] + between('career_starts', 10, 25)),
        ('a7p_c26p', '7歳以上・観測26走以上', [c('age', 'ge', 7), c('career_starts', 'ge', 26)]),
    )
    for low, high in ((57, 112), (113, 224)):
        for aid, alab, age_cond in age_profiles:
            for state, label, cond in (('good', '前走3着以内', [c('prev_finish', 'le', 3)]), ('poor', '前走6着以下', [c('prev_finish', 'ge', 6)])):
                with_prices('age_career_rest', f'g{low}_{high}.{aid}.{state}', f'間隔{low}–{high}日 × {alab} × {label}', between('days_since_last', low, high) + age_cond + cond)

    # 8. Only fresh ER, never the legacy model's p/ev/rank fields.
    model_contexts = (
        ('second_flop', '長期休養後2走目・前走6着以下', [c('rest_second', 'eq', True), c('prev_finish', 'ge', 6)]),
        ('third', '長期休養後3走目', [c('rest_third', 'eq', True)]),
        ('course_winner', '勝利実績ある競馬場×芝ダへ復帰', [c('return_to_course', 'eq', True), c('course_starts', 'ge', 3), c('course_wins', 'ge', 1)]),
        ('course_placer', '未勝利だが複勝実績ある競馬場×芝ダへ復帰', [c('return_to_course', 'eq', True), c('course_starts', 'ge', 5), c('course_wins', 'eq', 0), c('course_top3_rate', 'ge', 0.4)]),
        ('surface_winner', '勝利実績ある芝ダへ変更', [c('return_to_surface', 'eq', True), c('surface_starts', 'ge', 3), c('surface_wins', 'ge', 1)]),
        ('band_shorten', '勝利実績ある今回距離帯へ200m以上短縮', [c('distband_starts', 'ge', 4), c('distband_wins', 'ge', 1), c('dist_change', 'le', -200)]),
        ('band_lengthen', '勝利実績ある今回距離帯へ200m以上延長', [c('distband_starts', 'ge', 4), c('distband_wins', 'ge', 1), c('dist_change', 'ge', 200)]),
        ('only_front', '10–12頭・脚質既知10頭以上・前走逃げ/先行は自馬だけ', between('field_size', 10, 12) + [c('pace_known_count', 'ge', 10), c('prev_running_style', 'in', FRONT), c('pace_front_others', 'eq', 0)]),
        ('crowded_back', '13–16頭・脚質既知13頭以上・多数先行で前走差し側', between('field_size', 13, 16) + [c('pace_known_count', 'ge', 13), c('prev_running_style', 'in', BACK), c('pace_front_count', 'ge', 4), c('pace_front_share', 'ge', 1 / 3)]),
        ('fav_repriced', '前走3番人気以内で6着以下・今回オッズ1.5倍以上', [c('prev_popularity', 'le', 3), c('prev_finish', 'ge', 6), c('odds_ratio_prev', 'ge', 1.5)]),
    )
    for threshold in (1.0, 1.1, 1.2):
        for key, label, cond in model_contexts:
            with_prices('market_er_context', f'er{int(round(threshold * 100))}.{key}', f'新規モデル予測回収倍率>{threshold} × {label}',
                        [c('year', 'ge', 2010), c('market_er', 'gt', threshold), c('days_since_last', 'le', 112)] + cond)

    validate(out)
    return out


def validate(items: list[dict]) -> None:
    """Definition-only invariants; never executes conditions on race data."""
    allowed_ops = {'eq', 'ne', 'ge', 'gt', 'le', 'lt', 'in'}
    forbidden = {'won', 'finish_order', 'n_winners', 'place_hit', 'dead_heat', 'race_ok', 'in_bundle',
                 'finish_time', 'margin_sec', 'last_3f', 'running_style', 'last3f_rank'}
    legacy_model = {'p', 'p_top2', 'p_top3', 'p_rank', 'ev', 'p_over_q', 'p_gap12', 'model_fav_is_market_fav'}
    assert len(items) == 480 <= MAX_PATTERNS
    assert len({p['id'] for p in items}) == len(items), 'Duplicate ids'
    assert len({signature(p['conditions']) for p in items}) == len(items), 'Duplicate exact condition sets'
    counts = Counter(p['family'] for p in items)
    assert counts == {f: v['count'] for f, v in FAMILY_HYPOTHESES.items()}, counts
    model_count = 0
    for p in items:
        assert set(p) == {'id', 'family', 'label', 'conditions'}
        assert c('days_since_last', 'ge', 14) in p['conditions'], p['id']
        fields = {x['field'] for x in p['conditions']}
        assert not (fields & forbidden), p['id']
        assert not (fields & legacy_model), p['id']
        for condition in p['conditions']:
            assert set(condition) == {'field', 'op', 'value'} and condition['op'] in allowed_ops
            assert condition['value'] is not None
            if condition['op'] == 'in':
                assert isinstance(condition['value'], list) and condition['value']
        if 'market_er' in fields:
            model_count += 1
            assert p['family'] == 'market_er_context'
            assert c('year', 'ge', 2010) in p['conditions']
            assert fields - {'days_since_last', 'year', 'market_er', 'odds'}, p['id']
    assert model_count == 90 <= MAX_MODEL_PATTERNS


if __name__ == '__main__':
    ps = patterns()
    print(json.dumps({'n_patterns': len(ps), 'families': dict(Counter(p['family'] for p in ps)),
                      'model_patterns': sum(any(c['field'] == 'market_er' for c in p['conditions']) for p in ps),
                      'stake_yen': STAKE_YEN, 'selection': SELECTION, 'bet_type': BET_TYPE,
                      'outcome_data_read': False}, ensure_ascii=False, indent=1))
