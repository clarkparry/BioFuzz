from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path

from biofuzz.core.fuzzer import PROGRESS_HEARTBEAT_SECONDS, run
from biofuzz.core.tui import FuzzerTUI
from biofuzz.docking.config import (
    apply_global_defaults_to_target,
    load_global_config,
    load_target_config,
    resolve_coverage_config,
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
    if engine:
        requested_binary = docking_runner.resolve_requested_binary(engine)
        if requested_binary is None:
            missing.append(f"docking binary ({engine})")
        else:
            issues = docking_runner.runtime_issues_for_binary(requested_binary)
            if issues:
                missing.append(f"{Path(requested_binary).name} runtime ({'; '.join(issues)})")
    else:
        resolved_binary = docking_runner._resolve_binary(None)
        if resolved_binary is None:
            missing.append("docking binary (gnina/vina/quickvina2/quickvina-w)")
        else:
            issues = docking_runner.runtime_issues_for_binary(resolved_binary)
            if issues:
                missing.append(f"{Path(resolved_binary).name} runtime ({'; '.join(issues)})")
    return missing


def _binary_name(binary: str | None) -> str:
    if binary is None:
        return "unknown"
    return Path(binary).name or binary


def _is_gnina_binary(binary: str | None) -> bool:
    if binary is None:
        return False
    return "gnina" in Path(binary).name.lower()


def _gpu_enabled_for_binary(binary: str | None) -> bool:
    if not _is_gnina_binary(binary):
        return False
    cuda_visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if cuda_visible is not None and cuda_visible.strip().lower() in {"", "-1", "none"}:
        return False
    return Path("/dev/nvidiactl").exists() or Path("/proc/driver/nvidia/version").exists()


def _engine_runtime_note(
    requested_engine: str | None,
    resolved_binary: str | None,
    gpu_enabled: bool,
) -> str | None:
    requested_label = _binary_name(requested_engine) if requested_engine else None
    resolved_label = _binary_name(resolved_binary) if resolved_binary else None

    if requested_label and resolved_label and requested_label != resolved_label:
        if "gnina" in requested_label.lower() and "gnina" not in resolved_label.lower():
            return f"gnina unavailable or not runnable; using {resolved_label}."
        return f"Requested {requested_label}; using {resolved_label}."

    if requested_label and resolved_label is None:
        if "gnina" in requested_label.lower():
            return "gnina unavailable and no fallback docking binary resolved."
        return f"Requested {requested_label}; no docking binary resolved."

    if _is_gnina_binary(resolved_binary) and not gpu_enabled:
        cuda_visible = os.environ.get("CUDA_VISIBLE_DEVICES")
        if cuda_visible is not None and cuda_visible.strip().lower() in {"", "-1", "none"}:
            return "gnina found, but CUDA_VISIBLE_DEVICES disables GPU; running on CPU."
        return "gnina found, but GPU is not available; running on CPU."

    return None


def _runtime_engine_details(engine: str | None) -> tuple[str, bool, str | None]:
    resolved_binary = docking_runner._resolve_binary(engine)
    display_engine = resolved_binary if resolved_binary is not None else (engine or "unknown")
    gpu_enabled = _gpu_enabled_for_binary(resolved_binary)
    note = _engine_runtime_note(
        requested_engine=engine,
        resolved_binary=resolved_binary,
        gpu_enabled=gpu_enabled,
    )
    return display_engine, gpu_enabled, note


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
    runtime_engine, runtime_gpu_enabled, runtime_engine_note = _runtime_engine_details(engine_name)
    worker_count = args.workers if args.workers is not None else global_cfg["fuzzer"]["workers"]
    coverage_cfg = resolve_coverage_config(global_cfg)

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
        engine=runtime_engine,
        workers=worker_count,
        gpu_enabled=runtime_gpu_enabled,
    )
    if runtime_engine_note:
        tui.notice(runtime_engine_note)

    stats: dict[str, float | int | str] | None = None
    run_error: Exception | None = None
    try:
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
                coverage_config=coverage_cfg,
                logger=tui.log if tui.enabled else print,
                progress_callback=tui.update if tui.enabled else None,
                progress_heartbeat_seconds=(0.0 if tui.enabled else PROGRESS_HEARTBEAT_SECONDS),
            )
        except Exception as exc:  # noqa: BLE001 - user-facing CLI boundary
            run_error = exc
    finally:
        tui.close()

    if run_error is not None:
        print(f"Run failed unexpectedly: {type(run_error).__name__}: {run_error}")
        return 1
    if stats is None:
        print("Run failed: no campaign stats were produced.")
        return 1

    stopped_reason = stats.get("stopped_reason")
    if stopped_reason == "keyboard_interrupt":
        print("Run stopped: user quit fuzzing manually; checkpoints saved.")
    elif stopped_reason == "failed":
        failure_reason = stats.get("failure_reason", "unknown error")
        print(f"Run failed gracefully; checkpoints saved where possible. Reason: {failure_reason}")
    else:
        print("Run complete")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    if stopped_reason == "keyboard_interrupt":
        return 130
    if stopped_reason == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
