#!/usr/bin/env python3
"""regnames.py - register names for traces, from RTLPlayground's rtl837x_regs.h.

    tools/regnames.py RTLPLAYGROUND/rtl837x_regs.h > build/regnames.inc

Emits {0xADDR, "NAME"}, for each RTL837X_* define with a 16-bit value (the
first name wins when several share an address). Without the header it emits
nothing, and traces show bare addresses.
"""
import re
import sys

seen = {}
try:
    text = open(sys.argv[1]).read()
except (IndexError, OSError):
    text = ""
for name, val in re.findall(r"^#define\s+(RTL837X_\w+)\s+(0x[0-9a-fA-F]+)\b", text, re.M):
    v = int(val, 16)
    if v <= 0xffff and v not in seen:
        seen[v] = name
for v, name in sorted(seen.items()):
    print(f'{{0x{v:04x}, "{name}"}},')
