"""flashlab.py - switches on flash files, power-ons and power-cut sweeps, for
the flash tests (test_powercut.py, test_migrate.py).

A Scenario is a starting flash (a whole chip) plus what the switch must look
like once it's done with it. power_on() boots the chip once, until the
prompt or a cycle budget. settle() powers on until the switch is up, or
dead (no prompt; or a power-on that leaves the flash as it found it, which
rtlsim being deterministic means every later one would too). verdict() says
whether the result is right. sweep() does that after a power cut at each of
a list of points, in parallel, and writes one line per cut as it finishes.
"""
import collections, concurrent.futures, os, shutil, subprocess, threading

SECTOR = 0x1000
BOOT_CYCLES = 1_200_000_000	# a boot reaches its prompt in ~250 M; an install and a boot in ~600 M
POWER_ONS = 4


class Scenario:
    def __init__(self, rtlsim, image, base, board=None):
        self.rtlsim, self.image, self.board = rtlsim, image, board
        self.base = base			# the starting flash, a file
        self.checks = []			# (what, function(flash bytes) -> bool)
        self.messages = []			# (what, function(console of all power-ons) -> bool)

    def expect(self, what, test):
        self.checks.append((what, test))

    def expect_console(self, what, test):
        self.messages.append((what, test))

    def run(self, flash, *args, cycles=BOOT_CYCLES):
        cmd = [self.rtlsim, "--flash", flash, "--image", self.image, "--cycles", str(cycles), "--quiet", *args]
        if self.board:
            cmd += ["--board", self.board]
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
        return r.returncode, r.stdout.decode(errors="replace").replace("\r", "")

    @staticmethod
    def prompted(out):
        last = out.rfind("Software version:")
        return last >= 0 and "\n> " in out[last:]

    def settle(self, flash):
        """Power-ons until the prompt: ("up" | "dead", power-ons, console)."""
        console = ""
        for n in range(POWER_ONS):
            before = open(flash, "rb").read()
            rc, out = self.run(flash)
            console += out
            if self.prompted(out):
                return "up", n + 1, console
            if open(flash, "rb").read() == before:
                break
        return "dead", n + 1, console

    def verdict(self, flash, console):
        d = open(flash, "rb").read()
        why = [w for w, t in self.checks if not t(d)] + [w for w, t in self.messages if not t(console)]
        return "up" if not why else "bad: " + ",".join(why)

    def outcome(self, flash):
        state, n, console = self.settle(flash)
        return (self.verdict(flash, console) if state == "up" else "dead"), n


def ranges(xs):
    out = []
    for x in sorted(xs):
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return ", ".join(f"{s}" if s == e else f"{s}-{e}" for s, e in out)


def ops_of(scenario, workdir):
    """The flash operations of a run from the starting flash to the prompt."""
    f = os.path.join(workdir, "dry.bin")
    shutil.copy(scenario.base, f)
    log = os.path.join(workdir, "ops.txt")
    scenario.run(f, "--flash-ops", log)
    return [l.split() for l in open(log)], f


def sweep(scenario, ops, cuts, workdir, jobs, listing=None):
    """{cut: (result, power-ons)}; one line per cut to `listing` as it's done."""
    lock = threading.Lock()
    out = open(listing, "w") if listing else None

    def one(i):
        f = os.path.join(workdir, f"cut{i}.bin")
        shutil.copy(scenario.base, f)
        rc, _ = scenario.run(f, "--powercut-after", str(i))
        res = scenario.outcome(f) if rc == 5 else (f"bad: no cut (rc {rc})", 0)
        os.remove(f)
        if out:
            with lock:
                out.write(f"{i} {'erase' if ops[i][0] == 'E' else 'program'} 0x{ops[i][1]} {res[0].replace(' ', '')}\n")
                out.flush()
        return i, res

    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        results = dict(pool.map(one, cuts))
    if out:
        out.close()
    return results


def sampled(ops, lo=0, hi=None, every=False):
    """Cut points: every erase and every program starting a 512-byte block."""
    hi = len(ops) if hi is None else hi
    return [i for i, o in enumerate(ops) if lo <= i <= hi and (every or o[0] == "E" or int(o[1], 16) % 512 == 0)]


def by_sector(results, ops):
    s = collections.defaultdict(collections.Counter)
    for i, (r, _) in results.items():
        s[int(ops[i][1], 16) & ~(SECTOR - 1)][r.split(":")[0]] += 1
    return s
