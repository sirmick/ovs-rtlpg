#!/bin/sh
# mkvm.sh - build build/vm/rtlsw.qcow2: a small Alpine VM that is one
# simulated switch (tools/vm/README.md).
#
#   tools/vm/mkvm.sh [FIRMWARE.bin]
#
# FIRMWARE defaults to $FW_IMAGE, else the newest SWTG024AS compact build in
# $RTLPG (~/src/RTLPlayground). Everything happens in an alpine container
# (Docker), unprivileged.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
TOP=$(cd "$HERE/../.." && pwd)
RTLPG=${RTLPG:-$HOME/src/RTLPlayground}
FW=${1:-${FW_IMAGE:-$(ls -t "$RTLPG"/output/SWGT024_V2_0_UNMANAGED-compact/rtlplayground-v*-compact.bin 2>/dev/null | head -1)}}
[ -f "$FW" ] || { echo "mkvm: no firmware image (give one, or set FW_IMAGE)" >&2; exit 1; }
OUT=$TOP/build/vm
mkdir -p "$OUT"
cp "$FW" "$OUT/firmware.bin"
echo "mkvm: firmware $(basename "$FW")"
docker run --rm \
	-v "$TOP:/src:ro" -v "$RTLPG:/rtlpg:ro" -v "$OUT:/fw:ro" -v "$OUT:/out" \
	alpine:3.21 sh /src/tools/vm/build-inside.sh
