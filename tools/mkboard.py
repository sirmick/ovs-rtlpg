#!/usr/bin/env python3
"""mkboard.py - draft rtlsim board files from RTLPlayground's machine sources.

    tools/mkboard.py RTLPG MACHINE [-o boards/NAME.json] [--flash 512K|2M]
    tools/mkboard.py RTLPG --all [-d boards/generated]

Reads machine/MACHINE.h (MACHINE_MODEL, MACHINE_CHIPSET), machine/MACHINE.c
(often a symlink into machine/pcb/: the struct machine initialiser) and
machine/MACHINE_init.c, and writes what rtlsim needs to know about the
hardware (sys/board.h): the chip, the flash, the front ports and the SFP
cages' wiring, and the chips beside the switch chip.
- Ports: log_to_phys_port maps a hardware port (the index) to its front
  panel number (the value, as the CLI and web UI print it; 0: none).
  is_sfp gives a port's cage, 1-based, and sfp_port[cage - 1] its detect and
  LOS pins and its I2C bus (I2CBUS(SDA, SCL)).
- Chips: an RTL8373 board gets the RTL8224 PHYs the firmware's RTL8373 path
  drives; a board whose init code loads an RTL8238B gets it, at PSE_ADDR on
  the GPIO pins the code bit-bangs.
- Flash: the source doesn't say (61 boards put the factory MAC at 0x1FC000,
  the 512K ones included), so it comes from --flash, the KNOWN_FLASH table,
  or is assumed 2M (and the file says so).
- Identity: a UUID and lot made from the machine name, so two simulated
  boards don't share a MAC.
The result is a draft: check it, and edit what the source gets wrong.
"""
import argparse, hashlib, json, os, re, sys

KNOWN_FLASH = {"SWGT024_V2_0_UNMANAGED": "512K", "AMPCOM_WAM902_SWTG018AS": "2M", "POE_2G040210GSM": "2M"}
# The bench SWTG024AS's own identity, so its MAC stays 06:0f:00:c8:54:07
KNOWN_ID = {"SWGT024_V2_0_UNMANAGED": {"revision": "0x68", "uuid": "0xc8540700", "lot": "0x0f000000"}}


def strip_comments(s):
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", s, flags=re.S)


def gpio_numbers(rtlpg):
    pins = {}
    for m in re.finditer(r"#define\s+(GPIO\w+)\s+(0x[0-9a-fA-F]+|\d+)\b", open(os.path.join(rtlpg, "rtl837x_pins.h")).read()):
        n = int(m.group(2), 0)
        pins[m.group(1)] = None if n == 0xff else n	# GPIO_NA: not wired
    return pins


def field(src, name):
    m = re.search(r"\." + re.escape(name) + r"\s*=\s*([^,\n}]+(?:\([^)]*\))?)", src)
    return m.group(1).strip() if m else None


def array(src, name):
    m = re.search(r"\." + re.escape(name) + r"\s*=\s*\{([^}]*)\}", src)
    return [int(x, 0) for x in m.group(1).split(",") if x.strip()] if m else []


def board(rtlpg, machine, flash=None):
    mdir = os.path.join(rtlpg, "machine")
    h = open(os.path.join(mdir, machine + ".h")).read()
    c = strip_comments(open(os.path.join(mdir, machine + ".c")).read())
    init_path = os.path.join(mdir, machine + "_init.c")
    init = open(init_path).read() if os.path.exists(init_path) else ""
    pins = gpio_numbers(rtlpg)

    def pin(sym):
        if sym is None:
            return None
        if sym not in pins:
            raise SystemExit(f"mkboard: {machine}: unknown GPIO {sym}")
        return pins[sym]

    model = re.search(r'#define\s+MACHINE_MODEL\s+"([^"]*)"', h)
    chip = re.search(r"#define\s+MACHINE_CHIPSET\s+(\w+)", h)
    front, is_sfp = array(c, "log_to_phys_port"), array(c, "is_sfp")
    sfp = {}
    for m in re.finditer(r"\.sfp_port\[(\d)\]\.(\w+)\s*=\s*([^,\n(]+(?:\([^)]*\))?)\s*,", c):
        sfp.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    ports = []
    for hw, f in enumerate(front):
        if not f:
            continue
        p = {"phys": hw, "front": str(f)}
        slot = is_sfp[hw] if hw < len(is_sfp) else 0
        if slot:
            s = sfp.get(slot - 1, {})
            i2c = re.match(r"I2CBUS\(\s*(\w+)\s*,\s*(\w+)\s*\)", s.get("i2c", ""))
            p["sfp"] = {"slot": slot, "detect_gpio": pin(s.get("pin_detect", "GPIO_NA")),
                        "los_gpio": pin(s.get("pin_los", "GPIO_NA")),
                        "i2c": [pin(i2c.group(1)), pin(i2c.group(2))] if i2c else None, "module": None}
        ports.append(p)
    ports.sort(key=lambda p: int(p["front"]))

    chips = []
    if chip and chip.group(1) == "RTL8373":
        chips.append({"type": "rtl8224", "comment": "the RTL8373 path's PHYs; not modelled yet"})
    if "RTL8238" in init:
        addr = re.search(r"#define\s+PSE_ADDR\s+(0x[0-9a-fA-F]+)", init)
        sda = re.search(r"gpio_pin_test\((GPIO\d+_I2C_SDA\d)\)", init)
        scl = re.search(r"gpio_pin_test\((GPIO\d+_I2C_SCL\d)\)", init)
        chips.append({"type": "rtl8238b", "i2c": [pin(sda.group(1)) if sda else None, pin(scl.group(1)) if scl else None],
                      "addr": addr.group(1) if addr else None})

    size = flash or KNOWN_FLASH.get(machine)
    digest = hashlib.sha256(machine.encode()).digest()
    ident = KNOWN_ID.get(machine, {"uuid": "0x%02x%02x%02x00" % (digest[0], digest[1], digest[2]),
                                   "lot": "0x%02x000000" % (digest[3] | 1)})
    b = {"name": machine.lower(),
         "comment": f"Drafted by tools/mkboard.py from RTLPlayground machine/{machine}.{{h,c}}"
                    + (f" ({os.path.basename(os.path.realpath(os.path.join(mdir, machine + '.c')))})"
                       if os.path.islink(os.path.join(mdir, machine + ".c")) else "") + ".",
         "model": model.group(1) if model else machine,
         "chip": chip.group(1) if chip else "RTL8372",
         **ident,
         "flash": {"size": size or "2M", "jedec": "ef40"}}
    if not size:
        b["flash"]["comment"] = "assumed: the source doesn't say"
    mac = field(c, "mac_flash_offset")
    if mac:
        b["factory_mac_at"] = mac
    reset = field(c, "reset_pin")
    if reset and pin(reset) is not None:
        b["reset_gpio"] = pin(reset)
    b["ports"] = ports
    if chips:
        b["chips"] = chips
    return b


def dump(b):
    """JSON with one port a line, as the hand-written files were."""
    head = {k: v for k, v in b.items() if k not in ("ports", "chips")}
    s = json.dumps(head, indent=2)[:-2] + ',\n  "ports": [\n'
    s += ",\n".join("    " + json.dumps(p) for p in b["ports"]) + "\n  ]"
    if "chips" in b:
        s += ',\n  "chips": [\n' + ",\n".join("    " + json.dumps(c) for c in b["chips"]) + "\n  ]"
    return s + "\n}\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rtlpg")
    ap.add_argument("machine", nargs="?")
    ap.add_argument("-o", "--out")
    ap.add_argument("--flash")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("-d", "--dir", default="boards/generated")
    a = ap.parse_args()
    if a.all:
        os.makedirs(a.dir, exist_ok=True)
        names = sorted(f[:-2] for f in os.listdir(os.path.join(a.rtlpg, "machine"))
                       if f.endswith(".c") and not f.endswith("_init.c") and os.path.exists(os.path.join(a.rtlpg, "machine", f[:-2] + ".h")))
        for n in names:
            open(os.path.join(a.dir, n.lower() + ".json"), "w").write(dump(board(a.rtlpg, n)))
        print(f"mkboard: {len(names)} boards in {a.dir}")
        return
    if not a.machine:
        ap.error("MACHINE or --all")
    out = dump(board(a.rtlpg, a.machine, a.flash))
    if a.out:
        open(a.out, "w").write(out)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
