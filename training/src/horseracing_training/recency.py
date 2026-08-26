"""feature 101: 学習の時間重み(recency weighting)。"""

from __future__ import annotations

import datetime
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from numbers import Real
from typing import Any

import numpy as np

from .ev_weight import assert_race_constant

RECENCY_SCHEME = "recency-v1"
DEFAULT_FLOOR = 0.05
MIN_HALF_LIFE_DAYS = 30
MAX_HALF_LIFE_DAYS = 7300
NORMALIZE_BASIS = "row_sum_equals_n"


class RecencyContractError(RuntimeError):
    """重み計算の契約違反(fail-closed)。"""


def _finite_float(value: object, *, name: str) -> float:
    """設定値の単位違いや欠損を後段の計算へ流さないため、有限の実数だけを受け付ける。"""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise RecencyContractError(f"{name} は有限の実数でなければなりません")
    result = float(value)
    if not math.isfinite(result):
        raise RecencyContractError(f"{name} は有限の実数でなければなりません")
    return result


def _date_only(value: object, *, name: str) -> datetime.date:
    """時刻の切り捨てが呼び出し側のバグを隠すため、datetime は明示的に拒否する。"""
    if isinstance(value, datetime.datetime) or not isinstance(value, datetime.date):
        raise RecencyContractError(
            f"{name} は datetime ではなく datetime.date でなければなりません"
        )
    return value


def _object_array(values: object, *, name: str) -> np.ndarray:
    """row-aligned 契約を曖昧にしないため、一次元の有限長 iterable に固定する。"""
    if isinstance(values, (str, bytes)):
        raise RecencyContractError(f"{name} は一次元配列でなければなりません")
    try:
        items = list(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise RecencyContractError(f"{name} は一次元配列でなければなりません") from exc

    result = np.empty(len(items), dtype=object)
    result[:] = items
    return result


def _weights_array(weights: object, *, allow_empty: bool = False) -> np.ndarray:
    """NaN や負値を ESS・監査値に混ぜると安全側に倒れないため、入口で拒否する。"""
    values = _object_array(weights, name="weights")
    if len(values) == 0 and not allow_empty:
        raise RecencyContractError("weights は空にできません")
    try:
        result = np.asarray(values.tolist(), dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise RecencyContractError("weights は有限の非負実数でなければなりません") from exc
    if result.ndim != 1:
        raise RecencyContractError("weights は一次元配列でなければなりません")
    if not np.isfinite(result).all() or (result < 0.0).any():
        raise RecencyContractError("weights は有限の非負実数でなければなりません")
    if len(result) > 0 and not (result > 0.0).any():
        raise RecencyContractError("weights の総和は正でなければなりません")
    return result


def _is_missing_group(value: object) -> bool:
    """None と NaN を通常カテゴリと分離し、groupby 相当の欠損落ちを防ぐ。"""
    if value is None:
        return True
    if isinstance(value, (float, np.floating)):
        return bool(np.isnan(value))
    return False


def _group_key(value: object) -> object:
    """欠損表現の違いや行順でキーが変わらないよう、欠損は None に統一する。"""
    key = None if _is_missing_group(value) else value
    try:
        hash(key)
    except TypeError as exc:
        raise RecencyContractError("グループ値は辞書キーにできる値でなければなりません") from exc
    return key


def _group_sort_key(value: object) -> tuple[str, str]:
    """異種型のカテゴリも値の表現で並べ、入力行順によらない監査出力にする。"""
    value_type = type(value)
    return (f"{value_type.__module__}.{value_type.__qualname__}", repr(value))


def _validate_race_ids(race_ids: np.ndarray) -> None:
    """既存のレース単位検査が欠損 ID を別レースへ混同しないよう、先に拒否する。"""
    for race_id in race_ids:
        if _is_missing_group(race_id):
            raise RecencyContractError("race_ids に欠損値を含めることはできません")
        _group_key(race_id)


def _validated_dates(
    race_dates: object,
    *,
    cutoff: object,
    expected_length: int,
) -> tuple[np.ndarray, datetime.date]:
    """未来情報や欠損日を重みに混ぜないため、全日付を計算前に検査する。

    **実行時の壁時計を読まない。** 「cutoff が今日より後か」を検査したくなるが、それをすると
    重みが `(race_date, cutoff)` の純関数でなくなり、**同じ呼び出しが日付をまたぐと結果
    (成功/例外)を変える**。walk-forward の cutoff は常に過去であり、意味のある異常は
    「race_date が cutoff より後 = 経過日数が負」の方で、これは入力だけから判定できる。

    cutoff が全レースよりはるかに先という退化(全重みが floor に潰れる)は、ここではなく
    ESS の下限検査で表に出る — そちらは値から決まるので壁時計に依存しない。
    """
    cutoff_date = _date_only(cutoff, name="cutoff")

    values = _object_array(race_dates, name="race_dates")
    if len(values) != expected_length:
        raise RecencyContractError("race_ids と race_dates の長さが一致しません")

    dates = np.empty(expected_length, dtype=object)
    for index, value in enumerate(values):
        race_date = _date_only(value, name=f"race_dates[{index}]")
        if race_date > cutoff_date:
            raise RecencyContractError(
                f"race_dates[{index}] が cutoff より後で、経過日数が負になります"
            )
        dates[index] = race_date
    return dates, cutoff_date


def _assert_race_weights(race_ids: np.ndarray, weights: np.ndarray) -> None:
    """PL 尤度を壊す per-horse 重みを、既存の共通検査で fail-closed にする。"""
    try:
        assert_race_constant(race_ids, weights)
    except (TypeError, ValueError) as exc:
        raise RecencyContractError("同じ race_id の行で重みが一定ではありません") from exc


@dataclass(frozen=True)
class RecencyWeightSpec:
    """日付だけで事前登録し、結果を見てから変えない時間重みの定義。"""

    half_life_days: float
    floor: float = DEFAULT_FLOOR
    scheme: str = RECENCY_SCHEME
    normalize: str = NORMALIZE_BASIS
    selection_basis: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        half_life_days = _finite_float(self.half_life_days, name="half_life_days")
        if not MIN_HALF_LIFE_DAYS <= half_life_days <= MAX_HALF_LIFE_DAYS:
            raise RecencyContractError(
                "half_life_days は "
                f"[{MIN_HALF_LIFE_DAYS}, {MAX_HALF_LIFE_DAYS}] の範囲でなければなりません"
            )
        floor_value = _finite_float(self.floor, name="floor")
        if not 0.0 < floor_value < 1.0:
            raise RecencyContractError("floor は 0 より大きく 1 より小さくなければなりません")
        if self.scheme != RECENCY_SCHEME:
            raise RecencyContractError(f"scheme は {RECENCY_SCHEME!r} でなければなりません")
        if self.normalize != NORMALIZE_BASIS:
            raise RecencyContractError(
                f"normalize は {NORMALIZE_BASIS!r} でなければなりません"
            )
        if not isinstance(self.selection_basis, dict):
            raise RecencyContractError("selection_basis は dict でなければなりません")

        # frozen dataclass 内の数値型と辞書を標準化し、artifact の直列化を安定させる。
        object.__setattr__(self, "half_life_days", half_life_days)
        object.__setattr__(self, "floor", floor_value)
        object.__setattr__(self, "selection_basis", dict(self.selection_basis))

    def to_dict(self) -> dict:
        """呼び出し側の変更で凍結済み定義が書き換わらないよう、辞書をコピーして返す。"""
        return {
            "half_life_days": self.half_life_days,
            "floor": self.floor,
            "scheme": self.scheme,
            "normalize": self.normalize,
            "selection_basis": dict(self.selection_basis),
        }


def build_recency_weights(race_ids, race_dates, *, cutoff, spec) -> np.ndarray:
    """行ごとの重み(float64・race_ids に row-aligned)を返す。

    ``alpha_tilde = floor + (1 - floor) * 0.5 ** (age_days / half_life_days)``
    を計算する。LightGBM が消費するのはレースではなく行重みなので、レース平均を 1 にしても
    総量が N からずれる。実測でも ``corr(年, 平均頭数) = -0.809`` のため 0.6〜1.5% ずれるので、
    ``alpha = alpha_tilde * len(rows) / sum_rows(alpha_tilde)`` とし、行重み総和を行数にする。
    """
    if not isinstance(spec, RecencyWeightSpec):
        raise RecencyContractError("spec は RecencyWeightSpec でなければなりません")

    ids = _object_array(race_ids, name="race_ids")
    if len(ids) == 0:
        raise RecencyContractError("race_ids と race_dates は空にできません")
    _validate_race_ids(ids)
    dates, cutoff_date = _validated_dates(
        race_dates,
        cutoff=cutoff,
        expected_length=len(ids),
    )

    ages = np.fromiter(
        ((cutoff_date - race_date).days for race_date in dates),
        dtype=np.float64,
        count=len(dates),
    )
    raw = spec.floor + (1.0 - spec.floor) * np.power(
        0.5,
        ages / spec.half_life_days,
    )

    # math.fsum で総量を値から決め、入力行の並べ替えによる加算順の差を避ける。
    raw_sum = math.fsum(float(value) for value in raw)
    if not math.isfinite(raw_sum) or raw_sum <= 0.0:
        raise RecencyContractError("正規化前の重み総和が有限の正数ではありません")
    weights = np.asarray(raw * (len(ids) / raw_sum), dtype=np.float64)

    # 丸め後も契約を満たすことを実測し、将来の式変更を静かに通さない。
    weight_sum = math.fsum(float(value) for value in weights)
    if not math.isclose(weight_sum, float(len(ids)), rel_tol=1e-9, abs_tol=0.0):
        raise RecencyContractError("行重み総和が行数と一致しません")
    _assert_race_weights(ids, weights)
    return weights


def effective_sample_size(weights) -> float:
    """``(sum(w) ** 2) / sum(w ** 2)`` で実効行数を返す。"""
    values = _weights_array(weights)
    total = math.fsum(float(value) for value in values)
    squared_total = math.fsum(float(value) * float(value) for value in values)
    if not math.isfinite(total) or not math.isfinite(squared_total) or squared_total <= 0.0:
        raise RecencyContractError("ESS を有限値として計算できません")
    return float(total * total / squared_total)


def ess_by_group(weights, groups) -> dict:
    """グループ値ごとの ESS を、欠損値も独立した ``None`` キーとして返す。

    欠損を黙って落とすと監査対象の行が減るため、None と NaN は同じ欠損カテゴリへ束ねる。
    """
    values = _weights_array(weights)
    group_values = _object_array(groups, name="groups")
    if len(values) != len(group_values):
        raise RecencyContractError("weights と groups の長さが一致しません")

    buckets: dict[object, list[float]] = {}
    for weight, group in zip(values, group_values, strict=True):
        key = _group_key(group)
        buckets.setdefault(key, []).append(float(weight))

    # 各グループの値だけで集約するので、入力行の順序に意味を持たせない。
    return {
        key: effective_sample_size(buckets[key])
        for key in sorted(buckets, key=_group_sort_key)
    }


@dataclass(frozen=True)
class WeightAudit:
    """再学習時に同じ重みを再現し、実効データ量を検査するための監査値。"""

    cutoff: str
    half_life_days: float
    floor: float
    normalize: str
    ess_total: float
    ess_by: dict
    weight_min: float
    weight_max: float
    weight_mean: float
    weight_sum: float
    n_rows: int
    n_races: int
    regime_mass: float | None
    vanished_categories: dict
    scope: dict

    def to_dict(self) -> dict:
        """入れ子の可変値をコピーし、監査オブジェクト自体を変更させずに返す。"""
        return {
            "cutoff": self.cutoff,
            "half_life_days": self.half_life_days,
            "floor": self.floor,
            "normalize": self.normalize,
            "ess_total": self.ess_total,
            "ess_by": {name: dict(values) for name, values in self.ess_by.items()},
            "weight_min": self.weight_min,
            "weight_max": self.weight_max,
            "weight_mean": self.weight_mean,
            "weight_sum": self.weight_sum,
            "n_rows": self.n_rows,
            "n_races": self.n_races,
            "regime_mass": self.regime_mass,
            "vanished_categories": {
                name: list(values) for name, values in self.vanished_categories.items()
            },
            "scope": dict(self.scope),
        }


def _major_categories_by_group(major_categories: object) -> dict[object, list[object]]:
    """主要カテゴリの粒度を明示させ、別粒度の同名値との取り違えを防ぐ。"""
    if major_categories is None:
        return {}
    if not isinstance(major_categories, Mapping):
        raise RecencyContractError("major_categories は粒度名からカテゴリ列への dict で指定します")

    result: dict[object, list[object]] = {}
    for name, categories in major_categories.items():
        if isinstance(categories, (str, bytes)):
            raise RecencyContractError("主要カテゴリは文字列ではなくカテゴリ値の配列で指定します")
        try:
            result[name] = [_group_key(category) for category in categories]
        except TypeError as exc:
            raise RecencyContractError("主要カテゴリは iterable で指定します") from exc
    return result


def _unique_count(values: np.ndarray) -> int:
    """行の出現順ではなく ID の値でレース数を数える。"""
    return len({_group_key(value) for value in values})


def build_audit(
    race_ids,
    race_dates,
    weights,
    *,
    cutoff,
    spec,
    group_specs=None,
    regime_from=None,
    scope=None,
    ess_floor=None,
    major_categories=None,
) -> WeightAudit:
    """監査値を組み立て、ESS 下限を割った場合は fail-closed にする。"""
    if not isinstance(spec, RecencyWeightSpec):
        raise RecencyContractError("spec は RecencyWeightSpec でなければなりません")

    ids = _object_array(race_ids, name="race_ids")
    if len(ids) == 0:
        raise RecencyContractError("race_ids と race_dates は空にできません")
    _validate_race_ids(ids)
    dates, cutoff_date = _validated_dates(
        race_dates,
        cutoff=cutoff,
        expected_length=len(ids),
    )
    weight_values = _weights_array(weights)
    if len(weight_values) != len(ids):
        raise RecencyContractError("race_ids と weights の長さが一致しません")
    if not (weight_values > 0.0).all():
        raise RecencyContractError("recency weight はすべて正でなければなりません")
    _assert_race_weights(ids, weight_values)

    weight_sum = math.fsum(float(value) for value in weight_values)
    if not math.isclose(weight_sum, float(len(ids)), rel_tol=1e-9, abs_tol=0.0):
        raise RecencyContractError("行重み総和が行数と一致しません")

    if group_specs is None:
        group_specs = {}
    if not isinstance(group_specs, Mapping):
        raise RecencyContractError("group_specs は粒度名からグループ配列への dict で指定します")
    ess_by: dict[Any, dict] = {
        name: ess_by_group(weight_values, groups) for name, groups in group_specs.items()
    }

    ess_total = effective_sample_size(weight_values)
    floor_value = None if ess_floor is None else _finite_float(ess_floor, name="ess_floor")
    if floor_value is not None and floor_value <= 0.0:
        raise RecencyContractError("ess_floor は正でなければなりません")
    if floor_value is not None and ess_total < floor_value:
        raise RecencyContractError(
            f"全体 ESS {ess_total} が ess_floor {floor_value} を下回りました"
        )

    vanished_categories: dict[Any, list[object]] = {
        name: (
            [category for category, ess in group_ess.items() if ess < floor_value]
            if floor_value is not None
            else []
        )
        for name, group_ess in ess_by.items()
    }

    major_by_group = _major_categories_by_group(major_categories)
    for name, categories in major_by_group.items():
        if name not in ess_by:
            raise RecencyContractError(f"主要カテゴリの粒度 {name!r} が group_specs にありません")
        if floor_value is None:
            continue
        for category in categories:
            category_ess = ess_by[name].get(category, 0.0)
            if category_ess < floor_value:
                raise RecencyContractError(
                    f"主要カテゴリ {name!r}/{category!r} の ESS {category_ess} が "
                    f"ess_floor {floor_value} を下回りました"
                )

    if regime_from is None:
        regime_mass = None
    else:
        regime_date = _date_only(regime_from, name="regime_from")
        if regime_date > cutoff_date:
            raise RecencyContractError("regime_from は cutoff より後にできません")
        regime_weight = math.fsum(
            float(weight)
            for weight, race_date in zip(weight_values, dates, strict=True)
            if race_date >= regime_date
        )
        regime_mass = float(regime_weight / weight_sum)

    if scope is None:
        scope = {}
    if not isinstance(scope, Mapping):
        raise RecencyContractError("scope は dict でなければなりません")

    return WeightAudit(
        cutoff=cutoff_date.isoformat(),
        half_life_days=spec.half_life_days,
        floor=spec.floor,
        normalize=spec.normalize,
        ess_total=ess_total,
        ess_by=ess_by,
        weight_min=float(np.min(weight_values)),
        weight_max=float(np.max(weight_values)),
        weight_mean=float(weight_sum / len(weight_values)),
        weight_sum=float(weight_sum),
        n_rows=len(ids),
        n_races=_unique_count(ids),
        regime_mass=regime_mass,
        vanished_categories=vanished_categories,
        scope=dict(scope),
    )
