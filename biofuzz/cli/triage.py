from __future__ import annotations

import argparse
import sys
from pathlib import Path

from biofuzz.fuzzer.config import load_global_config, load_target_config, merge_defaults
from biofuzz.triage import run_triage, write_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BioFuzz post-fuzzing triage")
    parser.add_argument("--findings", required=True, help="Path to a run's findings/ directory")
    parser.add_argument("--target", required=True, help="Target name under targets/<name>/")
    parser.add_argument(
        "--output", default=None,
        help="Triage output directory (default: <findings>/../triage)",
    )
    parser.add_argument("--config", default="config.yaml", help="Global config path")
    parser.add_argument(
        "--exhaustiveness-confirm", type=int, default=None,
        help="Exhaustiveness for confirmation re-docking (default: from config.yaml)",
    )
    parser.add_argument(
        "--top-n", type=int, default=20,
        help="How many ranked hits to copy into triage/top_hits/ (default: 20)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    global_config = load_global_config(args.config)
    target_config = load_target_config(args.target)
    target_config = merge_defaults(global_config, target_config)

    exhaustiveness_confirm = (
        args.exhaustiveness_confirm
        if args.exhaustiveness_confirm is not None
        else global_config.get("docking", {}).get("exhaustiveness_confirm", 16)
    )
    cnn_model_confirm = global_config.get("docking", {}).get("cnn_model_confirm")
    scoring_policy = target_config.get("oracle", {}).get("scoring_policy", "consensus")

    receptor_path = str(Path("targets") / args.target / target_config["receptor"])
    output_dir = args.output or str(Path(args.findings).parent / "triage")

    from biofuzz.triage.runner import default_stages

    records = run_triage(
        findings_dir=args.findings,
        target_config=target_config,
        receptor_path=receptor_path,
        stages=default_stages(
            exhaustiveness_confirm=exhaustiveness_confirm,
            cnn_model_confirm=cnn_model_confirm,
            scoring_policy=scoring_policy,
        ),
    )

    report_path = write_report(records, output_dir, top_n=args.top_n)

    n_flagged = sum(1 for r in records if r.filters_failed)
    print(
        f"Triage complete: {len(records)} findings analyzed, {n_flagged} filtered, "
        f"report at {report_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
