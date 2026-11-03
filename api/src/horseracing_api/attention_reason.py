"""Feature 177: consistency of the 注目条件 reason (why a chip was given).

One pure function, shared by the builder (``attention.build_attention``) and the response model
(``schemas.AttentionHorse``'s validator): the builder turns a violation into the explicit
``unavailable`` state with a WARNING, the model rejects a directly constructed contradiction.
Rules V1-V10 are data-model §2. The function reads attributes only (pydantic models or any
duck-typed object) and imports nothing from ``schemas`` (schemas imports this module). No DB.
"""

from __future__ import annotations

from horseracing_eval import attention_rules as ar


def note_ev_gt(chip: ar.RuleDefinition, other: ar.RuleDefinition) -> float | None:
    """The threshold the 「…にも該当」 line may spell out for ``other``: only when ``other`` differs
    from the chip rule by its EV threshold alone (same ensemble flag, band and gap) AND is stricter
    (a looser sibling is implied by the chip itself — S4 under an S1 chip says nothing new)."""
    same_shape = (
        other.uses_ensemble == chip.uses_ensemble
        and other.odds_band == chip.odds_band
        and other.gap_days == chip.gap_days
    )
    return other.ev_gt if same_shape and other.ev_gt > chip.ev_gt else None


def _criteria_match(defn: ar.RuleDefinition, components) -> bool:
    expected_kinds = ["ens_expected_return"]
    if defn.odds_band is not None:
        expected_kinds.append("win_odds")
    if defn.gap_days is not None:
        expected_kinds.append("days_since_last")
    if [c.kind for c in components] != expected_kinds:
        return False
    for c in components:
        if c.kind == "ens_expected_return" and c.ev_gt != defn.ev_gt:
            return False
        if c.kind == "win_odds" and (c.lo, c.hi) != defn.odds_band:
            return False
        if c.kind == "days_since_last" and (c.lo, c.hi) != defn.gap_days:
            return False
    return True


def _snapshot_value(snapshot, kind: str):
    if kind == "ens_expected_return":
        return snapshot.ens_expected_return
    if kind == "win_odds":
        return snapshot.odds
    return None


def check_reason_consistency(
    *, chip_rule, chip_now, judged, current, applicable, reason
) -> list[str]:
    """Names of the violated rules (empty = consistent). V1 is structural and applies to every
    reason (None / unavailable / available); V2-V10 apply to an ``available`` reason only."""
    bad: list[str] = []
    if (reason is None) != (chip_rule is None):
        return ["V1"]
    if reason is None or getattr(reason, "status", None) != "available":
        return bad
    if reason.rule_id != chip_rule:
        bad.append("V2")
    if reason.rule_set_version != ar.RULE_SET_VERSION:
        bad.append("V3")
    try:
        defn = ar.definition(reason.rule_id)
    except ValueError:
        return bad + ["V4"]
    comps = list(reason.components)
    if not _criteria_match(defn, comps):
        bad.append("V4/V5")
    # V6: current None <=> unknown <=> every component current None; matches <=> all met
    cur_none = [c.current is None for c in comps]
    if current is None:
        if chip_now != "unknown" or not all(cur_none):
            bad.append("V6")
    else:
        if any(cur_none) or chip_now == "unknown":
            bad.append("V6")
        else:
            all_met = all(c.current.status == "met" for c in comps)
            if (chip_now == "matches") != all_met:
                bad.append("V6")
    # V7 / V8: values equal the snapshots; the gap's current value is the frozen judged value
    for c in comps:
        if c.kind in ("ens_expected_return", "win_odds"):
            if judged is None or c.judged.value != _snapshot_value(judged, c.kind):
                bad.append("V7")
                break
    for c in comps:
        if c.current is None:
            continue
        if c.kind in ("ens_expected_return", "win_odds"):
            if current is None or c.current.value != _snapshot_value(current, c.kind):
                bad.append("V8")
                break
        elif c.current.value != c.judged.value:
            bad.append("V8")
            break
    # V9 / V10: the other judged, non-control rules in rank order with the sibling threshold
    expected_also = [
        r for r in ar.RULE_IDS
        if r in applicable and r != chip_rule and not ar.definition(r).control
    ]
    if [a.rule_id for a in reason.also_applicable] != expected_also:
        bad.append("V9")
    else:
        for a in reason.also_applicable:
            if a.note_ev_gt != note_ev_gt(defn, ar.definition(a.rule_id)):
                bad.append("V10")
                break
    return bad
