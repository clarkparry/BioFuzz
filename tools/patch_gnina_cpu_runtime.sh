#!/usr/bin/env bash
# Makes a CUDA-linked gnina "compat" binary runnable on a CPU-only machine
# (no GPU, no system CUDA install) by:
#   1. Installing the NVIDIA CUDA/cuDNN runtime as user-space pip wheels
#      into .venv (provides libcudart/libcublas/libcusparse/libcufft/
#      libcusolver/libcudnn/libnvjitlink).
#   2. Building a no-op stub for libnvToolsExt.so.1 (classic NVTX v1),
#      which ships with none of the pip nvidia-*-cu12 wheels but is only
#      used for optional GPU profiling markers gnina never calls on the
#      CPU-only docking path.
#   3. Patching gnina's rpath (via patchelf, installed as a pip wheel) so
#      it finds all of the above without requiring LD_LIBRARY_PATH.
#
# Usage: tools/patch_gnina_cpu_runtime.sh [path-to-gnina-binary]
# Defaults to .tools/bin/gnina.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GNINA_BIN="${1:-$REPO_ROOT/.tools/bin/gnina}"
VENV="$REPO_ROOT/.venv"
COMPAT_LIBS_DIR="$(dirname "$GNINA_BIN")/compat-libs"

if [[ ! -x "$GNINA_BIN" ]]; then
  echo "gnina binary not found or not executable: $GNINA_BIN" >&2
  echo "Run: $VENV/bin/python tools/install_gnina.py --variant compat" >&2
  exit 1
fi

"$VENV/bin/pip" install --quiet \
  nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 \
  nvidia-cusparse-cu12 nvidia-cufft-cu12 nvidia-cusolver-cu12 \
  nvidia-nvtx-cu12 patchelf

mkdir -p "$COMPAT_LIBS_DIR"

STUB_C="$(mktemp --suffix=.c)"
STUB_MAP="$(mktemp --suffix=.map)"
trap 'rm -f "$STUB_C" "$STUB_MAP"' EXIT

cat > "$STUB_C" << 'EOF'
/* No-op stub for libnvToolsExt.so.1 (NVTX v1 profiling API).
   Satisfies gnina's dynamic loader on the CPU-only docking path, which
   links against NVTX transitively via libtorch but never calls it
   without an active CUDA profiler attached. */
#include <stddef.h>
#define STUB_V(name) void name(void) {}
#define STUB_P(name) void* name(void) { return NULL; }
#define STUB_I(name) int name(void) { return 0; }
STUB_I(nvtxInitialize)
STUB_P(nvtxDomainCreateA) STUB_P(nvtxDomainCreateW) STUB_V(nvtxDomainDestroy)
STUB_V(nvtxDomainMarkEx)
STUB_I(nvtxDomainRangeStartEx) STUB_V(nvtxDomainRangeEnd)
STUB_I(nvtxDomainRangePushEx) STUB_I(nvtxDomainRangePop)
STUB_P(nvtxDomainResourceCreate) STUB_V(nvtxDomainResourceDestroy)
STUB_V(nvtxDomainNameCategoryA) STUB_V(nvtxDomainNameCategoryW)
STUB_P(nvtxDomainRegisterStringA) STUB_P(nvtxDomainRegisterStringW)
STUB_V(nvtxMarkA) STUB_V(nvtxMarkW) STUB_V(nvtxMarkEx)
STUB_I(nvtxRangeStartA) STUB_I(nvtxRangeStartW) STUB_I(nvtxRangeStartEx)
STUB_V(nvtxRangeEnd)
STUB_I(nvtxRangePushA) STUB_I(nvtxRangePushW) STUB_I(nvtxRangePushEx)
STUB_I(nvtxRangePop)
STUB_V(nvtxNameCategoryA) STUB_V(nvtxNameCategoryW)
STUB_V(nvtxNameOsThreadA) STUB_V(nvtxNameOsThreadW)
STUB_V(nvtxNameCudaDeviceA) STUB_V(nvtxNameCudaDeviceW)
STUB_V(nvtxNameCudaContextA) STUB_V(nvtxNameCudaContextW)
STUB_V(nvtxNameCudaStreamA) STUB_V(nvtxNameCudaStreamW)
STUB_V(nvtxNameCudaEventA) STUB_V(nvtxNameCudaEventW)
STUB_V(nvtxNameCuDeviceA) STUB_V(nvtxNameCuDeviceW)
STUB_V(nvtxNameCuContextA) STUB_V(nvtxNameCuContextW)
STUB_V(nvtxNameCuStreamA) STUB_V(nvtxNameCuStreamW)
STUB_V(nvtxNameCuEventA) STUB_V(nvtxNameCuEventW)
STUB_V(nvtxNameClDeviceA) STUB_V(nvtxNameClDeviceW)
STUB_V(nvtxNameClContextA) STUB_V(nvtxNameClContextW)
STUB_V(nvtxNameClCommandQueueA) STUB_V(nvtxNameClCommandQueueW)
STUB_V(nvtxNameClMemObjectA) STUB_V(nvtxNameClMemObjectW)
STUB_V(nvtxNameClSamplerA) STUB_V(nvtxNameClSamplerW)
STUB_V(nvtxNameClProgramA) STUB_V(nvtxNameClProgramW)
STUB_V(nvtxNameClEventA) STUB_V(nvtxNameClEventW)
EOF

cat > "$STUB_MAP" << 'EOF'
libnvToolsExt.so.1 {
  global:
    nvtx*;
  local:
    *;
};
EOF

gcc -shared -fPIC -o "$COMPAT_LIBS_DIR/libnvToolsExt.so.1" "$STUB_C" \
  -Wl,-soname,libnvToolsExt.so.1 -Wl,--version-script="$STUB_MAP"

REL_LIB_DIRS=""
for d in "$VENV"/lib/python3.*/site-packages/nvidia/*/lib; do
  [[ -d "$d" ]] || continue
  rel="\$ORIGIN/../../.venv/${d#"$VENV"/}"
  REL_LIB_DIRS="${REL_LIB_DIRS}${rel}:"
done

NEW_RPATH="\$ORIGIN/compat-libs:${REL_LIB_DIRS%:}"
"$VENV/bin/patchelf" --set-rpath "$NEW_RPATH" "$GNINA_BIN"

echo "Patched $GNINA_BIN"
echo "rpath: $NEW_RPATH"
"$GNINA_BIN" --version
