#!/usr/bin/env python3
"""
mkimage.py - pack an SDCC .ihx into an RTL837x flash image, laid out as
RTLPlayground's tools/imagebuilder.c does (and src/sys/system.h describes):

    flash[0:2]                 header 00 40
    flash[2 + a]               common code, CPU a = 0x0000-0x3ffd
    flash[(b-1)*0xc000 + a]    bank b, CPU a = 0x4000-0xffff (linked at b*0x10000 + a)

Code linked at 0x4000-0xffff without a bank counts as bank 1.

    tools/mkimage.py IN.ihx OUT.bin
"""
import sys


def read_ihx(path):
    mem, base = {}, 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(":"):
            continue
        rec = bytes.fromhex(line[1:])
        n, addr, typ, data = rec[0], (rec[1] << 8) | rec[2], rec[3], rec[4:4 + rec[0]]
        if typ == 0:
            for i, b in enumerate(data):
                mem[base + addr + i] = b
        elif typ == 4:
            base = ((data[0] << 8) | data[1]) << 16
        elif typ == 1:
            break
    return mem


def flash_addr(a):
    if a < 0x4000:
        if a >= 0x3ffe:
            sys.exit(f"mkimage: common code reaches {a:#x}; it must end below 0x3ffe")
        return 2 + a
    bank, off = (a >> 16) or 1, a & 0xffff
    if off < 0x4000:
        sys.exit(f"mkimage: {a:#x} is below a bank window")
    return (bank - 1) * 0xc000 + off


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    mem = read_ihx(sys.argv[1])
    out = {flash_addr(a): b for a, b in mem.items()}
    size = (max(out) + 1 + 0xfff) & ~0xfff
    img = bytearray(b"\xff" * size)
    img[0:2] = b"\x00\x40"
    for f, b in out.items():
        img[f] = b
    open(sys.argv[2], "wb").write(img)


if __name__ == "__main__":
    main()
