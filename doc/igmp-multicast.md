# IGMP snooping / multicast in rtlsim

Goal: model enough of the RTL837x multicast path that IGMP snooping (and
RTLPlayground PR #904's forwarding behaviour) is observable on the virtual
fabric — a crafted IGMP exchange across two cabled switches forwards queries,
tracks a querier port, and prunes a multicast stream to its members. Today the
fabric compiler skips all of this (`src/fabric/compiler.cpp`:
`// multicast entries: IGMP, later`), and the table engine doesn't even capture
the entries, so there is nothing to forward.

## What the firmware actually does (findings)

- **It uses L3 multicast, not L2.** `rtl837x_igmp.c` defines `IPMC_USES_L3MC`
  unconditionally, so multicast forwarding is keyed on **(source IP, dest IP)**
  with a port mask, not on the group MAC. The `mac[0] & 1` branch in the
  compiler is for the L2MC path this firmware never takes.

- **L3MC entries live in the ordinary L2 table (TBL type 4).** `entry_to_l3mc()`
  fills `TBL_DATA_IN_A/B/C` and commits with `TBL_CTRL` type `TBL_L2_UNICAST`
  (4) — the same table as unicast — but with a different word layout and a
  different valid bit.

- **Word layout of an L3MC entry** (from `entry_to_l3mc`, bytes MSB-first):
  - `A` = SIP[0..3]  (source IP; `0.0.0.0` = any-source, i.e. `(*,G)`)
  - `B` = `B0<<24 | dip[1]<<16 | dip[2]<<8 | dip[3]`, where
    `B0 = ((pmask & 3) << 6) | (dip[0] & 0xf) | 0x10`
    - `B[31:30]` = `pmask[1:0]`
    - `B[28]`    = **L3MC valid** (`0x10`) — *not* bit 29, which is the L2
      valid bit
    - `B[27:24]` = `dip[0] & 0xf` (low nibble; the full octet is `0xE0 | nibble`
      because IPv4 multicast is 224–239)
    - `B[23:0]`  = dip[1..3]
  - `C` = `0 | (igmp_asic & 1)<<16 | igmp_index<<8 | (pmask >> 2)`
    - `C[7:0]` = `pmask >> 2`
  - So the 10-bit **member mask** = `((B >> 30) & 3) | ((C & 0xff) << 2)`, and
    **DIP** = `{ 0xE0 | (B>>24 & 0xf), B>>16 & 0xff, B>>8 & 0xff, B & 0xff }`.

- **The sim gap.** `switch_tables::table_exec` (index write) sets
  `e->present = (DATA_IN_B >> 29) & 1`. L3MC sets bit 28, not 29, so the entry
  is dropped on the floor. `key()`/`port()` also decode every entry as a
  unicast MAC.

## Registers to model (switch_core)

The compiler must know snooping is on and where unknown multicast floods:

| reg | addr | meaning |
|---|---|---|
| `RTL837X_IGMP_PORT_CFG` | 0x52a0 + 4*p | per port: `IGMP_PROTOCOL_ENABLE`, `IGMP_FLOOD` vs `IGMP_TRAP` |
| `RTL837X_IGMP_ROUTER_PORT` | 0x529c | static router-port mask |
| `RTL837X_IGMP_TRAP_CFG` | 0x50bc | trap config |
| `RTL837X_IPV4_UNKN_MC_FLD_PMSK` | 0x5368 | flood mask for unknown IPv4 MC |
| `RTL837X_L2_CTRL` bit `LUT_IPMC_HASH` | 0x5350 | IPMC hashing on |

These are "unmodelled reg" today; switch_core just needs to retain their last
written value for `core.reg()` to read (the engine already stores most regs).

## Compiler extensions (fabric/compiler.cpp + flow/openflow)

1. **L3 match fields.** `struct flow` only matches eth src/dst, VLAN, in_port.
   Add `eth_type`, `ip_proto`, and `ipv4_src`/`ipv4_dst` (+ masks), and emit the
   OXM fields in `openflow.cpp`. This is the main plumbing.
2. **IGMP-trap flow.** When a port has `IGMP_TRAP`, match `eth_type=IP,
   ip_proto=2` → `CONTROLLER`, preserving `in_port` and the VLAN tag, at a
   priority above flood. That delivers queries/reports/leaves to the firmware,
   whose #904 handler then packet-outs the forwarded copies.
3. **IPMC-forwarding flow.** For each captured L3MC entry emit: match
   `eth_type=IP, ipv4_dst=DIP` (and `ipv4_src=SIP` when SIP≠0, else wildcard),
   in the entry's VLAN → output the member-mask ports (tag/untag per the VLAN
   entry), minus STP-blocked. Prunes the stream to members.
4. **Unknown-MC flood.** `RTL837X_IPV4_UNKN_MC_FLD_PMSK` → flood unmatched IPv4
   multicast to that mask (the firmware's default is all member ports).

STP: PR #904 excludes blocked ports; the member mask the firmware programs
already reflects that for CPU-driven sends, but the IPMC data flow should also
drop blocked ports (reuse whatever port-state source table 3 flood uses).

## Plan / phases

1. **switch_tables**: capture L3MC entries (valid on bit 28 or 29) and expose a
   decoder — `is_l3mc(e)`, `l3mc_sip/dip(e)`, `l3mc_mask(e)`. Leave unicast
   decoding untouched. *(started here)*
2. **switch_core**: retain the IGMP registers above.
3. **flow/openflow**: add the L3 match fields.
4. **compiler**: trap flow + IPMC forwarding flow + unknown-MC flood.
5. **tests**: fabric goldens for the new flows, and a 2-switch topo test with a
   querier guest and member guests that watches a stream get pruned — the first
   real multi-switch multicast test. Cross-check against `test_igmp.c`'s
   expectations in RTLPlayground.

Not silicon-timing-faithful, like the rest of rtlsim — it models *what* the
firmware programs the ASIC to forward, so the snooping decisions (PR #904) are
testable end to end even though the ASIC's own IGMP offload is not.
