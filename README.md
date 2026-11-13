# rtlsim

An RTL837x switch simulated with the real RTLPlayground firmware. See
[DESIGN.md](DESIGN.md).

Status: the real firmware boots to its CLI prompt (in 0.1 s), its boot log
matching the bench SWTG024AS line for line, and CLI commands work against the
modelled chip. The switch runs as an OVS bridge whose flows rtlsim compiles
from the chip's state, so its front ports switch as the firmware configures
them: VLANs, PVIDs, frame types, ingress filter, isolation, learning and
ageing. Linux pings the switch and loads its web UI, and firmware updates
through the web UI install on the simulated flash.

- `src/cpu`: MAME's mcs51/DW8051 core (vendored unchanged in
  `third_party/mame`, behind `src/shim/emu.h`), the RTL837x variant, and an
  idle fast-forward (the firmware sleeps in `delay()` between ticks).
- `src/sys`: code banking and the flash image; the flash controller over
  RTLPlayground's SPI-NOR model (`third_party/rtlplayground`); the switch
  core (register handshake, SerDes, SMI/PHY) and its table engine (VLAN, L2,
  flush, counters, ported from RTLPlayground's `test/hw_mock.c`); the CPU
  port's NIC (`nic.h`: DMA, RX ring, TX area, INT1, checksum offload); the
  I2C master (`i2c.h`) and SFP modules on it (`board.h`: the bench board's
  cages).
- `src/net`: a front port on a UNIX stream socket, framed as QEMU's
  `-netdev stream`; the OpenFlow 1.3 controller of an OVS bridge (`--ofp`).
- `src/fabric`: the flow compiler (register state and tables to an OVS flow
  set, `compiler.h`), and the glue that keeps the bridge's flows current and
  learns and ages L2 entries in the chip model (`fabric.h`).

    make -j            # build/rtlsim
    make test -j       # ISA, banking and firmware boot tests
    build/rtlsim --image fw.bin [--flash-size 512K] [--cycles N|0] [--trace]
                 [--trace-sfr] [--trace-reg] [--trace-flash] [--quiet] [--stats]
    tests/fw/console.py fw.bin --cmd "vlan 10 1t 2" --cmd l2   # drive the CLI
    make check-banks   # boot FW_IMAGE with the bank check (src/sys/bankcheck.h)
    make test-net      # Linux in netns against the firmware, via a tap and via OVS, and switching (sudo; ~20 s)
    tools/rtlswitch.py fw.bin --host 1=192.168.2.1/24 --host 2=192.168.2.3/24   # a switch to play with (sudo)
    make test-update   # a web firmware update and a power cut mid-install (sudo; ~1.5 min)
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

`--board FILE` describes the hardware around the chip (`src/sys/board.h`,
`boards/swtg024as.json`): the chip's type, revision, UUID and lot number
(the default MAC comes from these), the flash chip, the front ports and the
SFP cages (slot, detect and LOS pins, I2C pins), and the modules in them.
Without it, the bench SWTG024AS is built in.

`--sfp SLOT=EEPROM.bin` puts a module in SFP cage 1 or 2 (the firmware's
slot numbers): its detect pin goes low and its EEPROM (SFF-8472 A0h, and A2h
if the file has 512 bytes) answers on the cage's I2C bus. Empty cages NACK.
`tools/mksfp.py` writes an EEPROM (`--speed 10g|1g`, `--vendor`, `--model`).
Give the cage's port a link too (`--link 8=10g` for slot 1).
`--trace-i2c` logs each transfer.

`--ofp PATH` makes rtlsim the controller of an OVS bridge
(`ovs-vsctl set-controller BR unix:PATH`, `fail_mode=secure`). The
bridge's ports are front ports: OpenFlow port N is physical port N - 1.
Each port's link follows its OVS port. rtlsim installs the flows the
chip's state compiles to (DESIGN.md "Compiler"), and recompiles when the
firmware changes a VLAN, a PVID, an isolation mask or the L2 table. Frames
from unknown sources come to rtlsim once, to be learned into the chip's L2
table, so the firmware's `l2` command shows what OVS forwards by. Frames for
the CPU are packet-ins, and its own frames go out as packet-outs.
`--print-flows` prints the flow set at exit; `--ageing S` sets the L2 ageing
time (300 s).
`tools/privovs.py` runs a private OVS; `tools/rtlswitch.py` builds a switch
on it from a board file. Every front port becomes an OVS port (a veth), and
each `--host FRONT=ADDR/LEN` gets a namespace of its own, `rtl-sw0-FRONT`,
with Linux on that port: `sudo ip netns exec rtl-sw0-1 curl
http://192.168.2.2/`. The switch's console is the terminal, and `^C`
removes everything.

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
  namespace (`--via tap`), or builds the switch on OVS with
  `tools/rtlswitch.py` (`--via ovs`): Linux on front port 1, a second Linux
  on port 2, and a pull of port 1's cable. Its Linux
  pings the switch, logs in, loads the web UI and every JSON page it uses
  (with a module in slot 1), and measures throughput. It also prints what
  each kind of reply cost the CPU (DESIGN.md, "How fast"). `test_update.py`
  (`make test-update`) works on a persistent `--flash`. It uploads a good
  image across `loader_confirm()`'s moment and follows the install through
  its resets. Then it uploads a corrupted image, which must be refused.
  Last, it cuts the power in the middle of a second install and checks the
  next power-on finishes it (if the image can resume; dev e3347e6 and later).
- `tests/fabric`: `test_flows.py` (in `make test`) compares the flows that
  firmware configurations (VLANs, PVIDs, isolation, frame types) compile
  to with `golden/` (`--update` rewrites them).
  `tests/net/test_switching.py` (`make test-net`) puts three Linux hosts on
  front ports 1-3 and configures the switch through its CLI. It checks
  switching, learning (in the firmware's `l2` table), no flooding of known
  unicast, isolation, PVID and tagged VLANs, and ageing.
- `tests/sdcc-bugs`: two SDCC 4.6.0 miscompilations the ISA tests found.

## Found

- RTLPlayground (dev, 3428100): the main loop's event bits share bit-space
  with other modules' bits. `evflags` is byte 0x20, and its flags are
  `__bit __at(0x00..0x03)`. The linker doesn't reserve absolute bits, so it
  puts BSEG bits on top of them. In the compact image, `cmd_parser`'s spilled
  bool locals are bits 0-5 (`clr 0x00`, `setb 0x01`, `mov 0x02,c`: link_irq,
  rx_irq, tick_pending), and `tx_buf_empty` is bit 6. So `if (!evflags)`
  never sleeps while the UART is idle (the firmware runs flat out after its
  boot). CLI parsing also writes the events: `cmd_is_space_or_nul()` can
  clear a pending `link_irq`, which leaves EX0 masked, and the firmware then
  ignores link changes until it reboots. `rx_irq` is only ever set by
  mistake, which is harmless. Fixed in RTLPlayground 169af36 (branch
  `evflags-bits`, on dev e3347e6, not yet merged): with it the firmware
  sleeps ~98% of the time it's idle, and 20 s of its time simulates in 0.24 s
  instead of 7 s.
- RTLPlayground (dev, e3347e6): 15 s after each boot (`LDR_CONFIRM_S`),
  `handle_tick()` calls `loader_confirm()`, which reads the loader's boot
  counter (64 bytes) into `flash_buf`. That is the buffer a firmware upload
  in flight is filling with its next page, so the page is written with
  0xff in it. The read-back check then refuses the image ("NO: checksum
  failed"), so nothing breaks, but an update started soon after a boot
  fails. Two `fwupdate.py` runs in a row do this, because the second
  catches the first's reboot. Fixed in RTLPlayground 56db072 (on
  `evflags-bits`, handed to the dev checkout to merge): `loader_confirm()`
  reads the 2-byte mark into a buffer of its own. `test_update.py` uploads
  across the 15 s mark, so it fails on firmware without the fix.

## Parked

- The two SDCC bugs in `tests/sdcc-bugs` are upstream: stock 4.6.0 has them
  too. To do: scan RTLPlayground for the cast pattern (`cast_u8_to_u32.c`,
  small model, the firmware's model); report both to SDCC.
