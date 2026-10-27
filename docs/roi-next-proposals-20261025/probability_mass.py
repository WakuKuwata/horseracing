"""Describe raw race probability mass; no normalized-policy scoring."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from analyze import ROOT, OUT, LOCATIONS, sha


def run():
    result, hashes = {}, {}
    for name, (directory, arm) in LOCATIONS.items():
        path = ROOT / "artifacts" / directory / f"{arm}_ensemble.parquet"
        hashes[str(path.relative_to(ROOT))] = sha(path)
        d = pd.read_parquet(path)
        mass = d.groupby("race_id").p.sum()
        selected = d.p*d.odds > 1.2
        result[name] = dict(races=len(mass), mean=float(mass.mean()), median=float(mass.median()),
            p01=float(mass.quantile(.01)), p99=float(mass.quantile(.99)),
            fraction_outside_0_95_1_05=float(((mass < .95) | (mass > 1.05)).mean()),
            selected_horses_in_mass_above_one_fraction=float(d.loc[selected, "race_id"].map(mass).gt(1).mean()),
            selected_mean_mass=float(d.loc[selected, "race_id"].map(mass).mean()))
    hashes[str(Path(__file__).resolve().relative_to(ROOT))] = sha(Path(__file__))
    for name, digest in hashes.items():
        assert sha(ROOT / name) == digest
    with (OUT / "probability_mass.json").open("x") as stream:
        json.dump(dict(results=result, input_sha256=hashes, new_fits=0, new_policies_scored=0,
            note="Mass incoherence is descriptive, not evidence that normalization increases profit"), stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    run()
