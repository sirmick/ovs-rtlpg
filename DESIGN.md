# rtlsim — an RTL837x switch, simulated as an Open vSwitch bridge

Status: phases 1-2 built (see README.md); next the CPU-port NIC, then OVS.

## Goal

    rtlsim --image rtlplayground.bin --board swtg024as.json [--map rtlplayground.map]

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
| 0x80, 0x9a-0x9e, 0xa9-0xaf, 0xb1/0xb2, 0xbc | flash controller | SPI-NOR model over a file (port `RTLPlayground/test/flash_dev.c`, MIT): erase granularity, page-program wrap, chip-size wrap, JEDEC ID from the board JSON |
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
A pure function of the register file and tables. It produces a complete,
readable flow set (ovs-ofctl syntax text). The controller installs it with
replace-flows semantics in an OpenFlow bundle, so updates are atomic. Golden
tests can check the text without OVS running.

Pipeline (tables):
| Table | Job | From |
|---|---|---|
| 0 ingress | port enabled, STP state (drop / learn-only / forward), traps to CPU (BPDU, LACP, LLDP, IGMP/MLD, RMA config) | `MSTP_STATES`, `RMA_*`, `IPV4/6_*` |
| 1 classify | accepted frame types, add the PVID tag when untagged, ingress VLAN filter, VLAN → FID | `VLAN_PORT_*`, VLAN table |
| 2 learn | `learn()` into table 3 keyed on (FID, src MAC) → in_port; idle_timeout = ageing; skipped when learning is off on the port | `L2_CTRL`, `L2_LRN` |
| 3 L2 | learned + static entries (the firmware's L2 writes), the CPU's MAC → CPU | L2 table |
| 4 flood | unknown unicast / multicast / broadcast → the VLAN's member mask, minus the ingress port | VLAN table, flood masks |
| 5 egress | port isolation mask; LAG: one group per trunk (`select`, hash fields from `TRK_HASH`); per-port untag mask (strip or keep the tag); mirroring | `PORT_ISOLATION`, `TRK_*`, VLAN untag mask, mirror regs |

Storm control and ingress rate limits → OpenFlow meters. Mirroring → output
to the mirror port, in the egress table or via OVS's `Mirror` table.

The firmware's view of learned entries (the `l2` command reads the L2 table
through `TBL_CTRL`) is answered from a flow dump of table 3, converted back to
the hardware entry format.

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
cycles/s, ~15 MIPS. A register handshake is ~30-40 cycles (~1 µs), so the
ring bookkeeping of one RX frame (two register reads, two DMAs, the release)
is ~5-10 µs; the frame's protocol work dominates. uIP verifies checksums in
software, ~10-15 cycles a byte, so a full TCP segment costs ~0.5-0.7 ms before
the application sees it; TX checksums are offloaded. Estimates: a few
thousand small frames/s (ARP, ICMP), and TCP well under the wire's speed: uIP
keeps one segment in flight (uip-split is linked but never called), so a
peer's delayed ACK can hold it to one segment per ACK timeout (Linux: ≥40 ms,
~36 KB/s) where quick-ACK doesn't kick in. The 10 KiB RX ring holds ~6
full-size or ~140 minimum frames: at 2.5 Gb/s that is ~33 µs of traffic, so a
flood towards the CPU overflows it within one main-loop pass. Hence the traps
and storm control. In rtlsim the DMA and handshakes are instant and the
firmware's cost is exact in machine cycles, so these numbers can be measured
per frame instead of estimated.

### Bridge setup
rtlsim creates `sw-<name>` on start: `datapath_type=netdev`,
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
  "name": "swtg024as",
  "chip": "RTL8372",
  "cpu_clock_hz": null,
  "flash": { "jedec": "ef4013", "size": "512K" },
  "factory": { "mac": "1c:2a:a3:00:00:01" },
  "ports": [
    { "phys": 4, "front": "1", "phy": "internal-2g5", "smi": 0 },
    { "phys": 5, "front": "2", "phy": "internal-2g5", "smi": 1 },
    { "phys": 6, "front": "3", "phy": "internal-2g5", "smi": 2 },
    { "phys": 7, "front": "4", "phy": "internal-2g5", "smi": 3 },
    { "phys": 3, "front": "SFP1", "sds": 1, "sfp": { "detect_gpio": 30, "los_gpio": 37, "i2c": [39, 40], "module": "10g-sr.bin" } },
    { "phys": 8, "front": "SFP2", "sds": 0, "sfp": { "detect_gpio": 50, "los_gpio": 51, "i2c": [41, 40], "module": null } }
  ],
  "gpio": { "reset": null },
  "i2c_devices": []
}
```
A tool can draft the JSON from an RTLPlayground machine `.c`. `i2c_devices`
covers later additions: SFP EEPROMs (module dumps), and the POE-2G040210GSM's
RTL8238B as a stub.

### Topology file (tests, several switches)
```yaml
switches:
  a: { image: build/a.bin, board: boards/swtg024as.json }
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
- **Licence:** MIT (RTLPlayground, whose `flash_dev.c` we port) or Apache-2.0
  (OVS). MAME's mcs51 is BSD-3, compatible with both.
- **Repo:** github.com/sirmick/ovs-rtlpg. Developed on devel (the Incus CT on
  buzz: taps, network namespaces, KVM, userspace OVS; nothing on the host).

## Open questions (resolve on the bench)
- PSBANK=0 mapping, image header offset, exact interrupt vectors and sources.
- The 8051 clock (CKCON, timer reloads vs measured tick).
- NIC ring / descriptor format and the TX header (`rtl837x_nic.c` is the spec).
- Which registers the boot polls, and with what expected values.
- The table-access protocol per table (`TBL_CTRL` encoding), flood-mask
  registers, mirroring registers.
- Whether OVS accepts packet-out `in_port=CONTROLLER` + `resubmit` for "normal lookup".
