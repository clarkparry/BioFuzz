from __future__ import annotations

import argparse
import sys
from pathlib import Path

from biofuzz.fuzzer import Campaign, load_global_config, load_target_config, merge_defaults
from biofuzz.ui import FuzzerTUI, QuietUI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BioFuzz fuzzing campaign")
    parser.add_argument("--target", required=True, help="Target name under targets/<name>/")
    parser.add_argument(
        "--seeds", default="seeds/approved_drugs.smi",
        help="Seed SMILES file, one 'SMILES id' pair per line "
             "(default: seeds/approved_drugs.smi)",
    )
    parser.add_argument(
        "--output", default="runs", help="Base output directory (default: runs/<date>_<target>)"
    )
    parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="Stop after this many iterations (default: run until interrupted)",
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Parallel docking processes (default: from config.yaml)",
    )
    parser.add_argument(
        "--checkpoint-every", type=int, default=None,
        help="Checkpoint every N iterations (default: from config.yaml)",
    )
    parser.add_argument(
        "--checkpoint-every-seconds", type=float, default=None,
        help="Also checkpoint on this wall-clock interval, whichever comes first "
             "(default: from config.yaml). One iteration is a whole batch of docks, "
             "so the iteration count alone is a coarse clock.",
    )
    parser.add_argument(
        "--resume", metavar="RUN_DIR", default=None,
        help="Continue a previous campaign from its run directory, reusing its "
             "corpus, coverage and findings (e.g. --resume runs/2026-09-11_hiv_protease)",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="RNG seed for reproducible stage selection"
    )
    parser.add_argument("--config", default="config.yaml", help="Global config path")
    parser.add_argument(
        "--ui", choices=["tui", "quiet"], default=None,
        help="Display mode (default: from config.yaml, or tui on a TTY / quiet otherwise)",
    )
    return parser.parse_args()


def _build_ui(mode: str, target: str, engine: str, workers: int, log_path):
    if mode == "tui":
        return FuzzerTUI(target=target, engine=engine, workers=workers, log_path=log_path)
    return QuietUI()


def main() -> int:
    args = parse_args()

    global_config = load_global_config(args.config)
    target_config = load_target_config(args.target)
    target_config = merge_defaults(global_config, target_config)

    fuzzer_cfg = global_config.get("fuzzer", {})
    workers = args.workers if args.workers is not None else fuzzer_cfg.get("workers", 1)
    checkpoint_every = (
        args.checkpoint_every
        if args.checkpoint_every is not None
        else fuzzer_cfg.get("checkpoint_every", 500)
    )
    checkpoint_every_seconds = (
        args.checkpoint_every_seconds
        if args.checkpoint_every_seconds is not None
        else fuzzer_cfg.get("checkpoint_every_seconds", 300)
    )

    if args.resume and not Path(args.resume).is_dir():
        print(f"Error: --resume directory not found: {args.resume}")
        return 2

    seeds_path = args.seeds if Path(args.seeds).exists() else None
    if seeds_path is None and not args.resume:
        print(f"Warning: seeds file not found at {args.seeds}, starting with an empty corpus")

    campaign = Campaign(
        target_name=args.target,
        target_config=target_config,
        global_config=global_config,
        output_dir=args.output,
        seeds_path=seeds_path,
        workers=workers,
        max_iterations=args.max_iterations,
        checkpoint_every=checkpoint_every,
        checkpoint_every_seconds=checkpoint_every_seconds,
        resume_dir=args.resume,
        seed=args.seed,
    )
    if args.resume:
        print(f"Resuming campaign in {campaign.layout.root}")

    ui_mode = args.ui or global_config.get("ui", {}).get("mode")
    if ui_mode is None:
        ui_mode = "tui" if sys.stdout.isatty() else "quiet"
    campaign.ui = _build_ui(
        ui_mode, args.target, target_config["docking"].get("engine", "gnina"),
        workers, campaign.layout.log_path,
    )

    try:
        state = campaign.run()
    except KeyboardInterrupt:
        print("\nAborted by user. Checkpoints saved.")
        return 130

    print(
        f"Campaign complete: {state.stopped_reason} | "
        f"iterations={state.iterations} hits={state.hits} "
        f"best_affinity={state.best_affinity} checkpoints={state.checkpoint_count}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
