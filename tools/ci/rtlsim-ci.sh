#!/bin/bash
# rtlsim-ci.sh - run the RTLPlayground firmware through rtlsim, as CI does.
#
#   tools/ci/rtlsim-ci.sh RTLPG_DIR [TIER ...]
#
# RTLPG_DIR is a built RTLPlayground checkout (its patched SDCC under
# toolchain/, and at least a SWGT024_V2_0_UNMANAGED compact build in output/).
# With no TIER it runs them all; name tiers to run a subset:
#
#   core      build rtlsim; ISA + bank (SDCC vs host), the firmware boot log
#             vs silicon, NIC ARP/ICMP/TCP, the flow-compiler goldens, the
#             flash-model self-test.           (no privilege)
#   net       Linux in a netns against the firmware, over a tap and over an
#             OVS bridge, and switching.        (needs sudo + openvswitch)
#   vm        build the Alpine switch VM and boot it under QEMU.  (needs qemu;
#             KVM if present, else TCG)
#   powercut  a power cut at every step of a web firmware install.
#   flash     standard->compact migrations (2 and 4 MiB boards), the RTL8238B
#             PoE load, the loader start, a web update through the loader on
#             every board.  Needs an image matrix in $RTLSIM_IMAGES (make
#             rtlsim-images); skipped, not failed, when it is absent.
#
# These are functional tests, not silicon-timing-faithful: rtlsim's flash and
# clock are not the real chip's (see tools/ci/README.md), so a pass is not a
# green light to flash hardware - the bench is the final gate.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OVS=$(cd "$HERE/../.." && pwd)
RTLPG=${1:?usage: rtlsim-ci.sh RTLPG_DIR [TIER ...]}; shift || true
RTLPG=$(cd "$RTLPG" && pwd)
TIERS=("$@"); [ ${#TIERS[@]} -eq 0 ] && TIERS=(core net vm powercut flash)
export RTLPG
M="make -C $OVS RTLPG=$RTLPG"
rc=0

run() {	# run() NAME CMD...; groups the log and records a failure
	local name=$1; shift
	echo "::group::$name"
	if "$@"; then echo "--- $name: ok"; else rc=1; echo "::error::$name failed"; fi
	echo "::endgroup::"
}

echo "rtlsim-ci: RTLPG=$RTLPG"
ls "$RTLPG"/toolchain/sdcc-*/bin/sdcc >/dev/null 2>&1 || { echo "::error::no patched SDCC in $RTLPG/toolchain (build the firmware first)"; exit 2; }
$M -j build/rtlsim >/dev/null || { echo "::error::rtlsim did not build"; exit 2; }

for t in "${TIERS[@]}"; do
	case $t in
	core)     run "core (ISA, bank, boot, NIC, flows, flash model)" $M test ;;
	net)      run "network (tap, OVS, switching)" $M test-net ;;
	vm)       run "QEMU VM boot" bash -c "$M vm && $M test-vm" ;;
	powercut) run "power cut during a firmware install" $M test-powercut ;;
	flash)
		images=${RTLSIM_IMAGES:-$HOME/rtlsim/images}
		if ls "$images"/*/manifest.json >/dev/null 2>&1; then
			run "flash matrix (migrate, PoE, loader, loader update)" env RTLSIM_IMAGES="$images" $M test-flash
		else
			echo "flash matrix: no image set in $images (make rtlsim-images), skipped"
		fi ;;
	*) echo "::error::unknown tier $t"; rc=1 ;;
	esac
done

echo "rtlsim-ci: $([ $rc -eq 0 ] && echo PASS || echo FAILED)"
exit $rc
