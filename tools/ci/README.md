# CI: running the firmware through rtlsim

The firmware's CI moved to [rtlpg-tests](https://github.com/sirmick/rtlpg-tests):
its `ci/run.sh` (rtlsim-ci.sh's successor) builds this simulator and runs
the tiers, and its `ci/rtlsim.yml` is the workflow every RTLPlayground branch
carries as `.github/workflows/rtlsim.yml`.

What stays here is the simulator's own `make test`, which that workflow runs
as its `sim` tier.

## Fidelity

These are functional tests. rtlsim's flash is instant unless `--flash-timing`
is set, and it runs one fixed CPU clock, so it does not reproduce the real
chip's flash timing or the reset-clock phase of the boot0 installer. A green
run means the firmware's logic is sound, not that an update is safe to flash
to a switch. The bench remains the final gate.

In particular, a chip reset's own behaviour is not modelled here: plain
rtlsim boots every reset. On silicon, resets issued before the switch init
have hung until a power cycle (every boot0 install, the old mid-boot ^R^R,
the 8x schedtest's ^R^R before it ran the init), so a green run doesn't
show an early reset is safe on a real switch. `--early-reset-hang` (on
under `--control`) models what the 8x did, for resets by the application
only, with a proxy for the condition: the end of RTLPlayground's switch
init (`src/sys/system.h`).
