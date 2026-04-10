from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path

from biofuzz.core.fuzzer import run
from biofuzz.core.tui import FuzzerTUI
from biofuzz.docking.config import (
    apply_global_defaults_to_target,
    load_global_config,
    load_target_config,
)
from biofuzz.docking import runner as docking_runner
from biofuzz.molecules import preparation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BioFuzz runner")
    parser.add_argument("--target", required=True, help="Target name under targets/<name>/")
    parser.add_argument(
        "--seeds",
        default="seeds/zinc_druglike_10k.smi",
        help="Path to seed SMILES corpus",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory (default: runs/<date>_<target>)",
    )
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--checkpoint-every", type=int, default=None)
    parser.add_argument("--mutations-per-entry", type=int, default=None)
    parser.add_argument("--engine", default=None, help="Docking engine binary name")
    parser.add_argument("--config", default="config.yaml", help="Global config path")
    return parser.parse_args()


def _requires_runtime_dependencies(max_iterations: int | None) -> bool:
    return max_iterations is None or max_iterations > 0


def _missing_runtime_dependencies(engine: str | None) -> list[str]:
    missing: list[str] = []
    if preparation.Chem is None or preparation.AllChem is None:
        missing.append("RDKit")
    if not preparation.meeko_available():
        missing.append("Meeko")
    if docking_runner._resolve_binary(engine) is None:
        engine_label = engine or "gnina/vina/quickvina2/quickvina-w"
        missing.append(f"docking binary ({engine_label})")
    return missing


def _gpu_enabled(engine: str | None) -> bool:
    resolved = docking_runner._resolve_binary(engine)
    if resolved is None:
        return False
    if "gnina" not in Path(resolved).name.lower():
        return False
    cuda_visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if cuda_visible is not None and cuda_visible.strip().lower() in {"", "-1", "none"}:
        return False
    return Path("/dev/nvidiactl").exists() or Path("/proc/driver/nvidia/version").exists()


def main() -> int:
    args = parse_args()
    global_cfg = load_global_config(args.config)
    target_cfg = apply_global_defaults_to_target(
        load_target_config(args.target),
        global_cfg,
    )
    corpus_cfg = global_cfg["corpus"]
    molecule_cfg = global_cfg["molecules"]
    engine_name = args.engine if args.engine is not None else global_cfg["docking"]["engine"]
    worker_count = args.workers if args.workers is not None else global_cfg["fuzzer"]["workers"]

    if _requires_runtime_dependencies(args.max_iterations):
        missing = _missing_runtime_dependencies(engine_name)
        if missing:
            print(
                "BioFuzz cannot start a live fuzzing run because required runtime "
                f"dependencies are unavailable: {', '.join(missing)}"
            )
            return 1

    output_dir = args.output
    if output_dir is None:
        stamp = datetime.now().strftime("%Y-%m-%d")
        output_dir = f"runs/{stamp}_{args.target}"

    tui = FuzzerTUI(
        target=target_cfg.name,
        engine=engine_name,
        workers=worker_count,
        gpu_enabled=_gpu_enabled(engine_name),
    )

    try:
        stats = run(
            target_config=target_cfg,
            seed_smiles_path=args.seeds,
            output_dir=output_dir,
            max_iterations=args.max_iterations,
            workers=worker_count,
            checkpoint_every=(
                args.checkpoint_every
                if args.checkpoint_every is not None
                else global_cfg["fuzzer"]["checkpoint_every"]
            ),
            mutations_per_entry=(
                args.mutations_per_entry
                if args.mutations_per_entry is not None
                else global_cfg["molecules"]["mutations_per_entry"]
            ),
            molecule_min_mw=molecule_cfg.get("min_mw", 0.0),
            molecule_max_mw=molecule_cfg.get("max_mw", 550.0),
            molecule_max_logp=molecule_cfg.get("max_logp", 5.0),
            molecule_max_hbd=molecule_cfg.get("max_hbd", 5),
            molecule_max_hba=molecule_cfg.get("max_hba", 10),
            molecule_max_rot_bonds=molecule_cfg.get("max_rot_bonds", 10),
            exhaustiveness=global_cfg["docking"]["exhaustiveness_fuzz"],
            exhaustiveness_confirm=global_cfg["docking"].get("exhaustiveness_confirm", 16),
            num_modes=global_cfg["docking"]["num_modes"],
            engine=engine_name,
            max_corpus_size=corpus_cfg["max_size"],
            priority_new_bit_weight=corpus_cfg["priority_new_bit_weight"],
            priority_affinity_weight=corpus_cfg["priority_affinity_weight"],
            priority_reuse_penalty=corpus_cfg["priority_reuse_penalty"],
            logger=tui.log if tui.enabled else print,
            progress_callback=tui.update if tui.enabled else None,
        )
    finally:
        tui.close()

    stopped_reason = stats.get("stopped_reason")
    if stopped_reason == "keyboard_interrupt":
        print("Run interrupted by user; checkpoints saved.")
    else:
        print("Run complete")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    return 130 if stopped_reason == "keyboard_interrupt" else 0


if __name__ == "__main__":
    raise SystemExit(main())
