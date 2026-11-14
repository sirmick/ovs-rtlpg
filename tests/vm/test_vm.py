#!/usr/bin/env python3
"""test_vm.py - the switch VM (build/vm/rtlsw.qcow2) under QEMU, as CI would.

    tests/vm/test_vm.py [--image build/vm/rtlsw.qcow2] [--data build/vm/rtlsw-data.qcow2]

No root, no host networking: the VM's NICs are -netdev stream sockets this
test is the other end of, and the firmware's console is a socket too. Boots
twice on a copy of the data disk, with -fw_cfg putting a 10G module in SFP
cage 1. Checks the console answers, the module is found, the switch answers
ARP on front port 1 and floods a broadcast to port 2, a unicast to a MAC
learned on port 2 is switched there, and the second boot finds the flash the
first one left (its saved config: no "Written default config"). Uses KVM if
it can.
"""
import argparse, os, shutil, socket, struct, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../net"))
from test_nic import Peer, eth, ip_bytes, PEER_MAC, PEER_IP, SWITCH_IP, SWITCH_MAC
from console import DEFAULT_IP, peer_ip
import re

failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


class VM:
    def __init__(self, image, data, d, nics=2, conf=""):
        self.d = d
        self.con = os.path.join(d, "fw.sock")
        self.nic = [os.path.join(d, f"p{i + 1}.sock") for i in range(nics)]
        for sock in (self.con, *self.nic):	# a previous VM's: QEMU makes them anew
            if os.path.exists(sock):
                os.remove(sock)
        cmd = ["qemu-system-x86_64", "-m", "256", "-nographic", "-monitor", "none",
               "-drive", f"file={image},if=virtio,snapshot=on",
               "-serial", f"file:{os.path.join(d, 'linux.log')}",
               "-chardev", f"socket,id=fw,path={self.con},server=on,wait=on", "-serial", "chardev:fw"]
        if conf:	# QEMU refuses an empty string
            cmd += ["-fw_cfg", f"name=opt/rtlsw/conf,string={conf}"]
        if data:
            cmd += ["-drive", f"file={data},if=virtio"]
        for i, s in enumerate(self.nic):
            cmd += ["-netdev", f"stream,id=p{i},server=on,addr.type=unix,addr.path={s}",
                    "-device", f"virtio-net-pci,netdev=p{i}"]
        if os.access("/dev/kvm", os.R_OK | os.W_OK):
            cmd.insert(1, "-enable-kvm")
        self.p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open(os.path.join(d, "qemu.log"), "w"))
        for _ in range(100):
            if os.path.exists(self.con):
                break
            time.sleep(0.1)
        self.c = socket.socket(socket.AF_UNIX)
        self.c.connect(self.con)
        self.log = b""

    def expect(self, pat, timeout):
        end, start = time.time() + timeout, len(self.log)
        while time.time() < end:
            self.c.settimeout(max(0.1, end - time.time()))
            try:
                d = self.c.recv(65536)
            except socket.timeout:
                break
            if not d:
                break
            self.log += d.replace(b"\r", b"")
            if pat in self.log[start:]:
                return True
        return False

    def cli(self, cmd):
        start = len(self.log)
        self.c.sendall(cmd.encode() + b"\r")
        self.expect(b"\n> ", 10)
        return self.log[start:].decode(errors="replace")

    def close(self):
        self.p.terminate()
        self.p.wait()


def main():
    ap = argparse.ArgumentParser()
    top = os.path.join(HERE, "../..")
    ap.add_argument("--image", default=os.path.join(top, "build/vm/rtlsw.qcow2"))
    ap.add_argument("--data", default=os.path.join(top, "build/vm/rtlsw-data.qcow2"))
    a = ap.parse_args()
    if not shutil.which("qemu-system-x86_64") or not os.path.exists(a.image):
        print("vm: no qemu-system-x86_64 or no image (make vm), skipped")
        return 0
    d = tempfile.mkdtemp(prefix="rtlsw-vm-")
    data = os.path.join(d, "data.qcow2")
    shutil.copy(a.data, data)
    try:
        t = time.time()
        vm = VM(a.image, data, d, conf="SFP1=10g")
        up = vm.expect(b"\n> ", 90)
        check(up, f"the VM boots and the firmware reaches its prompt on ttyS1 ({time.time() - t:.0f} s)")
        if not up:
            print(open(os.path.join(d, "linux.log"), errors="replace").read()[-1500:])
            return 1
        first = vm.log
        vm.expect(b"Module: RTLSIM", 30)
        out = vm.cli("sfp")
        check("Slot 1 - Rate: 67" in out and "Slot 2 - empty" in out, "-fw_cfg SFP1=10g: a 10G module in cage 1")

        global SWITCH_IP, PEER_IP
        m = re.search(r"Current IP: ([\d.]+)", vm.cli("ip"))
        sip = m.group(1) if m else DEFAULT_IP
        SWITCH_IP, PEER_IP = ip_bytes(sip), ip_bytes(peer_ip(sip))
        print(f"switch IP {sip}, peer {peer_ip(sip)}")

        p1, p2 = Peer(vm.nic[0]), Peer(vm.nic[1])
        arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, PEER_MAC, PEER_IP, b"\0" * 6, SWITCH_IP)
        # the bridge forwards nothing until rtlsim's flows are in: ask until it answers
        t, r = time.time(), None
        while r is None and time.time() - t < 30:
            p1.send(eth(b"\xff" * 6, 0x0806, arp))
            r = p1.reply(0x0806, timeout=0.5)
        check(r is not None and r[6:12] == SWITCH_MAC, f"the switch answers ARP on front port 1 "
              f"(forwarding {time.time() - t:.1f} s after the prompt)")
        p1.send(eth(b"\xff" * 6, 0x0806, arp))
        check(p2.reply(0x0806, timeout=3) is not None, "the broadcast floods to front port 2")
        mac2 = bytes.fromhex("020000000002")
        p2.send(b"\x02\x00\x00\x00\x00\x99" + mac2 + b"\x88\xb5" + b"learn me" * 8)
        time.sleep(0.5)
        p1.send(eth(mac2, 0x88b5, b"from port 1" * 4))
        check(p2.reply(0x88b5, lambda f: b"from port 1" in f, timeout=3) is not None,
              "a unicast from port 1 to a MAC learned on port 2 is switched there")
        vm.close()

        vm = VM(a.image, data, d)
        up = vm.expect(b"\n> ", 90)
        why = [w for w, bad in (("first boot wrote the default config", b"Written default config to flash" not in first),
                                ("second boot found it", b"Written default config" in vm.log),
                                ("second boot's prompt", not up)) if bad]
        check(not why, "a second boot finds the flash the first left on the data disk" + (f"; not: {why}" if why else ""))
        if not up:
            print("  firmware console:", vm.log.decode(errors="replace")[-300:])
            print("  qemu:", open(os.path.join(d, "qemu.log"), errors="replace").read()[-500:])
        vm.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"test_vm: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
