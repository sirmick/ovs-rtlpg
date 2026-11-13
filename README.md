# rtlsim

An RTL837x switch simulated with the real RTLPlayground firmware. See
[DESIGN.md](DESIGN.md).

Phase 1 (now): the 8051. MAME's mcs51/DW8051 core (vendored unchanged in
`third_party/mame`, behind `src/shim/emu.h`), the RTL837x variant
(`src/cpu`), and the flash image's code banking (`src/sys`).

    make -j            # build/rtlsim
    make test -j       # differential ISA tests + banking test
    build/rtlsim --image fw.bin [--cycles N] [--trace] [--trace-sfr] [--stats]

Tests need SDCC; by default the one in `~/src/RTLPlayground/toolchain`.

- `tests/isa`: each program built with SDCC (small, medium, large,
  stack-auto) and run on rtlsim, and built for the host; outputs must match.
- `tests/bank`: code and constants in BANK1-3 called across banks through
  RTLPlayground's `crtbank.asm`, packed like the firmware (`tools/mkimage.py`).
- `tests/sdcc-bugs`: two SDCC 4.6.0 miscompilations the ISA tests found.
