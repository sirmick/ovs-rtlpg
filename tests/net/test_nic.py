#!/usr/bin/env python3
"""test_nic.py - the CPU port's NIC with hand-made frames on the --nic socket.

    tests/net/test_nic.py IMAGE [--rtlsim build/rtlsim]

No Linux stack and no privileges: frames go straight onto the socket. Checks
ARP, ICMP echo and a TCP SYN to the web server get answers with the right
addresses and checksums (the NIC fills in what the firmware leaves to the
ASIC), that VLAN 1 replies leave port 1 untagged, and that a frame in a VLAN
without the CPU never reaches it.
"""
import argparse, os, socket, struct, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT

PEER_MAC = bytes.fromhex("020000000001")
SWITCH_MAC = bytes.fromhex("060f00c85407")
PEER_IP, SWITCH_IP = bytes([192, 168, 2, 1]), bytes([192, 168, 2, 2])
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def csum(b):
    if len(b) & 1:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    while s >> 16:
        s = (s & 0xffff) + (s >> 16)
    return s


def ipv4(proto, payload):
    h = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, 0, 64, proto, 0, PEER_IP, SWITCH_IP)
    h = h[:10] + struct.pack("!H", 0xffff ^ csum(h)) + h[12:]
    return h + payload


def eth(dst, ethertype, payload, vlan=None):
    tag = struct.pack("!HH", 0x8100, vlan) if vlan is not None else b""
    return dst + PEER_MAC + tag + struct.pack("!H", ethertype) + payload


class Peer:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.buf = b""

    def send(self, f):
        self.s.sendall(struct.pack("!I", len(f)) + f)

    def frames(self, timeout):
        """Frames for the peer until `timeout` passes without one."""
        self.s.settimeout(timeout)
        while True:
            while len(self.buf) >= 4 and len(self.buf) >= 4 + struct.unpack("!I", self.buf[:4])[0]:
                n = struct.unpack("!I", self.buf[:4])[0]
                f, self.buf = self.buf[4:4 + n], self.buf[4 + n:]
                yield f
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                return
            if not d:
                return
            self.buf += d

    def reply(self, ethertype, match=lambda f: True, timeout=1.0):
        for f in self.frames(timeout):
            if f[12:14] == struct.pack("!H", ethertype) and match(f):
                return f
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "nic.sock")
        c = Console(a.rtlsim, a.image, ["--nic", sock, "--nic-port", "4"])
        try:
            if not c.expect(PROMPT, 30):
                check(False, "boots to the prompt")
                return 1
            p = Peer(sock)

            arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, PEER_MAC, PEER_IP, b"\0" * 6, SWITCH_IP)
            p.send(eth(b"\xff" * 6, 0x0806, arp))
            r = p.reply(0x0806)
            check(r is not None and r[:6] == PEER_MAC and r[6:12] == SWITCH_MAC and r[20:22] == b"\0\2"
                  and r[22:28] == SWITCH_MAC and r[28:32] == SWITCH_IP,
                  "ARP: the switch answers with its MAC")

            p.send(eth(b"\xff" * 6, 0x0806, arp, vlan=1))
            r = p.reply(0x0806)
            check(r is not None and len(r) == 60, "ARP tagged with VLAN 1: the reply leaves port 1 untagged")

            p.send(eth(b"\xff" * 6, 0x0806, arp, vlan=10))
            r = p.reply(0x0806, timeout=0.3)
            check(r is None, "ARP tagged with VLAN 10 (no such VLAN): the CPU never sees it")

            icmp = struct.pack("!BBHHH", 8, 0, 0, 0x1234, 1) + b"rtlsim" * 8
            icmp = icmp[:2] + struct.pack("!H", 0xffff ^ csum(icmp)) + icmp[4:]
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(1, icmp)))
            r = p.reply(0x0800, lambda f: f[23] == 1)
            ok = r is not None
            if ok:
                ip, ihl = r[14:], (r[14] & 0xf) * 4
                body = ip[ihl:struct.unpack("!H", ip[2:4])[0]]
                ok = (csum(ip[:ihl]) == 0xffff and body[0] == 0 and csum(body) == 0xffff
                      and body[8:] == b"rtlsim" * 8 and ip[12:16] == SWITCH_IP)
            check(ok, "ICMP echo: a reply with valid IP and ICMP checksums")

            syn = struct.pack("!HHIIBBHHH", 40000, 80, 1000, 0, 5 << 4, 0x02, 4096, 0, 0)
            pseudo = PEER_IP + SWITCH_IP + struct.pack("!BBH", 0, 6, len(syn))
            syn = syn[:16] + struct.pack("!H", 0xffff ^ csum(pseudo + syn)) + syn[18:]
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, syn)))
            r = p.reply(0x0800, lambda f: f[23] == 6)
            ok = r is not None
            if ok:
                ip, ihl = r[14:], (r[14] & 0xf) * 4
                tcp = ip[ihl:struct.unpack("!H", ip[2:4])[0]]
                pseudo = SWITCH_IP + PEER_IP + struct.pack("!BBH", 0, 6, len(tcp))
                ok = (tcp[13] & 0x12) == 0x12 and struct.unpack("!I", tcp[8:12])[0] == 1001 \
                    and csum(pseudo + tcp) == 0xffff and csum(ip[:ihl]) == 0xffff
            check(ok, "TCP SYN to port 80: a SYN-ACK with valid checksums")
        finally:
            c.close()

    print(f"test_nic: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
