#!/usr/bin/env python3
"""rtlsim_ctl.py - drive rtlsim over its control socket (rtlsim --control).

The firmware only runs when told to, in firmware time (31.25 M machine cycles
a second), so a test sees the same thing every run. Times are integer
microseconds of firmware time (fmt_us() writes them as rtlsim does, ms.µs).
The protocol is in src/ctl/control.h.

    from rtlsim_ctl import Sim
    with Sim(image, board="boards/keeplink_kp_9000_9xhpml_v1_2.json", map=".../rtlplayground.map") as sim:
        sim.run_until_uart(r"\\n> $")           # booted: the prompt
        sim.uart_write("version\\r")
        sim.run_until_uart(r"\\n> $", max_ms=1000)
        for t, text, done in sim.uart_read(): ...
        ticks = sim.peek_sym("ticks")             # bytes, the symbol's size
        sim.run_until_pc("sched_pass_end")
        tasks = sim.read_struct("sched_task", [("state", "B"), ("next", "H")], count=8)

Sim(image, board=None, map=None, flash=None, args=(), rtlsim=..., stderr=None)
    starts rtlsim paused on a socket in a fresh temporary directory; on leaving
    the with block (or close()) it says quit, waits for it, and cleans up.
    flash: a flash file (rtlsim --flash); args: more rtlsim options.

Running (each returns the firmware time in µs it stopped at; the until
forms return None on timeout and raise Halted if the firmware halted):
    run(ms)                          advance by ms (float or "1.234" string)
    run_until_pc(loc, max_ms=None)   loc: symbol, 0x1234, "b2:0x6f00"
    run_until_uart(regex, max_ms=None)
        POSIX extended regex over the UART output since the last uart_read(),
        CRs removed; \\n \\r \\t \\xHH work, as in Python strings
    run_until(loc, op, value, max_ms=None, width=None)
        op "==" "!=" "<" ">" "&"; checked every 256 machine cycles (8.192 µs)
    time() -> (cycles, µs)

Memory: loc is "<space>:<addr or symbol>" or a symbol; spaces x (xdata),
i (idata), s (SFR), c (code, current bank), b<n> (code in bank n).
    sym(name) -> (space, addr, size)     from the .map; size is up to the next global
    peek(loc, n) -> bytes; peek_sym(name, n=None) -> bytes (n: the symbol's size)
    poke(loc, data)                       x, i and s
    read_struct(sym, fields, count=1, offset=0) -> dict, or a list of count dicts
        fields: [(name, struct format char)], packed and little-endian as SDCC
        lays them out ("B" u8, "b" s8, "H" u16, "h", "I" u32, "i", "3s" bytes ...)
    decode(fields, data, count=1)         the same over bytes you have

UART: uart_write(text or bytes) queues input; uart_read() -> [(µs, text,
complete)], each line stamped with the time its first byte went out, the last
possibly unfinished; uart_text() the same, joined.

Also: nic_inject(port, frame) -> bool, nic_capture(on), nic_read() ->
[(µs, port, bytes)], link(port, up, speed=None), checks() -> [(µs, kind,
text)], cov_reset(), cov_dump(path), reset(), powercut(), cmd(line) -> str,
block(line) -> [str] for anything else.
"""
import os, re, shutil, socket, struct, subprocess, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
RTLSIM = os.path.join(HERE, "../build/rtlsim")


class SimError(Exception):
    pass


class Halted(SimError):
    pass


def parse_us(t):
    """'2213.408' (ms.µs) -> 2213408"""
    ms, _, us = t.rstrip("+").partition(".")
    return int(ms) * 1000 + int(us or 0)


def fmt_us(us):
    return f"{us // 1000}.{us % 1000:03d}"


def _ms(ms):
    if isinstance(ms, str):
        return ms
    us = round(ms * 1000)
    return fmt_us(us)


def _escape(data):
    if isinstance(data, str):
        data = data.encode("latin-1")
    out = []
    for c in data:
        if c == 0x5c:
            out.append("\\\\")
        elif 0x20 <= c <= 0x7e:
            out.append(chr(c))
        else:
            out.append(f"\\x{c:02x}")
    return "".join(out)


def _unescape(s):
    return re.sub(r"\\(\\|x[0-9a-f]{2})", lambda m: "\\" if m.group(1) == "\\" else chr(int(m.group(1)[1:], 16)), s)


def _regex(pattern):
    """A Python-string regex for the wire: control characters as escapes,
    '/' escaped (it ends the regex)."""
    out = []
    for ch in pattern:
        c = ord(ch)
        if ch == "/":
            out.append("\\/")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif c < 0x20 or c > 0x7e:
            out.append(f"\\x{c:02x}")
        else:
            out.append(ch)
    return "".join(out)


def decode(fields, data, count=1):
    fmt = "<" + "".join(f for _, f in fields)
    size = struct.calcsize(fmt)
    recs = []
    for i in range(count):
        vals = struct.unpack_from(fmt, data, i * size)
        recs.append(dict(zip((n for n, _ in fields), vals)))
    return recs


class Sim:
    def __init__(self, image=None, board=None, map=None, flash=None, args=(), rtlsim=RTLSIM, stderr=None, timeout=600):
        self.dir = tempfile.mkdtemp(prefix="rtlsim-ctl-")
        self.path = os.path.join(self.dir, "ctl")
        cmd = [rtlsim, "--control", self.path]
        if image:
            cmd += ["--image", image]
        if flash:
            cmd += ["--flash", flash]
        if board:
            cmd += ["--board", board]
        if map:
            cmd += ["--map", map]
        cmd += list(args)
        self.argv = cmd
        self.p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        end = time.time() + 10
        while True:
            try:
                self.s.connect(self.path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if self.p.poll() is not None or time.time() > end:
                    self.close()
                    raise SimError(f"rtlsim didn't start: {' '.join(cmd)}")
                time.sleep(0.01)
        self.s.settimeout(timeout)
        self.f = self.s.makefile("r", encoding="latin-1", newline="\n")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if getattr(self, "f", None):
            try:
                self.cmd("quit")
            except (OSError, SimError):
                pass
            self.f.close()
            self.f = None
        self.s.close()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        shutil.rmtree(self.dir, ignore_errors=True)

    # --- the wire
    def _line(self):
        l = self.f.readline()
        if not l:
            raise SimError("rtlsim closed the socket")
        return l[:-1]

    def cmd(self, line):
        """One request; the reply after 'ok' ('' for a bare ok)."""
        self.s.sendall(line.encode("latin-1") + b"\n")
        r = self._line()
        if r == "ok":
            return ""
        if r.startswith("ok "):
            return r[3:]
        raise SimError(f"{line!r}: {r}")

    def block(self, line):
        n = int(self.cmd(line))
        return [self._line() for _ in range(n)]

    # --- time
    def time(self):
        cycles, t = self.cmd("time").split()
        return int(cycles), parse_us(t)

    def run(self, ms):
        r = self.cmd(f"run {_ms(ms)}").split()
        if r[0] == "halt":
            raise Halted(r[1])
        return parse_us(r[0])

    def _until(self, what, max_ms):
        r = self.cmd(f"run until {what}" + (f" max {_ms(max_ms)}" if max_ms is not None else "")).split()
        if r[0] == "halt":
            raise Halted(r[1])
        return parse_us(r[1]) if r[0] == "hit" else None

    def run_until_pc(self, loc, max_ms=None):
        return self._until(f"pc={loc}", max_ms)

    def run_until_uart(self, regex, max_ms=None):
        return self._until(f"uart=/{_regex(regex)}/", max_ms)

    def run_until(self, loc, op, value, max_ms=None, width=None):
        return self._until(f"{loc}{'/%d' % width if width else ''} {op} {value}", max_ms)

    # --- memory
    def sym(self, name):
        space, addr, size = self.cmd(f"sym {name}").split()
        return space, int(addr, 16), int(size)

    def peek(self, loc, n):
        return bytes.fromhex(self.cmd(f"peek {loc} {n}"))

    def peek_sym(self, name, n=None):
        if n is None:
            n = self.sym(name)[2]
        return self.peek(name, n)

    def poke(self, loc, data):
        self.cmd(f"poke {loc} {bytes(data).hex()}")

    def read_struct(self, sym, fields, count=1, offset=0):
        size = struct.calcsize("<" + "".join(f for _, f in fields))
        space, addr, _ = self.sym(sym)
        data = self.peek(f"{space}:{addr + offset * size:#x}", size * count)
        recs = decode(fields, data, count)
        return recs[0] if count == 1 else recs

    # --- UART
    def uart_write(self, data):
        if isinstance(data, bytes):
            self.cmd(f"uart write hex:{data.hex()}" if data else "uart write ")
        else:
            self.cmd("uart write " + _escape(data))

    def uart_read(self):
        out = []
        for l in self.block("uart read"):
            t, _, text = l.partition(" ")
            out.append((parse_us(t), _unescape(text), not t.endswith("+")))
        return out

    def uart_text(self):
        return "".join(text + ("\n" if done else "") for _, text, done in self.uart_read())

    # --- the rest
    def nic_inject(self, port, frame):
        return self.cmd(f"nic inject {port} {bytes(frame).hex()}") == "taken"

    def nic_capture(self, on=True):
        self.cmd(f"nic capture {'on' if on else 'off'}")

    def nic_read(self):
        out = []
        for l in self.block("nic capture read"):
            t, p, h = l.split()
            out.append((parse_us(t), int(p), bytes.fromhex(h)))
        return out

    def link(self, port, up=True, speed=None):
        self.cmd(f"link {port} {'up' if up else 'down'}" + (f" {speed}" if up and speed else ""))

    def checks(self):
        out = []
        for l in self.block("checks"):
            t, kind, text = l.split(" ", 2)
            out.append((parse_us(t), kind, _unescape(text)))
        return out

    def cov_reset(self):
        self.cmd("cov reset")

    def cov_dump(self, path):
        self.cmd(f"cov dump {path}")

    def reset(self):
        return parse_us(self.cmd("reset"))

    def powercut(self):
        return parse_us(self.cmd("powercut"))
