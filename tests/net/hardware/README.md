# Hardware proofs for the uIP CVE fixes

The simulator tests next door (`../test_cve_2020_17437.py`, `../test_uip_hardening.py`)
feed crafted frames to the firmware's CPU-port NIC and prove the two uIP bugs
in rtlsim. These are the **hardware** analogs: they send the same frames to a
real RTL837x switch over the LAN, to answer "was this reproduced on hardware?"

Reproduced on a MokerLink POE-2G040210GSM (RTL8372) running an unpatched build:

- **`cve_urg.py SWITCH_IP [PEER_IP]`** — CVE-2020-17437, the TCP urgent-pointer
  out-of-bounds. Opens a connection to the web server and sends one 1-byte
  segment with URG set and urgent pointer one past the payload. Unpatched, the
  switch acknowledges `rcv_nxt +65535` — data it never received. Non-destructive.
- **`dos_syn.py SWITCH_IP [PEER_IP]`** — the TCP-option-parsing lockup. Sends one
  crafted SYN (`01 03 FF 00`: NOOP, then an option whose length 255 wraps the
  8-bit option cursor). Unpatched, the single main loop never returns and the
  switch stops answering until it is **power-cycled**. Check liveness with
  `alive.sh SWITCH_IP` before and after.
- **`mss.py` / `optprobe.py`** — diagnostics used while reproducing #902: `mss.py`
  shows the CPU honors a tiny advertised MSS (so the ASIC does **not** strip TCP
  options), and `optprobe.py` sends an arbitrary option list and reports replies.

## Running

Needs root (raw frames) and a host on the switch's management L2 segment. The
peer is a spare IP the host kernel does **not** own, so the kernel never RSTs
the half-open connection; ARP for it is answered by the script. Set the
interface with `RTLHW_IFACE` (default `eth0`) and pass a free `PEER_IP`.

    sudo RTLHW_IFACE=eth0 python3 cve_urg.py 172.16.16.244 172.16.16.111
    sudo RTLHW_IFACE=eth0 python3 dos_syn.py 172.16.16.244 172.16.16.111

## Notes from reproducing on hardware

- The switch retransmits its SYN-ACK only a few times and then aborts, so the
  completing ACK must be sent the instant the SYN-ACK arrives (from the sniff
  callback), not after a sleep. A normal kernel socket does this in microseconds;
  a naive scapy script that sleeps first never completes the handshake.
- **#902 is intermittent**: the crafted SYN only hangs the switch when uIP has a
  free connection slot to take the SYN into the option parser; otherwise it is
  dropped harmlessly. Fire it a few times. Once it lands, the hang is hard
  (no watchdog recovery observed; power-cycle required).
- These only ever read or crash the target switch — never flash or reconfigure it.

## Multicast oracle

- **`mc_oracle.py SWITCH_IP SENDER MEMBER OTHER`** — what the chip does with
  IPv4 multicast under the firmware's IGMP snooping: igmp on/off, an
  unregistered group, link-local, IGMPv2 reports and leaves from two hosts,
  a stream after each, and the L3MC entry read back by key over `POST /cmd`
  (`tools/hwprobe.py`). Needs three NICs on three ports of the *same* switch,
  used as separate hosts (never bridged). The result is `SILICON` in the
  script; `../test_igmp.py` runs the same scenario on rtlsim against it.
  Leaves the switch with igmp off.

      sudo python3 mc_oracle.py 192.168.10.247 enp4s0 enp3s0 enp5s0
- **`mc_forward.py SWITCH_IP SENDER A B C`** — the forwarding behaviours the
  firmware never programs itself, by poking the ASIC directly: an (S,G) entry
  never forwards (IPMC is (\*,G)-only), the member mask is VLAN-scoped, and the
  four lookup-miss actions (flood to `IPV4_UNKN_MC_FLD_PMSK`, drop, trap, router
  port). Four NICs on one switch; `PHYS` gives their physical ports (default
  `5,4,6,7` = elder enp4/3/5/6 on switch-bench). Result table `SILICON`.

      sudo python3 mc_forward.py 192.168.10.247 enp4s0 enp3s0 enp5s0 enp6s0
