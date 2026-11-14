# CI: running the firmware through rtlsim

`rtlsim-ci.sh` boots a built RTLPlayground firmware in rtlsim and runs the
test suite. `firmware-rtlsim.yml` is the GitHub Actions workflow that does it
on every firmware push; it belongs in the RTLPlayground fork as
`.github/workflows/rtlsim.yml`, and is kept here so the workflow and the
script it calls stay together.

## What it runs

`tools/ci/rtlsim-ci.sh RTLPG_DIR [TIER ...]` — tiers: `core net vm powercut flash`.

| Tier | Covers | Needs |
|---|---|---|
| core | ISA + bank (SDCC vs host), boot log vs silicon, NIC ARP/ICMP/TCP, flow goldens, flash-model self-test | — |
| net | Linux in a netns over a tap and over an OVS bridge; switching | sudo, openvswitch |
| vm | the Alpine switch VM booted under QEMU | qemu (KVM if present, else TCG) |
| powercut | a power cut at every step of a web firmware install | — |
| flash | standard→compact migration, the RTL8238B PoE load, the loader start | an image matrix in `$RTLSIM_IMAGES` (see below); skipped if absent |

Run it locally against this checkout the same way CI does:

    tools/ci/rtlsim-ci.sh ~/src/RTLPlayground            # all tiers
    tools/ci/rtlsim-ci.sh ~/src/RTLPlayground core net   # a subset

It drives the Makefile, so `make test`, `make test-net`, `make vm test-vm`,
`make test-powercut` and `make test-flash` are what actually run; a single
`RTLPG=` points the build, the SDCC for the ISA/bank tests, and the firmware
image all at one checkout.

## The image matrix (flash tier)

`test_migrate`, `test_pse` and `test_loader` read `$RTLSIM_IMAGES/<set>/`
(default `~/rtlsim/images`) with a `manifest.json`: standard, compact, loader,
bridge and old-standard chip images for a few boards. The RTLPlayground fork
owns building it, as `make rtlsim-images OUT=dir` (synthetic PoE images, never
the vendor's firmware). The workflow calls that target when it exists; until
then the flash tier skips rather than fails, so the rest of CI is useful
immediately.

## Fidelity

These are functional tests. rtlsim's flash is instant unless `--flash-timing`
is set, and it runs one fixed CPU clock, so it does not reproduce the real
chip's flash timing or the reset-clock phase of the boot0 installer. A green
run means the firmware's logic is sound, not that an update is safe to flash
to a switch. The bench remains the final gate.
