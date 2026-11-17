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

## Measured on silicon (2026-11-16)

`tests/net/hardware/mc_oracle.py` on switch-bench (RTL8372) and moker81
(RTL8373), both on the pre-#904 IGMP code; `tests/net/test_igmp.py` holds
rtlsim to the same table. What it showed, and what the model does about it:

- **Snooping prunes.** After a member's IGMPv2 report, a group stream reaches
  the members only; after the last leave it reaches nobody. The report itself
  is not forwarded (the pre-#904 handler sends nothing on).
- **Unregistered groups drop** with `igmp on` (`IPV4_PORT_MC_LM_ACT` = 01 per
  port), **except 224.0.0.0/24**, which still floods. Modelled as table 2
  flows: per ingress port `nw_dst=224.0.0.0/4` drop (prio 110), and
  224.0.0.0/24 to the flood table above it (115). `IPV4_UNKN_MC_FLD_PMSK`
  (flood action 00) and the router-port action (11) are not modelled.
- **A trapped IGMP frame keeps its ingress tagging.** An untagged report reaches
  the CPU with no 802.1Q tag; a VID 1 or priority-tagged one keeps a tag, and
  the pre-#904 handler (which reads the EtherType right after the CPU tag)
  ignores it. Every other frame to the CPU gets the tag. Modelled: table 0
  marks an untagged arrival (metadata bit 10), the trap flow for those pops
  the tag first (prio 151), and the NIC takes an untagged packet-in as it is,
  with only the CPU tag and no VLAN check.
- **L3MC table access.** A key lookup (method 0) finds the entry, at a hash
  index; index reads with methods 1 and 2 return it; the next-valid walk
  (method 3, the `l2` command) skips L3MC entries. A lookup is exact-key:
  (S,G) misses when only (*,G) exists, though a stream from S still forwards
  by (*,G). On a miss DATA_OUT A/B hold the key. All modelled in
  `switch_tables` (the method-0 path used to decode the key as a MAC and never
  found an L3MC entry).
- **Forwarding is (\*,G)-only.** With both a (\*,G) and an (S,G) entry for a
  group in the table, a stream whose source matches the (S,G) entry still
  forwards to the (\*,G) members, not the (S,G) ones (switch-bench, 2026-11-17,
  tools/mcprobe-style poke of both entries + stream on three ports). So the
  forwarding lookup keys on (SIP=0, DIP); an (S,G) entry sits in the table but
  never forwards. The firmware only programs SIP=0, so this matches it. The
  compiler skips SIP!=0 entries for the forwarding flow (was speculatively
  giving them priority). Confirmed identical on rtlsim (veths) after the fix.
- Not measured yet: two VLANs (the entry has no VLAN field), IGMPv3, and
  #904's forwarding (build #904 and rerun the oracle).

## The oracle for correct snooping: a Linux bridge (2026-11-16)

Silicon says what the chip does with a register state; it can't say whether
the firmware's snooping decisions are right. For that the reference is the
Linux bridge's IGMP snooping (RFC 4541): `tests/net/test_igmp.py IMAGE
--oracle linux` runs `mc_oracle.run_router` on a bridge in a netns
(`tests/net/linux_bridge.py`), then on the firmware in rtlsim, on the same
four veths, and fails every step where the firmware differs. The scenario has
a sender S, members M and O that behave as IGMPv2 hosts (`mc_oracle.Host`:
they answer queries), and a multicast router / querier R that sends a general
query and, after a leave, the two group-specific queries RFC 2236 asks for;
at the end M vanishes without a leave. The membership interval is shortened
to 30 s so ageing is seen.

What Linux does (default bridge settings, mcast_snooping 1, not a querier):
- until it has seen a querier and the query's response time has passed, it
  floods all multicast (so no host misses a group nobody can refresh);
- then: unregistered groups and a group's stream also go to the router port;
  reports go only to the router port; 224.0.0.x floods;
- a leave prunes the port only after the querier's group-specific queries go
  unanswered; a general query is answered and the membership kept;
- a membership nobody refreshes ages out after the membership interval.

RTLPlayground #904 (e963975) against it - its control plane matches (queries
flooded, reports and re-reports only to the querier port, GSQs flooded). The
differences:
1. **The router port gets no multicast data**: neither a group's stream nor
   unregistered groups (RFC 4541 2.1.2: they must also go to router ports).
   Matters for routed multicast (a router behind the querier port).
2. **No ageing**: a member that vanishes keeps its group forever.
3. **Immediate leave**: the port is pruned on the leave itself, not after the
   querier's last-member queries (fast leave) - wrong for two hosts behind one
   port. (Linux floods the leave, to 224.0.0.2; #904 sends it to the router
   port only, which RFC 4541 prefers - not counted against #904.)
4. **No flooding before a querier is known** - a Linux policy, not required by
   RFC 4541.
Pre-#904 firmware fails the control plane too (forwards no IGMP at all).

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
