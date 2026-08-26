"""凍結済み gate-config を hash から引く(feature 100 / T003a).

FR-031a は「過去 verdict の δ を、その verdict が記録した gate-config hash から解決する。
**解決できなければ fail-closed**(現行の δ で補わない)」を要求する。その解決器が読む先が
本モジュールである。

**正本は `specs/*/gate-config.json` そのもの**で、別途レジストリファイルを作らない。理由:
レジストリを二重に持つと「spec を凍結したがレジストリに登録し忘れた」で静かに解決不能になり、
fail-closed が「登録漏れの検出」ではなく「運用の摩擦」に化ける。ディレクトリ規約を正本に
すれば、config を凍結した時点で自動的に引ける。

δ を現行値で補わないことが要点である。過去 verdict は当時の δ で判断されており、今の δ で
読み直すと採否の意味が変わる(新旧の ADOPT 率を比較しても無意味になる)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: リポジトリ直下の spec ディレクトリ。ここに凍結 config が置かれる規約。
_SPECS_DIRNAME = "specs"
_GATE_CONFIG_NAME = "gate-config.json"


class FrozenConfigNotFound(LookupError):
    """hash に対応する凍結 config が見つからない(fail-closed)。"""


@dataclass(frozen=True)
class FrozenConfig:
    """凍結された gate-config 1 件。"""

    gate_config_hash: str
    feature: str          #: 例 "097-early-mid-pace"
    path: str             #: リポジトリ相対パス
    config: dict

    @property
    def contract_version(self) -> str | None:
        return self.config.get("evaluation_contract_version")

    @property
    def min_effect_delta(self):
        """その config が凍結した δ。**現行値で補完しない**(欠落は None)。"""
        return self.config.get("min_effect_delta")


def _repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / _SPECS_DIRNAME).is_dir() and (parent / ".git").exists():
            return parent
    raise FrozenConfigNotFound(
        "リポジトリ直下(specs/ と .git を持つディレクトリ)を特定できない"
    )


@lru_cache(maxsize=1)
def _index(root_str: str) -> dict[str, FrozenConfig]:
    """hash -> FrozenConfig。同一 hash が複数 spec にあれば fail-closed。"""
    from horseracing_eval.decision import gate_config_hash

    root = Path(root_str)
    out: dict[str, FrozenConfig] = {}
    for path in sorted((root / _SPECS_DIRNAME).glob(f"*/{_GATE_CONFIG_NAME}")):
        try:
            cfg = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            # 壊れた config は「存在しない」ではなく「読めない」— 解決時に落とすため飛ばす
            continue
        if not isinstance(cfg, dict):
            continue
        h = gate_config_hash(cfg)
        rel = str(path.relative_to(root))
        if h in out and out[h].path != rel:
            raise FrozenConfigNotFound(
                f"gate-config hash {h!r} が複数の spec に存在する: {out[h].path} と {rel}。"
                "凍結 config は一意でなければ、過去 verdict の δ を一意に解決できない。"
            )
        out[h] = FrozenConfig(gate_config_hash=h, feature=path.parent.name, path=rel, config=cfg)
    return out


def resolve_frozen_config(gate_config_hash: str, *, root: Path | None = None) -> FrozenConfig:
    """hash から凍結 config を引く。見つからなければ **fail-closed**(FR-031a)。"""
    if not gate_config_hash:
        raise FrozenConfigNotFound(
            "gate_config_hash が空。過去 verdict の δ を現行値で補ってはならない(FR-031a)"
        )
    index = _index(str(root or _repo_root()))
    try:
        return index[gate_config_hash]
    except KeyError:
        raise FrozenConfigNotFound(
            f"gate-config hash {gate_config_hash!r} に対応する凍結 config が specs/ に無い。"
            "**現行の δ で補ってはならない** — 過去 verdict は当時の δ で判断されており、"
            "今の δ で読み直すと採否の意味が変わる(FR-031a)。"
        ) from None


def resolve_delta_for_verdict(verdict: dict, *, root: Path | None = None):
    """verdict が記録した hash から、その判断に使われた δ を引く(FR-031a)。

    verdict 自体が δ を持っていればそれを正とし、無ければ凍結 config から解決する。
    どちらでも解決できなければ fail-closed。
    """
    if not isinstance(verdict, dict):
        raise FrozenConfigNotFound("verdict が dict でない")
    for key in ("min_effect_delta", "delta"):
        if verdict.get(key) is not None:
            return verdict[key]
    frozen = resolve_frozen_config(str(verdict.get("gate_config_hash") or ""), root=root)
    delta = frozen.min_effect_delta
    if delta is None:
        raise FrozenConfigNotFound(
            f"{frozen.path} は min_effect_delta を持たない。現行値で補ってはならない(FR-031a)"
        )
    return delta
