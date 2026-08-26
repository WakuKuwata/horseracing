"""ensemble_spike の本走を長時間バックグラウンドで回すためのランナー(feature 100 Phase C)。

`SPIKE_SPEC_DIR` を差し替えられるようにしてあるのは、配線スモークを**凍結 config とは別の
config** で回すため。本走は必ず `specs/100-eval-contract-v5/` を指す(hash 照合が効く)。
"""

from __future__ import annotations

import os
import pathlib


def main() -> None:
    src = pathlib.Path(os.environ["SPIKE_SCRIPT"]).read_text()
    spec_dir = os.environ.get("SPIKE_SPEC_DIR")
    if spec_dir:
        src = src.replace(
            'SPEC_DIR = pathlib.Path(__file__).resolve().parents[1] / "specs" / "100-eval-contract-v5"',
            "SPEC_DIR = pathlib.Path(os.environ['SPIKE_SPEC_DIR'])",
        )
    ns: dict = {"__name__": "spike_runner",
                "__file__": str(pathlib.Path(os.environ["SPIKE_SCRIPT"]).resolve())}
    exec(compile(src, "ensemble_spike", "exec"), ns)  # noqa: S102

    class Args:
        from_ = os.environ["SPIKE_FROM"]
        to = os.environ["SPIKE_TO"]
        materialized_path = os.environ.get("SPIKE_PARQUET") or None
        json_out = os.environ["SPIKE_OUT"]

    ns["run"](Args(), ns["load_frozen"]())


if __name__ == "__main__":
    main()
