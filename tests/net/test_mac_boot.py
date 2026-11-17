#!/usr/bin/env python3
"""test_mac_boot.py - a `mac` line in the startup config must not leave the
switch deaf (logicog/RTLPlayground #818).

    tests/net/test_mac_boot.py IMAGE [--board FILE] [--rtlsim build/rtlsim] [--port 4]

IMAGE must carry a `mac` line in its startup config, e.g.
    make MACHINE=... CONFIG=cfg.txt   (cfg.txt: ip/gw/netmask plus "mac 8c:a6:82:70:9b:07")

Broadcast ARP arrives on a front port while the firmware boots, as from a
chatty PC. Before #880 the linker put parse_mac()'s spilled `__bit again` on
rx_irq (bit 0x01): a frame latched during the boot (isr_ext1 masks EX1) was
lost when the config's `mac` line ran, EX1 never came back, and the switch
received nothing until a reboot. Then it answers no ARP, also not after a live
`mac` change. Measured: 7957081 and dfe0c1f fail, main 6496638 passes.
No sudo needed: the peer is this script on rtlsim's --nic socket.
"""
import argparse, os, socket, struct, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT, DEFAULT_IP, switch_ip

PEER_MAC = bytes.fromhex("020000818001")
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def arp_request(target_ip):
    ip = bytes(int(x) for x in target_ip.split("."))
    f = (b"\xff" * 6 + PEER_MAC + b"\x08\x06" + bytes.fromhex("0001080006040001")
         + PEER_MAC + ip[:3] + b"\x01" + b"\x00" * 6 + ip)
    return f + b"\x00" * (60 - len(f))


class Peer:
    """The far end of rtlsim's --nic socket (QEMU stream framing)."""
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for _ in range(400):
            try:
                self.s.connect(path)
                break
            except OSError:
                time.sleep(0.005)
        self.s.setblocking(False)
        self.buf = b""

    def send(self, f):
        try:
            self.s.send(struct.pack("!I", len(f)) + f)
        except BlockingIOError:
            pass

    def arp_replies(self):
        try:
            while True:
                d = self.s.recv(65536)
                if not d:
                    break
                self.buf += d
        except BlockingIOError:
            pass
        n = 0
        while len(self.buf) >= 4 and len(self.buf) >= 4 + struct.unpack("!I", self.buf[:4])[0]:
            ln = struct.unpack("!I", self.buf[:4])[0]
            f, self.buf = self.buf[4:4 + ln], self.buf[4 + ln:]
            n += f[:6] == PEER_MAC and f[12:14] == b"\x08\x06" and f[20:22] == b"\x00\x02"
        return n


def answered(peer, ip, tries=10):
    peer.arp_replies()
    got = 0
    for _ in range(tries):
        peer.send(arp_request(ip))
        time.sleep(0.1)
        got += peer.arp_replies()
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    ap.add_argument("--port", type=int, default=4)
    a = ap.parse_args()

    sock = os.path.join(tempfile.mkdtemp(prefix="rtlsim-macboot-"), "nic.sock")
    extra = ["--nic", sock, "--nic-port", str(a.port)] + (["--board", a.board] if a.board else [])
    c = Console(a.rtlsim, a.image, extra)
    try:
        peer = Peer(sock)
        # ARP every 2 ms until the prompt, so frames land during the boot
        end = time.time() + 30
        while time.time() < end and not c.expect(PROMPT, 0.002):
            peer.send(arp_request(DEFAULT_IP))
        check(PROMPT in c.log, "boots to the prompt with ARP arriving during the boot")
        if b"MAC set to" not in c.log:
            print("FAIL  the image's startup config has no mac line; build it with one (see the docstring)")
            return 1
        ip = switch_ip(c)
        n = answered(peer, ip)
        check(n >= 8, f"after a boot that applied the config's mac line, {ip} answers ARP ({n}/10)")
        c.send("mac 02:00:00:81:80:99")
        c.expect(PROMPT, 5)
        n = answered(peer, ip)
        check(n >= 8, f"and after a live mac change ({n}/10)")
    finally:
        c.close()
    print("test_mac_boot: " + ("ALL PASS" if not failures else f"{failures} FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
