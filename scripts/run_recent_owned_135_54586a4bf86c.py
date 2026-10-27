"""Owned135 execution namespace. Only output routing changes; frozen calculations stay unchanged."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "54586a4bf86c"
WORK = ROOT / "artifacts/135-mixture-reproducibility" / ("recent-owned-" + TOKEN)
EVIDENCE = ROOT / "specs/135-mixture-reproducibility/evidence" / ("owned-" + TOKEN)


def bind():
    import mixture_recent_owned_135_54586a4bf86c as core
    core.WORK = WORK
    original = core.source_files
    def owned_sources():
        files = original()
        for path in [Path(__file__).resolve(), *[ROOT / "scripts" / name for name in (
            "audit_mixture_recent_inputs_135.py", "audit_mixture_recent_135.py",
            "audit_mixture_reproducibility_135.py")]]:
            files[str(path)] = core.digest(path)
        return files
    core.source_files = owned_sources
    return core


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "predict", "score", "audit-inputs", "audit-predictions", "audit-scores"))
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if args.phase.startswith("audit-"):
        if args.phase == "audit-scores":
            import audit_mixture_recent_135 as audit
            audit.WORK = WORK
            audit.OUT = EVIDENCE / "recent-numeric-review.json"
            audit.run()
        else:
            import audit_mixture_recent_inputs_135 as audit
            audit.WORK = WORK
            audit.output_path = lambda phase: EVIDENCE / ("recent-" + phase + "-independent-review.json")
            audit.run(args.phase.removeprefix("audit-"))
    else:
        core = bind()
        print(json.dumps(getattr(core, args.phase)(), allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
