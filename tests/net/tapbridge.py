#!/usr/bin/env python3
"""tapbridge.py - relay frames between a tap and an rtlsim --nic socket.

    tapbridge.py SOCKET TAP

The tap must exist (ip tuntap add dev TAP mode tap); run it in the network
namespace the tap is in, as root. Frames on the socket are QEMU -netdev
stream framed: a 4-byte big-endian length, then the frame.
"""
import fcntl, os, select, socket, struct, sys, time

TUNSETIFF = 0x400454ca
IFF_TAP, IFF_NO_PI = 0x0002, 0x1000


def main():
    path, tap = sys.argv[1], sys.argv[2]
    t = os.open("/dev/net/tun", os.O_RDWR)
    fcntl.ioctl(t, TUNSETIFF, struct.pack("16sH", tap.encode(), IFF_TAP | IFF_NO_PI))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    for _ in range(200):
        try:
            s.connect(path)
            break
        except OSError:
            time.sleep(0.05)
    else:
        sys.exit(f"tapbridge: cannot connect to {path}")
    buf = b""
    while True:
        r, _, _ = select.select([t, s], [], [])
        if t in r:
            f = os.read(t, 65536)
            s.sendall(struct.pack("!I", len(f)) + f)
        if s in r:
            d = s.recv(65536)
            if not d:
                return
            buf += d
            while len(buf) >= 4:
                n = struct.unpack("!I", buf[:4])[0]
                if len(buf) < 4 + n:
                    break
                os.write(t, buf[4:4 + n])
                buf = buf[4 + n:]


if __name__ == "__main__":
    main()
