# rtlsim

An RTL837x switch simulated with the real RTLPlayground firmware. See
[DESIGN.md](DESIGN.md).

Status: the real firmware boots to its CLI prompt (in 0.4 s), its boot log
matching the bench SWTG024AS line for line, and CLI commands work against the
modelled chip.

- `src/cpu`: MAME's mcs51/DW8051 core (vendored unchanged in
  `third_party/mame`, behind `src/shim/emu.h`), the RTL837x variant, and an
  idle fast-forward (the firmware sleeps in `delay()` between ticks).
- `src/sys`: code banking and the flash image; the flash controller over
  RTLPlayground's SPI-NOR model (`third_party/rtlplayground`); the switch
  core (register handshake, SerDes, SMI/PHY) and its table engine (VLAN, L2,
  flush, counters, ported from RTLPlayground's `test/hw_mock.c`).

    make -j            # build/rtlsim
    make test -j       # ISA, banking and firmware boot tests
    build/rtlsim --image fw.bin [--flash-size 512K] [--cycles N|0] [--trace]
                 [--trace-sfr] [--trace-reg] [--trace-flash] [--quiet] [--stats]
    tests/fw/console.py fw.bin --cmd "vlan 10 1t 2" --cmd l2   # drive the CLI
    make check-banks   # boot FW_IMAGE with the bank check (src/sys/bankcheck.h)

`--check-banks MAP` reports constants read through the wrong code bank
(code read as data, or a module's constant read with another bank mapped),
using the SDCC `.rst` listings next to the map for every byte's bank, kind and
label. On the pre-fix firmware (bf93461) it names the three strings of
`rtl837x_init` read from bank 3 instead of bank 2; the current one is clean.
`--flash state.bin` keeps the chip in a file across runs.

UART is stdin/stdout. Registers nobody has modelled are plain storage, and
the first access to each is logged ("unmodelled reg ...").

Tests need SDCC; by default the one in `~/src/RTLPlayground/toolchain`.

- `tests/isa`: each program built with SDCC (small, medium, large,
  stack-auto) and run on rtlsim, and built for the host; outputs must match.
- `tests/bank`: code and constants in BANK1-3 called across banks through
  RTLPlayground's `crtbank.asm`, packed like the firmware (`tools/mkimage.py`).
- `tests/fw`: boots the RTLPlayground image (`FW_IMAGE`, default from
  `RTLPG=~/src/RTLPlayground`), checks the boot log against silicon
  (`golden/`) and a few CLI commands.
- `tests/sdcc-bugs`: two SDCC 4.6.0 miscompilations the ISA tests found.

## Parked

- The two SDCC bugs in `tests/sdcc-bugs` are upstream: stock 4.6.0 has them
  too. To do: scan RTLPlayground for the cast pattern (`cast_u8_to_u32.c`,
  small model, the firmware's model); report both to SDCC.
