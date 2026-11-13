#!/usr/bin/env python3
"""mksfp.py - an SFP module's EEPROM (SFF-8472) for rtlsim --sfp.

    tools/mksfp.py OUT.bin [--speed 10g|1g] [--vendor V] [--model M]
                   [--serial S] [--no-ddm]

Writes 512 bytes: A0h (identity: type, encoding, rate, vendor, part number,
serial, with both checksums) and A2h (diagnostics: temperature, Vcc, TX
bias, TX and RX power). With --no-ddm it says it has no diagnostics and
only A0h is written (256 bytes).
"""
import argparse, struct

SPEEDS = {  # transceiver code byte 3 / 6, encoding, rate in 100 Mb/s, wavelength
    "10g": (0x10, 0x00, 0x06, 0x67, 850),   # 10GBASE-SR, 64B/66B, 10.3 Gb/s
    "1g": (0x00, 0x01, 0x01, 0x0d, 850),    # 1000BASE-SX, 8B/10B, 1.25 Gb/s
}


def field(s, n):
    return s.encode("ascii")[:n].ljust(n, b" ")


def a0(speed, vendor, model, serial, ddm):
    t3, t6, enc, rate, wl = SPEEDS[speed]
    e = bytearray(256)
    e[0:3] = b"\x03\x04\x07"                # SFP, function defined by serial ID, LC
    e[3], e[6] = t3, t6
    e[11], e[12] = enc, rate
    e[16], e[17] = 8, 30                    # OM2/OM3 reach, in 10 m
    e[20:36] = field(vendor, 16)
    e[37:40] = b"\x00\x90\x65"
    e[40:56] = field(model, 16)
    e[56:60] = field("1.0", 4)
    e[60:62] = struct.pack("!H", wl)
    e[63] = sum(e[0:63]) & 0xff
    e[65] = 0x1a                            # TX_DISABLE, TX_FAULT, LOS
    e[68:84] = field(serial, 16)
    e[84:92] = field("261113", 8)
    e[92] = 0x68 if ddm else 0x00           # DDM, internally calibrated, average power
    e[93] = 0xf0 if ddm else 0x00
    e[94] = 0x08 if ddm else 0x00           # SFF-8472 rev 12.3
    e[95] = sum(e[64:95]) & 0xff
    return e


def a2():
    e = bytearray(256)
    # live values (96-105): 35.5 C, 3.30 V, 6.0 mA, -2.2 dBm out, -4.0 dBm in
    e[96:106] = struct.pack("!hHHHH", int(35.5 * 256), 33000, 3000, 6026, 3981)
    e[110] = 0x00                           # status: TX on, no fault, light
    return e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--speed", choices=SPEEDS, default="10g")
    ap.add_argument("--vendor", default="RTLSIM")
    ap.add_argument("--model", default="SFP-10G-SR")
    ap.add_argument("--serial", default="SIM0001")
    ap.add_argument("--no-ddm", action="store_true")
    a = ap.parse_args()
    data = a0(a.speed, a.vendor, a.model, a.serial, not a.no_ddm)
    if not a.no_ddm:
        data += a2()
    open(a.out, "wb").write(data)


if __name__ == "__main__":
    main()
