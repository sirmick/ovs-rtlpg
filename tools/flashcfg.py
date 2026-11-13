#!/usr/bin/env python3
"""flashcfg.py - the saved config in a switch's flash file (rtlsim --flash).

    tools/flashcfg.py FLASH                          # print it
    tools/flashcfg.py FLASH --set CMD [--set CMD]... # replace it with these lines
    tools/flashcfg.py FLASH --file config.txt        # ... or with a file's

RTLPlayground keeps its config as CLI commands, one a line, in one 4 KiB
sector (CONFIG_START in layout.h: 0x7f000 compact, 0x70000 standard), and
runs them at boot. This edits it with the switch off, the way the web UI's
config upload would with it on. A flash file that doesn't exist yet is
created by booting rtlsim once with --image and --flash.
"""
import argparse, sys

CONFIG_START = {"compact": 0x7f000, "standard": 0x70000}
CONFIG_LEN = 0x1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("flash")
    ap.add_argument("--layout", choices=CONFIG_START, default="compact")
    ap.add_argument("--set", action="append", metavar="CMD")
    ap.add_argument("--file")
    a = ap.parse_args()
    at = CONFIG_START[a.layout]
    with open(a.flash, "r+b") as f:
        f.seek(at)
        sec = f.read(CONFIG_LEN)
        if len(sec) < CONFIG_LEN:
            sys.exit(f"flashcfg: {a.flash} is too small for the {a.layout} layout")
        if not a.set and not a.file:
            end = min(i for i in (sec.find(b"\0"), sec.find(b"\xff"), CONFIG_LEN) if i >= 0)
            sys.stdout.write(sec[:end].decode(errors="replace"))
            return
        text = open(a.file).read() if a.file else "".join(c + "\n" for c in a.set)
        data = text.encode()
        if len(data) >= CONFIG_LEN:
            sys.exit(f"flashcfg: the config is {len(data)} bytes, the sector {CONFIG_LEN}")
        f.seek(at)
        f.write(data + b"\0" * (CONFIG_LEN - len(data)))


if __name__ == "__main__":
    main()
