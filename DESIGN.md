# rtlsim — an RTL837x switch, simulated as an Open vSwitch bridge

Status: phases 1-3 built, and phase 4 (Fabric I) in large part. The switch
runs as an OVS bridge whose flows are compiled from the chip's state: VLANs,
PVIDs, frame types, ingress filter, isolation, L2 learning and ageing. The
CPU port's NIC and the firmware's TCP/IP, the web UI, SFP modules and
firmware updates all work. Next: phase 5 (STP, LAG, mirroring, meters,
IGMP). See README.md.

## Goal

    rtlsim --image rtlplayground.bin --board boards/swgt024_v2_0_unmanaged.json [--map rtlplayground.map]

brings up **one switch**: the real, unmodified firmware image running on an
emulated 8051 (with code banking), the RTL837x switch core reproduced as an
OVS userspace-datapath bridge, and the chip's internal CPU NIC grafted onto
it. The result is literally a switch: front-panel ports are OVS ports, it has
an IP, the web UI / CLI / LLDP / LACP / STP all run from the firmware, the
serial console is a pty. Several rtlsim instances on one OVS make a network
of switches to test between.

Non-goals: PHY/SerDes analog behaviour, line-rate or latency fidelity, L2
table hash/capacity behaviour, the boot ROM, the stock (vendor) firmware
(nice if it boots, not a target).

## Architecture

```
            rtlsim (one process per switch)                    ovs-vswitchd
 ┌───────────────────────────────────────────────────┐      (datapath_type=netdev,
 │  8051 core (MAME mcs51/dw8051, extracted)          │       shared by all switches)
 │   ├ code: 0x0000-3fff common, 0x4000-ffff ← PSBANK │     ┌────────────────────────┐
 │   ├ xdata 48K RAM | flash window (SFR 0x97)        │     │ bridge sw-<name>        │
 │   └ SFR bus ─┬─ UART ──────────── pty / tcp        │     │  fail_mode=secure       │
 │              ├─ timers / IRQs (EIE/EXIF/EIP)       │     │  no NORMAL/STP/LACP     │
 │              ├─ flash ctrl ───── flash file (NOR)  │     │                         │
 │              ├─ reg handshake A0-A7 ─┐              │     │  p1 p2 … pN  (veth/tap/ │
 │              ├─ SerDes 93/94, SMI C2-C5 → PHY model│     │   patch/real NIC)       │
 │              └─ NIC ring B3-B7 ──┐   │              │     └──────────▲─────────────┘
 │                                  │   ▼              │                │
 │  switch-core model: register file + behaviours     │   OpenFlow     │
 │   └ compiler: register state → desired flow set ───┼── (rtlsim is ──┘
 │  CPU port: NIC TX → packet-out, packet-in → NIC RX ┼──  the controller)
 └───────────────────────────────────────────────────┘
```

Three layers, deliberately separable:

1. **CPU** — runs the firmware; knows nothing about networking.
2. **Chip model** — SFR peripherals and the switch register file. The firmware
   only ever sees this layer.
3. **Fabric** — a pure function *register state → flow set*, plus a small
   OpenFlow controller that keeps OVS in sync and carries CPU-port frames.

OVS stays stock and unpatched. rtlsim is its controller.

## Layer 1: CPU

**Core: MAME's `src/devices/cpu/mcs51` (BSD-3-Clause), extracted.** It has
a `dw8051` variant (Synopsys DW8051: dual DPTR, CKCON) on the shared core,
which matches the Dallas-style SFRs the RTL837x firmware uses (CKCON 0x8e,
MPAGE 0x92, EIE 0xe8, EIP 0xf8, EXIF 0x91).

Extraction: the core touches the framework only through four memory spaces
(`m_program`, `m_xdata`, `m_sfr`, `m_idata` read/write_byte), an
`address_map` for SFR handlers, `state_add`/`save_item` (no-ops for us),
`logerror`, `standard_irq_callback`, `debugger_instruction_hook`, `BIT()`. We
write a thin shim (`mame_shim.h`: memory accessors over our own callbacks, an
SFR handler table, no-op state/save) and vendor the core's files unchanged,
so MAME fixes can be re-synced. Keep the disassembler (`mcs51dasm`) for traces.

Our variant, `rtl837x_cpu : dw8051`:
- `sfr_map()`: 0x80 (P0 on a stock 8051) is `SFR_FLASH_EXEC`, 0xa0 (P2) is
  `SFR_EXEC_GO` — remap. Add EIE/EIP/EXIF and the extended interrupt sources.
- `external_ram_iaddr()`: `movx @Ri` takes its page from MPAGE (0x92), not P2.
- Code banking: 0x0000-0x3fff common, 0x4000-0xffff a 48K window chosen by
  `PSBANK` (SFR 0x96, `& 0x1f`). Mapping (from `tools/imagebuilder.c`): the
  image starts with a 2-byte header, code at flash offset 2; bank *b*'s window
  is flash `0x4000 + (b-1)*0xc000`. **Verify** PSBANK=0 and the header offset
  against a flash dump and a boot on silicon.
- xdata: 48 KiB RAM; `MAP_FLASH_PAGE_TO_XDATA` (0x97) = 1 at reset maps
  0x4000-0xbfff to the flash image, the boot code clears it.

**Clock:** derive from the firmware's timer reloads, so its 1 ms tick is a
real millisecond. Run throttled to real time, so protocol timers (LACP, STP,
ageing) interoperate with real peers.

**Checker (from `--map` / `.cdb`):** every instruction fetch and `movc` read in
0x4000-0xffff is checked against the bank that owns the address. A mismatch
is the const-bank bug class, caught at run time on the real binary. Also: SP
overflow into idata variables, xdata reads before any write, xdata accesses
beyond 48K, writes to the flash window.

**Validation:** run SDCC's mcs51 regression tests (built for the plain 8051)
on our core, and compare with ucsim on the same binaries.

## Layer 2: chip model

### SFR peripherals
| SFRs | Peripheral | Model |
|---|---|---|
| 0x98/0x99 (SCON/SBUF), T1/T2 | UART | pty or TCP; ^R ^R reset, XMODEM to the #852 loader work as on the bench |
| T0, T2, IE/EIE/EXIF | timers, IRQs | from the core, plus the switch and NIC interrupt sources |
| 0x80, 0x9a-0x9e, 0xa9-0xaf, 0xb1/0xb2, 0xbc | flash controller | SPI-NOR model over a file (`src/sys/flash_dev.h`, MIT; written for RTLPlayground's host tests and kept here): erase granularity, page-program wrap, chip-size wrap, JEDEC ID from the board JSON |
| 0xa0-0xa7 | switch register handshake | `EXEC_GO` 1/3 = reg read/write (16-bit addr, 32-bit data) → register file; `EXEC_STATUS` reads 0 at once |
| 0x93/0x94 + EXEC 5/7 | SerDes | register store; link state from the PHY model |
| 0xc2-0xc5 + EXEC 9/11 | SMI / MDIO | PHY model: IDs, BMSR/link/speed from the board's port state |
| 0xb3-0xb7 | NIC ring | CPU-port frames in and out (see Layer 3) |
| others | — | stored, and logged once as "unmodelled" |

### Switch register file
16-bit address, 32-bit data. Default behaviour: plain storage, every access
traced. Registers that need behaviour get a handler:
- **Indirect table access** (`TBL_CTRL` and its data registers): VLAN table,
  L2 table, and any others, kept as real tables. The execute bit clears itself,
  and read-back is filled from the table.
- **Link and port status**: from the PHY model.
- **MIB counters** (`STAT_*`): from OVS port and flow stats.
- **Reset** (`REG_RESET`, chip reset): resets the CPU; flash and the PHY state persist.
- **Random number** (`RAND_NUM*`), the seconds counter.

**Boot-time state:** seed the register file from a register dump taken on
silicon. RTLPlayground has `regget`, so a CLI script on the bench board can do it.
Avoid clear-on-read registers. Registers the firmware polls for a bit are found
via the "unmodelled" log and given handlers.

Trace format (one line each, also from silicon's `REGDBG` build for diffing):
`R 0x1234 -> 0x00ff0001`, `W 0x1234 = 0x…`, `SDS/SMI/FLASH …`, with the C
function from the map file.

## Layer 3: fabric (OVS)

### Compiler: register state → flow set
A pure function of the register file, the tables and the board
(`src/fabric/compiler.h`). It produces a complete, readable flow set
(ovs-ofctl-like text, `rtlsim --print-flows`); golden tests check the text
for firmware configurations without OVS (`tests/fabric`). The controller
installs only the difference to what is installed (adds, strict deletes);
the plan was an OpenFlow bundle with replace-flows semantics, and that can
come back if a half-applied update ever shows.

Built (phase 4, Fabric I):
| Table | Job | From |
|---|---|---|
| 0 classify | accepted frame types; the PVID pushed as a tag on untagged and priority-tagged frames; ingress VLAN filter (tagged: only the port's VLANs; untagged: only if the PVID's VLAN has the port); the port's isolation mask into metadata | `RTL837x_REG_INGRESS`, `VLAN_CTRL`, `VLAN_PORT_IGR_FLTR`, PVID regs, `PORT_ISOLATION`, VLAN table |
| 1 learn | (VID, src MAC) known on this in_port → on; else also a copy to the controller | L2 table |
| 2 L2 | known unicast → its port, if the isolation mask allows (else drop); the CPU's MAC → CPU | L2 table, untag mask |
| 3 flood | the VLAN's members the isolation mask allows: tagged ones and the CPU first, then `pop_vlan` and the untagged ones | VLAN table |

**Learning lives in the chip model, not in OVS's `learn()`.** A frame from an
unknown source reaches the controller from table 1. rtlsim learns it into
the chip's own L2 table, as the hardware would, and the next compile gives
it its flows. The learn flow's `idle_timeout` is the ageing time, and its
FLOW_REMOVED ages the entry out. So the firmware's `l2` command, which reads
the L2 table through `TBL_CTRL`, shows exactly what OVS forwards by, with no
conversion from flow dumps. The cost is the first frame of every new source
going through the controller. The CPU's own frames get the same lookup in
the NIC model, against the same tables.

Still to do (phase 5): STP port states and traps (`MSTP_STATES`, `RMA_*`),
LAG (`select` groups from `TRK_*`), mirroring, storm control and rate
limits as meters, multicast entries and IGMP, per-port learning off, the
egress tag mode (`VLAN_PORT_EGR_TAG`), counters from OVS stats. Unknowns: the
ageing time register (300 s, `--ageing S` for tests), and whether a full L2
table floods (it does here).

Note: `isolate P PORTS` writes exactly that mask, without the CPU port, so
port P can't reach the switch's own address afterwards. That's the
firmware's doing, and silicon presumably behaves the same; `isolate P off`
puts the CPU back.

### CPU port and NIC
- Frames to the CPU (traps, the CPU's MAC, floods that include the CPU) leave
  OVS as **packet-in**. The ingress port is in the metadata. rtlsim builds the
  NIC RX descriptor (with the source port) and raises the NIC interrupt.
- Frames the firmware sends: the TX header gives a destination port mask
  → **packet-out** with `output:` actions; or "normal lookup" → packet-out with
  `in_port=CONTROLLER, actions=resubmit(,0)`. **Verify** OVS accepts this
  in_port, otherwise use a reserved virtual port.
- No extra OVS port, tap or privilege is needed for the CPU.

So the switch's management IP is reachable from any front port, through the
fabric, as on silicon: ARP floods reach the CPU, the reply is a packet-out.
Reach it from the host by putting a veth or tap with an address in the same
subnet on a front port.

Until the fabric exists, `--nic PATH --nic-port N` wires one front port
straight to the CPU over a socket (`src/sys/nic.h`): every frame from it goes
to the CPU if its VLAN has the CPU, and the CPU's frames go to it if the
pmask or the VLAN says so, with the VLAN's untag mask applied.

### The CPU port's ring (RTLPlayground rtl837x_nic.c, doc/CpuPort.md)
The NIC has its own packet memory on the ASIC side, which the 8051 reaches
only through a DMA engine in SFRs: 0xb3/0xb4 the xdata address, 0xb5/0xb6
the ring address (bit 15 set: the TX area), 0xb7 the number of 8-byte words
(writing starts it, it reads 0 when done; a frame of up to 2040 bytes per call).
- **RX ring:** 0x500 slots of 8 bytes (0x0000-0x27ff, 10 KiB). Each frame is
  an 8-byte descriptor (seq, -, -, port in bits 3:0, length LE, -, -) and the
  frame, padded to 8; the hardware wraps a transfer that crosses the end.
  `NIC_RX_BUFF_DATA` (0x7874) is nonzero while frames wait,
  `CPU_RX_CURR_PKT` (0x787c) the current frame's slot, `NIC_RXCMD` (0x784c) =
  1 releases it. A frame arrives with the CPU tag after the MACs (0x8899,
  version 4, reason, flags, port) and an 802.1Q tag after that (the PVID if it
  came in untagged), so the EtherType is at offset 24.
- **TX area:** 0x7fe slots. `CPU_TX_CURR_PKT` (0x7890) is where to write; the
  firmware DMAs its descriptor (seq, 0x07 = offload the checksums, -, -, length
  LE, -, -) plus the frame there and writes `NIC_TXCMD` (0x7850) = 1. A frame
  with the CPU tag (EtherType 0x8899) goes out to its pmask (or, with ALLOW,
  is looked up and limited to it); any other frame is switched as if it came
  in on the CPU port.
- **Interrupt:** `NIC_INT_STS` (0x7854) bit 1 RXIS, write 1 to clear; mask
  `NIC_INT_MSK` (0x7858) bit 1. It drives INT1, level-triggered: the ISR masks
  EX1, the main loop drains up to 4 frames per pass and unmasks when the ring
  is empty.

**How fast.** The 8051 is a four-clock DW8051 at 125 MHz: 31.25 M machine
cycles/s, ~15 MIPS. The 10 KiB RX ring holds ~6 full-size or ~140 minimum
frames: at 2.5 Gb/s that is ~33 µs of traffic, so a flood towards the CPU
overflows it within one main-loop pass. Hence the traps and storm control.

Measured on rtlsim (`make test-net`, the SWTG024AS compact image against
Linux in a netns). The cost is the CPU's busy machine cycles from a frame
landing in the ring to the reply's `NIC_TXCMD`. In rtlsim DMA, register
handshakes and flash reads are instant, so silicon is somewhat slower:

| frame in → reply out | machine cycles | time |
|---|---|---|
| ARP request → reply | ~5,100 | 164 µs |
| ICMP echo → reply | ~4,550 | 146 µs |
| TCP SYN → SYN-ACK | ~6,300 | 203 µs |
| HTTP GET → first segment | ~275,000 | 8.8 ms |
| TCP ACK → next full segment (1460 B) | ~154,000 | 4.9 ms |

- Small frames: a few thousand a second, as estimated. Ping's RTT is 0.4-0.7 ms.
- uIP doesn't check checksums on input: the IPv4 header check is commented
  out and TCP has none; only a nonzero UDP checksum is checked. On output the
  ASIC fills in the IPv4 header, TCP and ICMP checksums (uIP turns an echo
  into its reply without fixing the ICMP checksum). The old estimate of
  ~0.5 ms per inbound segment for checksums was wrong.
- A served segment costs ~100 cycles a byte, nearly all in
  `flash_read_bulk()` (the web UI is gzipped in flash) and SDCC's
  generic-pointer `memcpy()`. That bounds TCP: app.js (53 KB) comes in at
  270-280 KiB/s, which is the CPU's speed. uIP keeps one segment in flight,
  but Linux ACKed every segment at once: wall time matched CPU time, with no
  delayed-ACK stalls in these transfers. The ~36 KB/s estimate assumed stalls
  that didn't happen.
- The web UI, after the login (0.04 s): `/` and `/app.js` (8 + 53 KB) in 0.24 s.
- Before RTLPlayground b0fcf85 the firmware never idled after its boot (see
  README.md, "Found"). With the fix these numbers are unchanged: the work
  per frame was real; only the spinning between frames went away.

Frames per reply come from `--trace-nic` (one line per frame; a TX line has
the busy cycles since the last RX).

### Bridge setup
Built so far: `--ofp PATH` (`src/net/openflow.h`). OpenFlow port numbers are
physical port + 1 (OpenFlow has no port 0), and the bridge is set up from
outside: by `tools/rtlswitch.py` from the board file, on a private
ovsdb-server and ovs-vswitchd (`tools/privovs.py`) run as root in a network
namespace of its own. rtlsim runs as the user; it doesn't need to reach
OVSDB.
OVS sees rtlsim's socket because UNIX sockets don't belong to a namespace.
The veth's host end needs `ethtool -K tx off`: the userspace datapath
passes on the partial checksums Linux leaves for offload.

The plan: rtlsim creates `sw-<name>` on start: `datapath_type=netdev`,
`fail_mode=secure`, `other-config:disable-in-band=true`, no STP/RSTP/LACP, and
controller `unix:/run/rtlsim/<name>.sock` (rtlsim listens). It adds the
front-panel ports the board JSON and the topology ask for, with OpenFlow port
numbers = logical port numbers. Initially through `ovs-vsctl`, later perhaps
through the OVSDB IDL.

Link up/down: `ovs-ofctl mod-port … up|down` or the peer's carrier → the PHY
model → the firmware's link interrupt/poll.

**Datapath is an option** (`--datapath netdev|system`). The controller doesn't
depend on it: flows, `learn()`, groups, meters, packet-in/out behave the same.
`netdev` (userspace) needs no kernel module or privileges, for CI and
containers. `system` (kernel) forwards in the kernel flow cache. Only the
first packet of a new flow, learning and CPU-port traffic go to userspace. Use
it for real deployments.

With `fail_mode=secure`, installed flows survive an rtlsim crash or restart:
the fabric keeps forwarding without its CPU, as silicon does when the 8051 hangs.

### Deployment: Proxmox virtual switch fabric
Proxmox uses OVS with the kernel datapath. An rtlsim bridge (`--datapath
system`) is a virtual switch you can swap with a real one. VM NICs and
physical NICs are its front ports. It has the same firmware, `config.txt`,
web UI/API and management IP as a silicon switch, so the homelab configs and
tooling don't change.
- VLANs come from the switch's config, not Proxmox's per-NIC `tag=` (OVSDB
  port tags only affect `NORMAL`, which is never used).
- To check: how qemu-server attaches a VM tap to an OVS bridge it didn't
  define. Either declare the bridge in `/etc/network/interfaces` and let rtlsim
  take it over at start, or have rtlsim create it before VMs start (systemd ordering).
- Not built for performance: multicast floods or learning storms make many
  upcalls. Fine for a homelab, unmeasured beyond that.

## Inputs

### Firmware image
The full flash image (512 KiB / 1 / 2 MiB) as the flash file's initial content,
or `--flash state.bin` to keep a persistent flash between runs (updates,
saved config, loader). Optional `--map`/`--cdb` for symbols and the bank checker.

### Board JSON
The hardware that is *not* in the image. The firmware carries its own `struct
machine` (port mapping, SFP pins, LEDs); the JSON describes what is actually
on the board, so a wrong machine file shows up as a misbehaving switch.
```json
{
  "name": "poe_2g040210gsm",
  "model": "MokerLink POE-2G040210GSM",
  "chip": "RTL8372",
  "uuid": "0x69253500", "lot": "0xcf000000",
  "flash": { "size": "2M", "jedec": "ef40" },
  "factory_mac_at": "0x1FC000",
  "reset_gpio": 48,
  "ports": [
    {"phys": 4, "front": "1"}, {"phys": 5, "front": "2"}, {"phys": 6, "front": "3"}, {"phys": 7, "front": "4"},
    {"phys": 8, "front": "5", "sfp": {"slot": 1, "detect_gpio": 38, "los_gpio": null, "i2c": [39, 40], "module": null}},
    {"phys": 3, "front": "6", "sfp": {"slot": 2, "detect_gpio": 37, "los_gpio": null, "i2c": [41, 40], "module": null}}
  ],
  "chips": [ {"type": "rtl8238b", "i2c": [47, 46], "addr": "0x20"} ]
}
```
It describes the hardware, not the firmware, so old and new firmware run on
the same file: the board data the firmware carries (`struct machine`, whose
form changes with firmware versions) is the firmware's business, and a wrong
one misbehaves as it would on silicon. `tools/mkboard.py` drafts the files
from RTLPlayground's machine sources: chipset, port map, SFP wiring, factory
MAC offset, reset pin, and the extra chips (RTL8224 PHYs on RTL8373 boards,
an RTL8238B where the init code loads one). The source doesn't give the
flash size, so a table in the tool does (2M assumed otherwise). The UUID and
lot come from the machine name, so boards don't share a MAC. `boards/` has a
draft for each machine. rtlsim reads `chip`, `revision`, `uuid`, `lot`,
`flash`, and `ports` with `phys`, `front` and `sfp`; `chips` waits for the
RTL8224 and RTL8238B models. The bench board (`swgt024_v2_0_unmanaged`) is
also built in. `--sfp SLOT=EEPROM.bin` inserts a
module (`tools/mksfp.py` writes one). `i2c_devices`
covers later additions: SFP EEPROMs (module dumps), and the POE-2G040210GSM's
RTL8238B as a stub.

### Topology file (tests, several switches)
```yaml
switches:
  a: { image: build/a.bin, board: boards/swgt024_v2_0_unmanaged.json }
  b: { image: build/b.bin, board: boards/poe-2g040210gsm.json }
links:
  - [a.SFP1, b.SFP1]          # patch ports
  - [a.4, b.4]
hosts:
  h1: { port: a.1, ip: 192.168.10.11/24 }       # netns + veth
  h2: { port: b.2, vlan: 10, ip: 10.0.10.2/24 }
  wrt: { port: a.3, qemu: openwrt-x86.img }     # optional VM peer
```

## Testing

- **Unit:** core ISA (SDCC regressions vs ucsim), bank mapping, each SFR
  peripheral, compiler goldens (register state in → flow text out).
- **Boot:** the boot log matches silicon logs line for line (the bench logs in
  `~/rtlpg-bench`); the bank checker stays silent; the "unmodelled" list is reviewed.
- **Register traces:** run a CLI script on the sim and on silicon (`REGDBG`) and
  diff the traces.
- **Behaviour (pytest over topologies):** VLAN tagged/untagged/PVID/leaks; L2
  learn and ageing; isolation; LAG between two sims and against Linux bonding;
  STP rings of 3 with link pulls; LLDP neighbours; IGMP snooping; mirroring;
  rate limits (iperf3); web UI and API; firmware update, and power cuts in the
  flash model; loader XMODEM via `rtlctl.py` against the pty.
- **Hybrid:** add a physical NIC to a sim bridge and cable it to a bench switch.
  Run the same tests sim ↔ silicon. This is the calibration loop for the
  register semantics.

## Phases
1. **Core:** extract mcs51 + shim, dw8051 → rtl837x variant, banking, ISA tests.
2. **Boot to prompt:** UART pty, timers/IRQs, flash file, register handshake
   with the seeded register file, traces, bank checker. Done when the
   SWTG024AS image's boot log matches silicon and the CLI answers.
3. **On the wire:** OVS bridge + controller, ports from the board JSON, PHY
   link, NIC ↔ packet-in/out. Done when you can ping the switch IP and load the web UI.
4. **Fabric I:** VLAN, PVID, frame types, ingress filter, L2 learn/static/ageing,
   flooding, isolation. Done when the VLAN tests pass and match silicon traces.
5. **Fabric II:** STP states, traps, LAG, mirroring, meters/storm, counters, IGMP.
6. **Networks:** topology runner, multi-switch pytest, hybrid with the bench.

## Decisions to take
- **Language:** C++20 for rtlsim (the MAME core is C++). Debian ships no
  libopenvswitch-dev, so the controller speaks a minimal OpenFlow 1.3 itself
  (hello, echo, features, packet-in/out, bundles, flow-mods); flow text can
  also go through `ovs-ofctl`.
- **Licence:** MIT (as RTLPlayground, and the flash model, `flash_dev.c`) or Apache-2.0
  (OVS). MAME's mcs51 is BSD-3, compatible with both.
- **Repo:** github.com/sirmick/ovs-rtlpg. Developed on devel (the Incus CT on
  buzz: taps, network namespaces, KVM, userspace OVS; nothing on the host).

## Open questions (resolve on the bench)
- PSBANK=0 mapping, image header offset, exact interrupt vectors and sources.
- The 8051 clock (CKCON, timer reloads vs measured tick).
- NIC, modelled after `rtl837x_nic.c`; still unknown: whether the RX length
  counts the FCS (rtlsim leaves it out, and pads to 60 first), descriptor
  bytes 1-2 and 6-7, whether `NIC_RX_BUFF_DATA` counts slots or bytes,
  whether the ASIC fills in UDP checksums, whether TX DMA wraps.
- Which registers the boot polls, and with what expected values.
- The table-access protocol per table (`TBL_CTRL` encoding), flood-mask
  registers, mirroring registers.
- ~~Whether OVS accepts packet-out `in_port=CONTROLLER` + `resubmit`~~: it
  does (OVS 3.5, OpenFlow 1.3): the frame enters table 0 with
  `in_port=CONTROLLER` and runs through the pipeline, so "normal lookup"
  needs no reserved port.
