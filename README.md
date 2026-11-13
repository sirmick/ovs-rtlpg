# rtlsim

An RTL837x switch simulated with the real RTLPlayground firmware. See
[DESIGN.md](DESIGN.md).

Status: the real firmware boots to its CLI prompt (in 0.1 s), its boot log
matching the bench SWTG024AS line for line, and CLI commands work against the
modelled chip. Through the CPU port's NIC, Linux pings the switch and loads
its web UI.

- `src/cpu`: MAME's mcs51/DW8051 core (vendored unchanged in
  `third_party/mame`, behind `src/shim/emu.h`), the RTL837x variant, and an
  idle fast-forward (the firmware sleeps in `delay()` between ticks).
- `src/sys`: code banking and the flash image; the flash controller over
  RTLPlayground's SPI-NOR model (`third_party/rtlplayground`); the switch
  core (register handshake, SerDes, SMI/PHY) and its table engine (VLAN, L2,
  flush, counters, ported from RTLPlayground's `test/hw_mock.c`); the CPU
  port's NIC (`nic.h`: DMA, RX ring, TX area, INT1, checksum offload).
- `src/net`: a front port on a UNIX stream socket, framed as QEMU's
  `-netdev stream`.

    make -j            # build/rtlsim
    make test -j       # ISA, banking and firmware boot tests
    build/rtlsim --image fw.bin [--flash-size 512K] [--cycles N|0] [--trace]
                 [--trace-sfr] [--trace-reg] [--trace-flash] [--quiet] [--stats]
    tests/fw/console.py fw.bin --cmd "vlan 10 1t 2" --cmd l2   # drive the CLI
    make check-banks   # boot FW_IMAGE with the bank check (src/sys/bankcheck.h)
    make test-net      # Linux in a netns against the firmware (sudo; ~2 s)
    build/rtlsim --image fw.bin --cycles 0 --nic /tmp/sw.sock --nic-port 4 [--trace-nic]

`--check-banks MAP` reports constants read through the wrong code bank
(code read as data, or a module's constant read with another bank mapped),
using the SDCC `.rst` listings next to the map for every byte's bank, kind and
label. On the pre-fix firmware (bf93461) it names the three strings of
`rtl837x_init` read from bank 3 instead of bank 2; the current one is clean.
`--flash state.bin` keeps the chip in a file across runs. `--link 4=2g5`
plugs a cable into a physical port (the firmware sees it in REG_LINKS* and
gets its link interrupt); GPIO inputs nobody drives read 1, so SFP cages are
empty.

`--nic PATH --nic-port N` listens on a UNIX socket and wires physical port N
(4 is front port 1 on the SWTG024AS) to the CPU. Until OVS is in, the CPU is
the only thing on the switch's other side. A peer is QEMU (`-netdev
stream,id=n,server=off,addr.type=unix,addr.path=PATH`) or
`tests/net/tapbridge.py` onto a tap. While a peer is connected the
firmware's clock is held to the wall clock (`--no-realtime` turns that off).
`--trace-nic` logs each frame and the CPU's busy cycles per reply. The
firmware's address is 192.168.2.2 unless its saved config says otherwise
(CLI: `ip`), and the web password is 1234.

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
- `tests/net`: `test_nic.py` (in `make test`) puts hand-made ARP, ICMP,
  TCP and VLAN frames on the socket. `test_net.py` (`make test-net`, needs
  sudo, skipped without) bridges the socket to a tap in a private network
  namespace; its Linux pings the switch, logs in, loads the web UI and
  measures throughput. It also prints what each kind of reply cost the CPU
  (DESIGN.md, "How fast").
- `tests/sdcc-bugs`: two SDCC 4.6.0 miscompilations the ISA tests found.

## Found

- RTLPlayground (dev, 3428100): the main loop's event bits share bit-space
  with other modules' bits. `evflags` is byte 0x20, and its flags are
  `__bit __at(0x00..0x03)`. The linker doesn't reserve absolute bits, so it
  puts BSEG bits on top of them. In the compact image, `cmd_parser`'s spilled
  bool locals are bits 0-5 (`clr 0x00`, `setb 0x01`, `mov 0x02,c`: link_irq,
  rx_irq, tick_pending), and `tx_buf_empty` is bit 6. So `if (!evflags)`
  never sleeps while the UART is idle (the firmware runs flat out after its
  boot), and CLI parsing can set or lose an event: a lost `rx_irq` with EX1
  masked would stop the NIC for good. Not fixed here; the firmware is a
  separate repo.

## Parked

- The two SDCC bugs in `tests/sdcc-bugs` are upstream: stock 4.6.0 has them
  too. To do: scan RTLPlayground for the cast pattern (`cast_u8_to_u32.c`,
  small model, the firmware's model); report both to SDCC.
