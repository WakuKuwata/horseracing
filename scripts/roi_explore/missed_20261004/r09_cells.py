"""R09_new_info — 市場連動モデル(ens15)の入力に無い情報源のセルを as-of で作る(共変量のみ・結果列は使わない)。

各セルは「対象レースの発走前に分かる情報」だけで決まる。過去の結果は strictly-before(対象レース日より前)・
同日除外・自馬除外で集計する。対象行の won / finish_order は一切読まない。

出力: build_cells(rows, preds) -> (frame, meta)
  frame: 入力 rows と同じ行順・同じ index に、セル指示列(bool)と補助列を足したもの。
  meta : 分位点などの凍結値(発見期 2010–2015 の共変量分布から算出)。

使うのは artifacts/market_ev/rows_2007.parquet(平地・出走・2007〜)と DB の horses(読み取りのみ)。
"""
from __future__ import annotations

import unicodedata

import numpy as np
import pandas as pd

EAST = {"03", "04", "05", "06"}  # 福島・新潟・東京・中山
WEST = {"07", "08", "09", "10"}  # 中京・京都・阪神・小倉
# 札幌 01・函館 02 は東西どちらにも数えない
BIG = 1_000_000  # 日数(epoch 日)より大きい
DISC = (2010, 2015)

CELL_IDS = (
    "X01a_jockey_chose", "X01b_jockey_abandoned",
    "X02a_jockey_upgrade", "X02b_jockey_downgrade",
    "X04a_ritto_to_east", "X04b_miho_to_west",
    "X06a_prev_high_level", "X06b_lowlevel_prev_top3",
    "X08a_owner_low_ae", "X08b_new_sire_g1",
    "X09_first_dirt_sire_dirt_top",
    "MS10_g1day_other_20_40",
    "MCU11_prev_s3_lost",
)


def _norm(s) -> str | None:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return None
    t = unicodedata.normalize("NFKC", str(s)).strip()
    return t or None


def _days(dates: pd.Series) -> np.ndarray:
    return pd.to_datetime(dates).to_numpy().astype("datetime64[D]").astype(np.int64)


class AsOf:
    """Strictly-before cumulative sums per key (daily aggregated; same day excluded).

    hist_key: int codes, hist_day: int days, vals: dict name -> float array (one per history row).
    query(key, day, window=None) -> dict name -> sums over history rows with the same key and
    hist_day < day (and hist_day >= day - window when window is given)."""

    def __init__(self, hist_key: np.ndarray, hist_day: np.ndarray, vals: dict[str, np.ndarray]):
        comp = hist_key.astype(np.int64) * BIG + hist_day.astype(np.int64)
        order = np.argsort(comp, kind="stable")
        self.comp = comp[order]
        self.cum = {k: np.r_[0.0, np.cumsum(np.asarray(v, dtype=float)[order])] for k, v in vals.items()}

    def query(self, key: np.ndarray, day: np.ndarray, window: int | None = None) -> dict[str, np.ndarray]:
        key = key.astype(np.int64)
        hi = np.searchsorted(self.comp, key * BIG + day.astype(np.int64), side="left")
        if window is None:
            lo = np.searchsorted(self.comp, key * BIG, side="left")
        else:
            lo = np.searchsorted(self.comp, key * BIG + (day.astype(np.int64) - window), side="left")
        return {k: c[hi] - c[lo] for k, c in self.cum.items()}


def _quantile_disc(values: np.ndarray, years: np.ndarray, eligible: np.ndarray, q: float) -> float:
    m = eligible & (years >= DISC[0]) & (years <= DISC[1]) & np.isfinite(values)
    return float(np.quantile(values[m], q))


def build_cells(rows: pd.DataFrame, horses: pd.DataFrame, prev_ens_ev: np.ndarray | None = None) -> tuple[pd.DataFrame, dict]:
    """rows: rows_2007.parquet (all flat started rows 2007+), any order. horses: horse_id, horse_name,
    birth_year, sire_name, owner_name. prev_ens_ev: ens15 EV aligned with rows (NaN where absent)."""
    d = rows.copy()
    d["race_id"] = d["race_id"].astype(str)
    d["horse_id"] = d["horse_id"].astype(str)
    d["day"] = _days(d["race_date"])
    d["_won_hist"] = d["won"].astype(float)  # 過去行としての勝ち(対象行の自身の特徴には使わない)
    d["_q"] = d["q"].astype(float).fillna(0.0)
    if prev_ens_ev is not None:
        d["_ens_ev"] = prev_ens_ev
    h = horses.copy()
    h["horse_id"] = h["horse_id"].astype(str)
    h["sire_n"] = h["sire_name"].map(_norm)
    h["owner_n"] = h["owner_name"].map(_norm)
    h["name_n"] = h["horse_name"].map(_norm)
    d = d.merge(h[["horse_id", "sire_n", "owner_n", "birth_year"]], on="horse_id", how="left", validate="many_to_one")
    meta: dict = {}

    # ---- horse history order (one start per day per horse)
    d = d.sort_values(["horse_id", "day", "race_id"]).reset_index(drop=False).rename(columns={"index": "_orig"})
    gh = d.groupby("horse_id", sort=False)
    d["prev_race_id"] = gh["race_id"].shift(1)
    d["prev_day"] = gh["day"].shift(1)
    d["prev_jockey_id"] = gh["jockey_id"].shift(1)
    d["prev_won_hist"] = gh["_won_hist"].shift(1)
    d["prev_finish_order_hist"] = gh["finish_order"].shift(1)  # 過去走の着順(as-of)
    if prev_ens_ev is not None:
        d["prev_ens_ev"] = gh["_ens_ev"].shift(1)
    # dirt history (strictly before) for X09
    is_dirt = (d["track_type"] == "ダ").astype(float)
    d["prior_dirt_starts"] = is_dirt.groupby(d["horse_id"]).cumsum() - is_dirt
    d["prior_starts"] = gh.cumcount().astype(float)
    d = d.sort_values("_orig").reset_index(drop=True)
    assert (d["_orig"].to_numpy() == np.arange(len(d))).all()

    years = d["year"].to_numpy()
    day = d["day"].to_numpy()

    # ---- X01: 乗り移り先 / 乗り捨てられ(同じレースの騎手の選択)
    pj = d["prev_jockey_id"]
    cur_pairs = set(zip(d["race_id"], d["jockey_id"].astype(str)))
    # 乗り捨てられ: 前走騎手が今回同じレースで別の馬に乗っている
    abandoned = pj.notna() & (pj.astype(str) != d["jockey_id"].astype(str)) & np.array(
        [(r, str(j)) in cur_pairs for r, j in zip(d["race_id"], pj.astype(str))])
    d["X01b_jockey_abandoned"] = abandoned.to_numpy()
    # 乗り移り先: 今回の騎手が、同じレースの別の馬(=乗り捨てられ馬)の前走騎手
    ab = d.loc[abandoned, ["race_id", "prev_jockey_id"]].astype(str)
    chosen_pairs = set(zip(ab["race_id"], ab["prev_jockey_id"]))
    d["X01a_jockey_chose"] = np.array(
        [(r, str(j)) in chosen_pairs for r, j in zip(d["race_id"], d["jockey_id"].astype(str))])

    # ---- X02: 鞍上強化 / 弱化(1 騎乗あたりの市場超過 = (勝ち − Σq) / 騎乗数・365 日・strictly-before)
    jk_codes, jk_uni = pd.factorize(pd.concat([d["jockey_id"].astype(str), pj.astype(str)]), sort=False)
    jcur = jk_codes[: len(d)]
    jprev = jk_codes[len(d):]
    jasof = AsOf(jcur, day, {"n": np.ones(len(d)), "w": d["_won_hist"].to_numpy(), "q": d["_q"].to_numpy()})
    cur = jasof.query(jcur, day, window=365)
    prv = jasof.query(jprev, day, window=365)
    rate_cur = np.where(cur["n"] >= 50, (cur["w"] - cur["q"]) / np.maximum(cur["n"], 1), np.nan)
    rate_prv = np.where((prv["n"] >= 50) & pj.notna().to_numpy(), (prv["w"] - prv["q"]) / np.maximum(prv["n"], 1), np.nan)
    changed = pj.notna().to_numpy() & (pj.astype(str).to_numpy() != d["jockey_id"].astype(str).to_numpy())
    delta = np.where(changed, rate_cur - rate_prv, np.nan)
    d["jockey_delta"] = delta
    elig = np.isfinite(delta)
    p90 = _quantile_disc(delta, years, elig, 0.90)
    p10 = _quantile_disc(delta, years, elig, 0.10)
    meta["X02_delta_p10"], meta["X02_delta_p90"] = p10, p90
    d["X02a_jockey_upgrade"] = elig & (delta >= p90)
    d["X02b_jockey_downgrade"] = elig & (delta <= p10)

    # ---- X04: 東西(調教師の直近 365 日の東西出走比率で代理)と遠征
    tr_codes, _ = pd.factorize(d["trainer_id"].astype(str))
    venue = d["venue_code"].astype(str).str.zfill(2)
    east = venue.isin(EAST).to_numpy().astype(float)
    west = venue.isin(WEST).to_numpy().astype(float)
    tasof = AsOf(tr_codes, day, {"e": east, "w": west})
    t = tasof.query(tr_codes, day, window=365)
    tot = t["e"] + t["w"]
    region_w = (tot >= 20) & (t["w"] >= 0.75 * tot)
    region_e = (tot >= 20) & (t["e"] >= 0.75 * tot)
    d["trainer_region"] = np.where(region_w, "W", np.where(region_e, "E", None))
    d["X04a_ritto_to_east"] = region_w & (east == 1.0)
    d["X04b_miho_to_west"] = region_e & (west == 1.0)

    # ---- X06: 前走の「レースレベル」= 前走の同走馬(自馬除外)が前走後〜今回より前に挙げた勝ち / Σq
    hcodes, _ = pd.factorize(d["horse_id"])
    d["_hcode"] = hcodes
    hasof_comp = hcodes.astype(np.int64) * BIG + day
    order = np.argsort(hasof_comp, kind="stable")
    hcomp = hasof_comp[order]
    cw = np.r_[0.0, np.cumsum(d["_won_hist"].to_numpy()[order])]
    cq = np.r_[0.0, np.cumsum(d["_q"].to_numpy()[order])]
    cn = np.r_[0.0, np.cumsum(np.ones(len(d)))]
    # runner table sorted by race
    rorder = np.argsort(d["race_id"].to_numpy(), kind="stable")
    r_race = d["race_id"].to_numpy()[rorder]
    r_h = hcodes[rorder]
    has_prev = d["prev_race_id"].notna().to_numpy()
    qi = np.flatnonzero(has_prev)
    pr = d["prev_race_id"].to_numpy()[qi].astype(str)
    a = np.searchsorted(r_race, pr, side="left")
    b = np.searchsorted(r_race, pr, side="right")
    cnt = b - a
    rep_i = np.repeat(qi, cnt)
    starts = np.repeat(a - np.r_[0, np.cumsum(cnt)[:-1]], cnt) + np.arange(cnt.sum())
    runner = r_h[starts]
    keep = runner != hcodes[rep_i]  # 自馬除外
    rep_i, runner = rep_i[keep], runner[keep]
    dq = day[rep_i]
    dp = d["prev_day"].to_numpy()[rep_i].astype(np.int64)
    hi = np.searchsorted(hcomp, runner.astype(np.int64) * BIG + dq, side="left")  # strictly before 今回(同日除外)
    lo = np.searchsorted(hcomp, runner.astype(np.int64) * BIG + dp, side="right")  # 前走当日を含めて除く
    later_w = np.bincount(rep_i, weights=cw[hi] - cw[lo], minlength=len(d))
    later_q = np.bincount(rep_i, weights=cq[hi] - cq[lo], minlength=len(d))
    later_n = np.bincount(rep_i, weights=cn[hi] - cn[lo], minlength=len(d))
    level = np.where(has_prev & (later_n >= 5), (later_w + 1.0) / (later_q + 1.0), np.nan)
    d["prev_level"] = level
    d["prev_level_later_n"] = np.where(has_prev, later_n, np.nan)
    el = np.isfinite(level)
    l80 = _quantile_disc(level, years, el, 0.80)
    l20 = _quantile_disc(level, years, el, 0.20)
    meta["X06_level_p20"], meta["X06_level_p80"] = l20, l80
    pf = d["prev_finish_order_hist"].to_numpy(dtype=float)
    d["X06a_prev_high_level"] = el & (level >= l80)
    d["X06b_lowlevel_prev_top3"] = el & (level <= l20) & (pf <= 3)

    # ---- X08a: 馬主の市場超過(自馬除外・strictly-before・通算)。owner_name は現在の馬主(後知恵の恐れ)
    ow_codes, _ = pd.factorize(d["owner_n"].fillna("__NA__"))
    oasof = AsOf(ow_codes, day, {"w": d["_won_hist"].to_numpy(), "q": d["_q"].to_numpy()})
    o = oasof.query(ow_codes, day)
    selfh = AsOf(hcodes, day, {"w": d["_won_hist"].to_numpy(), "q": d["_q"].to_numpy()}).query(hcodes, day)
    ow_w = o["w"] - selfh["w"]
    ow_q = o["q"] - selfh["q"]
    ow_ae = np.where(d["owner_n"].notna().to_numpy() & (ow_q >= 5.0), (ow_w + 1.0) / (ow_q + 1.0), np.nan)
    d["owner_ae"] = ow_ae
    eo = np.isfinite(ow_ae)
    o20 = _quantile_disc(ow_ae, years, eo, 0.20)
    meta["X08a_owner_ae_p20"] = o20
    d["X08a_owner_low_ae"] = eo & (ow_ae <= o20)

    # ---- X08b: 新種牡馬(産駒の DB 初出走から 730 日以内) × 父が国内 G1 勝ち馬(2007 年以降の平地 G1 を DB で確認)
    g1w = d[(d["grade"] == "G1") & (d["_won_hist"] == 1.0)][["horse_id", "day"]]
    g1w = g1w.merge(h[["horse_id", "name_n"]], on="horse_id", how="left")
    first_g1 = g1w.groupby("name_n")["day"].min()
    sire_first_g1 = d["sire_n"].map(first_g1).to_numpy(dtype=float)
    sire_first_start = d.groupby("sire_n")["day"].transform("min").to_numpy(dtype=float)
    new_sire = np.isfinite(sire_first_start) & (day - sire_first_start <= 730)
    d["X08b_new_sire_g1"] = new_sire & np.isfinite(sire_first_g1) & (sire_first_g1 < day)
    meta["X08b_n_g1_winner_names"] = int(first_g1.size)

    # ---- X09: 初ダート × 父の産駒ダート市場超過 上位 1/3(自馬除外・strictly-before)
    sr_codes, _ = pd.factorize(d["sire_n"].fillna("__NA__"))
    dirt = is_dirt.to_numpy()
    sasof = AsOf(sr_codes, day, {"w": d["_won_hist"].to_numpy() * dirt, "q": d["_q"].to_numpy() * dirt, "n": dirt})
    s = sasof.query(sr_codes, day)
    sh = AsOf(hcodes, day, {"w": d["_won_hist"].to_numpy() * dirt, "q": d["_q"].to_numpy() * dirt}).query(hcodes, day)
    s_w, s_q = s["w"] - sh["w"], s["q"] - sh["q"]
    s_ae = np.where(d["sire_n"].notna().to_numpy() & (s_q >= 5.0), (s_w + 1.0) / (s_q + 1.0), np.nan)
    first_dirt = (dirt == 1.0) & (d["prior_starts"].to_numpy() >= 1) & (d["prior_dirt_starts"].to_numpy() == 0) & (
        d["birth_year"].to_numpy(dtype=float) >= 2005)
    d["sire_dirt_ae"] = s_ae
    ef = first_dirt & np.isfinite(s_ae)
    s67 = _quantile_disc(s_ae, years, ef, 2.0 / 3.0)
    meta["X09_sire_dirt_ae_p67"] = s67
    d["X09_first_dirt_sire_dirt_top"] = ef & (s_ae >= s67)

    # ---- MS10: 平地 G1 が行われる日の、G1 以外のレース × 単勝 20 倍以上 40 倍未満
    g1_days = set(d.loc[d["grade"] == "G1", "day"].tolist())
    odds = d["odds"].to_numpy(dtype=float)
    d["MS10_g1day_other_20_40"] = d["day"].isin(g1_days).to_numpy() & (d["grade"] != "G1").to_numpy() & (
        odds >= 20.0) & (odds < 40.0)

    # ---- MCU11: 前走でも S3(ens15 EV>1.2)に選ばれて、前走で勝てなかった馬
    if prev_ens_ev is not None:
        from horseracing_eval import attention_rules as ar
        s3 = ar.definition("S3")
        prev_ev = d["prev_ens_ev"].to_numpy(dtype=float)
        sel = ar.match_mask(s3, ens_ev=prev_ev, single_ev=np.full(len(d), np.nan), odds=np.full(len(d), np.nan),
                            days_since_last=np.full(len(d), np.nan))
        d["MCU11_prev_s3_lost"] = sel & (d["prev_won_hist"].to_numpy(dtype=float) == 0.0)
    else:
        d["MCU11_prev_s3_lost"] = False

    for c in CELL_IDS:
        d[c] = d[c].astype(bool)
    return d, meta
