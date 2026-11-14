# rtlsim

An RTL837x Ethernet switch, simulated by running the real, unmodified
RTLPlayground firmware on an emulated 8051 and reproducing the switch chip
around it. The firmware boots to its CLI, serves its web UI, installs firmware
updates, and switches traffic; the front ports are Open vSwitch ports, so the
whole thing behaves as a real managed switch with an IP. See
[DESIGN.md](DESIGN.md) for how it is built.

## What is modelled

- The 8051 (MAME's mcs51/DW8051 core) with the RTL837x's code banking.
- The flash chip (SPI-NOR), so the saved config and firmware updates persist.
- The switch core: the register handshake, SerDes, SMI/PHY, and the VLAN / L2 /
  counter tables.
- The CPU-port NIC, the I2C master and SFP modules, and (on PoE boards) an
  RTL8238B PoE-controller stub.
- The fabric: a compiler from the chip's register state to an OpenFlow flow
  set, so OVS forwards exactly as the firmware configured the chip — VLANs,
  PVIDs, accepted frame types, the ingress filter, isolation, L2 learning and
  ageing.

Not modelled: analog PHY/SerDes behaviour, line-rate throughput or latency,
the exact L2 hash/capacity, and the boot ROM. A chip reset reloads the code
and restarts the CPU; why some resets hang real hardware is not reproduced.
Registers nobody has modelled are plain storage, and the first access to each
is logged (`unmodelled reg ...`).

## Build and run

    make -j                 # build/rtlsim
    make test -j            # ISA, banking and firmware-boot tests (needs SDCC)

Boot a firmware image and drive its CLI over stdin/stdout:

    build/rtlsim --image fw.bin
    tests/fw/console.py fw.bin --cmd "vlan 10 1t 2" --cmd l2

`fw.bin` is a full flash image (512 KiB / 1 / 2 MiB), the kind RTLPlayground's
build produces. `--flash state.bin` keeps the chip in a file across runs, so
the saved config, firmware updates and loader state persist. `--board FILE`
selects the hardware (below); without it, the built-in SWTG024AS is used. The
web password is 1234 and the default address is 192.168.2.2 (CLI: `ip`).

Tests need SDCC to build their 8051 programs; by default the one in
`$RTLPG/toolchain` (`RTLPG` defaults to `~/src/RTLPlayground`).

## On a network

The simplest wiring puts one front port on a UNIX socket:

    build/rtlsim --image fw.bin --cycles 0 --nic /tmp/sw.sock --nic-port 4

`--nic PATH --nic-port N` wires physical port N (4 is front port 1 on the
SWTG024AS) straight to the CPU, over a UNIX stream socket framed as QEMU's
`-netdev stream`. The peer is QEMU, or `tests/net/tapbridge.py` onto a tap.
While a peer is connected the firmware's clock is held to the wall clock, so
protocol timers agree with the peer (`--no-realtime` turns that off).

For the full switch, rtlsim is the OpenFlow controller of an OVS bridge:

    tools/rtlswitch.py fw.bin --host 1=192.168.2.1/24 --host 2=192.168.2.3/24
    sudo ip netns exec rtl-sw0-1 ping 192.168.2.2

`tools/rtlswitch.py` (needs sudo) builds a private OVS, makes every front port
an OVS port, and gives each `--host FRONT=ADDR/LEN` its own network namespace
(`rtl-sw0-FRONT`) with Linux on that port. `--port FRONT=IFACE` puts a real
interface there instead; `eth0` is refused. `--flash FILE` keeps the switch's
flash, and `tools/flashcfg.py FILE --set "ip 10.0.0.2" ...` edits that saved
config while the switch is off. The serial console is the terminal; `^C`
removes everything. Nothing outside these namespaces is touched.

Several switches, cabled to each other:

    tools/rtlswitch.py fw.bin --topo topo.yaml

`--topo FILE` runs the switches in a YAML topology (see "Topology file" in
DESIGN.md; the format is documented in `tools/rtlswitch.py`), each on its own
namespace and OVS, with a veth pair for every cable between front ports and
Linux hosts on the ports they name. The consoles share the terminal, each line
behind its switch's name; `NAME CMD` goes to one switch's CLI, and
`link a.4 down|up` pulls or replugs a cable, which both firmwares see.

## As a VM

    make vm                 # build/vm/rtlsw.qcow2 (Alpine, ~54 MB)
    make test-vm            # boot it under QEMU, no root (~15 s)

`make vm` builds a small VM that boots into the switch: rtlsim running the
firmware as the controller of a kernel-datapath OVS bridge over the VM's NICs.
NIC *n* is front port *n*+1; the second serial port is the firmware's CLI;
`-fw_cfg opt/rtlsw/conf` sets the firmware, board and SFP modules; and a data
disk labelled `RTLSW` keeps the flash across boots. See
[tools/vm/README.md](tools/vm/README.md) for the full QEMU command line. This
is the self-contained artifact: no host OVS, namespaces or privileges.

## Board files

The firmware carries its own port map and pin assignments; the board file
describes what is actually on the board, so a wrong one misbehaves as it would
on silicon. `--board FILE` (`src/sys/board.h`) gives the chip type, revision,
UUID and lot number (which set the default MAC), the flash chip, the front
ports, and the SFP cages (slot, detect/LOS pins, I2C pins) and their modules.
`tools/mkboard.py` drafts a file from RTLPlayground's machine sources;
`boards/` has a draft for each of its machines, with the flash size filled in
where the source omits it. The built-in default is the SWTG024AS
(`boards/swgt024_v2_0_unmanaged.json`).

`--sfp SLOT=EEPROM.bin` inserts a module in cage 1 or 2: its detect pin goes
low and its EEPROM (SFF-8472 A0h, plus A2h diagnostics if the file is 512
bytes) answers on the cage's I2C bus. `tools/mksfp.py` writes one
(`--speed 10g|1g`, `--vendor`, `--model`); give the cage's port a link too
(`--link 8=10g` for slot 1).

## Options

    build/rtlsim --image fw.bin [options]

| Option | |
|---|---|
| `--flash FILE` | keep the chip in a file across runs |
| `--flash-size 512K` | flash size (else from the board, else 512K) |
| `--board FILE` | the board (above) |
| `--image FW` | initial flash contents (or the whole of it, in memory) |
| `--cycles N` | run N machine cycles, or `0` for no limit |
| `--link P=SPEED` | plug a cable into physical port P (`10m` `100m` `1g` `2g5` `5g` `10g` `down`) |
| `--nic PATH --nic-port N` | wire physical port N to the CPU over a socket |
| `--ofp PATH` | act as the OpenFlow controller of an OVS bridge on PATH |
| `--sfp SLOT=FILE` | a module in an SFP cage |
| `--map FILE` / `--check-banks MAP` | symbols; report constants read through the wrong code bank |
| `--flash-timing` | make flash erases/programs take their real time |
| `--trace`, `--trace-sfr`, `--trace-reg`, `--trace-flash`, `--trace-nic`, `--trace-i2c`, `--trace-of`, `--trace-reset` | trace output on stderr |
| `--print-flows` | print the compiled flow set at exit |
| `--stats`, `--quiet` | summary counters; suppress the "unmodelled" log |

`--ofp PATH` makes rtlsim the controller of an OVS bridge
(`ovs-vsctl set-controller BR unix:PATH`, `fail_mode=secure`): OpenFlow port N
is physical port N − 1, each port's link follows its OVS port, and rtlsim
installs the flows the chip's state compiles to and recompiles on any change.
Frames from unknown sources reach rtlsim once, to be learned into the chip's
L2 table, so the firmware's `l2` command shows what OVS forwards by. Frames for
the CPU are packet-ins; the CPU's own frames go out as packet-outs. The bridge
is normally set up by `tools/rtlswitch.py`, not by hand.

`--check-banks MAP` reports constants read through the wrong code bank (code
read as data, or a module's constant read with another bank mapped), using the
SDCC `.rst` listings next to the map. `--trace-reset` logs each chip reset with
its PC and whether `setup_clock()` had run since the last one.

## Tests

| Target | Covers | Needs |
|---|---|---|
| `make test` | ISA vs host, bank mapping, firmware boot log vs golden, the NIC, the flow compiler goldens, the flash model, a firmware install | SDCC |
| `make test-net` | Linux in a netns against the firmware over a tap and over OVS; three-host switching; two cabled switches | sudo |
| `make test-vm` | the VM boots, switches, and keeps its flash | — |
| `make test-flash` | the PoE board's RTL8238B; standard→compact migrations on 2 MiB | an image set |
| `make test-update` | a web firmware update, and a power cut mid-install | sudo |
| `make test-powercut` | a power cut at every step of an install | — |
| `make check-banks` | boot `FW_IMAGE` with the bank check | SDCC |

- **`tests/isa`** builds each program with SDCC (small/medium/large/stack-auto)
  and compares rtlsim's output with the host build's.
- **`tests/bank`** exercises code and constants in BANK1-3 called across banks.
- **`tests/fw`** boots the image and checks its boot log against a golden
  serial log and a few CLI commands.
- **`tests/net`** puts hand-made frames on the socket (`test_nic.py`), bridges
  the socket to Linux in a netns over a tap or OVS and loads the web UI
  (`test_net.py`), runs three hosts through VLAN/PVID/isolation/learning/ageing
  (`test_switching.py`), and cables two switches together (`test_topo.py`).
- **`tests/fabric`** compares compiled flow sets with goldens (`--update`
  rewrites them).
- **`tests/flash`** tests the flash model, the PoE image stream, the
  standard→compact migration, a firmware install and resume, and power-cut
  sweeps. The image-set tests read `$RTLSIM_IMAGES` and skip without it.
- **`tests/sdcc-bugs`** holds two SDCC 4.6.0 miscompilations the ISA tests
  surfaced.
