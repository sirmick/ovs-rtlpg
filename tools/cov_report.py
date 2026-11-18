#!/usr/bin/env python3
"""cov_report.py - what ran, from rtlsim's coverage (--cov FILE, cov dump FILE).

    tools/cov_report.py DUMP --map M [--rst DIR] [--region auto|boot|loaded]
        [--all | --missed]                  functions: ran/total instructions, name, module
        [--lines FILE.c [--missed]]         FILE.c's lines that have code: ran or not
        [--require-lines FILE.c]            exit 1 unless every line of FILE.c with code ran
        [--require-functions NAME...]       exit 1 unless each of them ran
        [--diff DUMP2]                      functions that ran in one dump and not the
                                            other, or ran other instructions; exit 1 if any
        [--except FILE:LINE[-LAST]]...      leave lines out of --lines/--require-lines
        [--src DIR]...                      where the .c files are (for cov:ignore)

Lines that can't run by design (the epilogue under the "}" of a function
that never returns, a defensive branch) are left out of --lines and
--require-lines with --except FILE:LINE, or by "cov:ignore" in a comment on
the line in the source (/* cov:ignore: never returns */), the .c found
where the .rst cites it, under --src, else the map's directory or up to
three above it. --require-lines says which it left out.

DUMP's addresses are mapped through the SDCC relocated listings (*.rst) of
the objects the map's link took (its "Files Linked", found under --rst,
default the map's directory): each instruction there has its
final address (bank << 16 | address), the function SDCC put it in (its
"; function NAME" comment, statics too) and the C line before it (the
";\tfile.c:NN:" comment). A function ran if any of its instructions did; a
line, likewise. --map is the link's map: its directory is where to look,
and the coverage is the image it describes.

Regions (src/sys/coverage.h): "boot" is what ran from the common code as
reset loads it from flash, "loaded" what ran after software loaded it (the
loader's app_start()). On a boot image with RTLPlayground's loader (LOADER=1),
boot is the loader (give it the loader's map) and loaded the application;
on any other image, boot is the application. auto: loaded if anything ran
there, else boot.

As a module, for tests:

    from cov_report import Coverage
    cov = Coverage("boot.cov", "output/.../rtlplayground.map")
    cov.functions["sched_run"]       # (instructions ran, instructions, module)
    cov.ran_function("sched_run")    # True / False
    cov.lines("sched.c")             # {line: (ran, instructions, source)}
    cov.missed_lines("sched.c")      # [(line, source)] that have code and didn't run
    Coverage(..., excepts=["sched.c:120"], src_dirs=[...])  # as --except, --src
"""
import argparse, os, re, sys

INSN = re.compile(r"^\s+([0-9A-F]{6}) ((?:[0-9A-F]{2} )+)\s*\[\s*\d+\]\s+\d+\s(.*)$")
CLINE = re.compile(r"^\s+\d+ ;\s*([^\s:]+):(\d+): ?(.*)$")
FUNC = re.compile(r"^\s+\d+ ;\s+function (\S+)\s*$")


def load_dump(path):
    """{region: set of linear addresses}"""
    data = open(path, "rb").read()
    if data[:8] != b"RTLCOV1\n":
        raise ValueError(f"{path}: not an rtlsim coverage dump")
    out = {0: set(), 1: set()}
    p = 8
    while p < len(data):
        region, bank = data[p], data[p + 1]
        bits = data[p + 4:p + 4 + 8192]
        p += 4 + 8192
        base = bank << 16
        for i, b in enumerate(bits):
            while b:
                low = b & -b
                out[region].add(base | (i << 3) | (low.bit_length() - 1))
                b ^= low
    return out


def rst_files(top, map_path):
    """{module: its .rst}, for every object the map's link took ("Files
    Linked"): its path, as the link had it, found under `top` by dropping
    leading directories until it exists. A module is named by its basename,
    or, where two linked objects share one, by the path the link gave it."""
    found = {}
    lines = open(map_path, errors="replace").read().splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("Files Linked"))
    except StopIteration:
        return found
    for l in lines[start + 1:]:
        w = l.split()
        if w and w[0].endswith(".rel"):
            parts = w[0][:-4].split("/")
            for k in range(len(parts)):
                cand = os.path.join(top, *parts[k:]) + ".rst"
                if os.path.exists(cand):
                    found["/".join(parts)] = cand
                    break
        elif w and not w[0].startswith("[") and found:
            break		# the next section
    base = {}
    for path in found:
        base.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    return {(path.rsplit("/", 1)[-1] if len(base[path.rsplit("/", 1)[-1]]) == 1 else path): rst
            for path, rst in found.items()}


def load_listing(files):
    """[(address, module, function, file, line, source)] for every instruction"""
    insns = []
    for mod, path in sorted(files.items()):
        func = cfile = None
        cline = 0
        csrc = ""
        for l in open(path, errors="replace"):
            m = INSN.match(l)
            if m:
                if func:
                    insns.append((int(m.group(1), 16), mod, func, cfile, cline, csrc))
                continue
            m = FUNC.match(l)
            if m:
                func, cfile, cline = m.group(1), None, 0
                continue
            m = CLINE.match(l)
            if m:
                cfile, cline, csrc = m.group(1), int(m.group(2)), m.group(3)
    return insns


IGNORE = "cov:ignore"


class Coverage:
    def __init__(self, dump, map_path, rst_dir=None, region="auto", src_dirs=(), excepts=()):
        """excepts: "FILE:LINE" or "FILE:FIRST-LAST" to leave out of the lines
        (by-design dead code); a source line holding "cov:ignore" (in a
        comment) is left out too, its .c looked up in src_dirs, else under
        the map's directory and up to three above it."""
        self.map = map_path
        top = os.path.dirname(os.path.abspath(map_path))
        self.src_dirs = list(src_dirs) or [top] + [os.path.abspath(os.path.join(top, *[".."] * n)) for n in (1, 2, 3)]
        self.excepts = {}
        for e in excepts:
            f, _, r = e.rpartition(":")
            if not f or not r:
                raise ValueError(f"--except FILE:LINE[-LAST], not {e}")
            lo, _, hi = r.partition("-")
            self.excepts.setdefault(os.path.basename(f), set()).update(range(int(lo), int(hi or lo) + 1))
        self._src = {}
        regions = load_dump(dump)
        if region == "auto":
            region = "loaded" if regions[1] else "boot"
        self.region = region
        self.addrs = regions[1 if region == "loaded" else 0]
        self.insns = load_listing(rst_files(rst_dir or os.path.dirname(os.path.abspath(map_path)), map_path))
        # functions: by name, module-qualified where two modules have a static of one name
        owners = {}
        for _, mod, func, *_ in self.insns:
            owners.setdefault(func, set()).add(mod)
        self.functions = {}
        for a, mod, func, *_ in self.insns:
            name = func if len(owners[func]) == 1 else f"{mod}:{func}"
            ran, total, _ = self.functions.get(name, (0, 0, mod))
            self.functions[name] = (ran + (a in self.addrs), total + 1, mod)

    def ran_function(self, name):
        f = self.functions.get(name) or self.functions.get(name.lstrip("_"))
        if f is None:
            raise KeyError(f"no function {name} in the listings")
        return f[0] > 0

    def source(self, cfile):
        """The C file as SDCC cited it, its lines, or None if not found."""
        if cfile not in self._src:
            self._src[cfile] = None
            for d in self.src_dirs:
                p = cfile if os.path.isabs(cfile) else os.path.join(d, cfile)
                if os.path.exists(p):
                    self._src[cfile] = open(p, errors="replace").read().splitlines()
                    break
        return self._src[cfile]

    def ignored(self, cfile, line):
        """Left out by --except or a cov:ignore comment on the line."""
        if line in self.excepts.get(os.path.basename(cfile), ()):
            return True
        src = self.source(cfile)
        return bool(src) and 0 < line <= len(src) and IGNORE in src[line - 1]

    def lines(self, cfile):
        """{line: (instructions ran, instructions, source)} for a C file's lines
        with code (its name as SDCC wrote it, or its basename), but those
        ignored()."""
        out, cited = {}, {}
        for a, _, _, f, line, src in self.insns:
            if f is None or (f != cfile and os.path.basename(f) != os.path.basename(cfile)):
                continue
            cited[line] = f
            ran, total, _ = out.get(line, (0, 0, src))
            out[line] = (ran + (a in self.addrs), total + 1, src)
        return {n: v for n, v in sorted(out.items()) if not self.ignored(cited[n], n)}

    def ignored_lines(self, cfile):
        """The lines with code that ignored() leaves out: [(line, source)]."""
        out = {}
        for a, _, _, f, line, src in self.insns:
            if f is not None and (f == cfile or os.path.basename(f) == os.path.basename(cfile)) and self.ignored(f, line):
                out[line] = src
        return sorted(out.items())

    def missed_lines(self, cfile):
        return [(n, src) for n, (ran, _, src) in self.lines(cfile).items() if not ran]


def main():
    ap = argparse.ArgumentParser(description="map rtlsim coverage to functions and C lines")
    ap.add_argument("dump")
    ap.add_argument("--map", required=True)
    ap.add_argument("--rst")
    ap.add_argument("--region", choices=["auto", "boot", "loaded"], default="auto")
    ap.add_argument("--all", action="store_true", help="every function, not only those that ran")
    ap.add_argument("--missed", action="store_true", help="only what didn't run")
    ap.add_argument("--lines", metavar="FILE.c")
    ap.add_argument("--require-lines", metavar="FILE.c")
    ap.add_argument("--require-functions", nargs="+", metavar="NAME")
    ap.add_argument("--diff", metavar="DUMP2")
    ap.add_argument("--except", dest="excepts", action="append", default=[], metavar="FILE:LINE[-LAST]",
                    help="leave a line (or lines) out of --lines and --require-lines (repeatable)")
    ap.add_argument("--src", action="append", default=[], metavar="DIR",
                    help="where the .c files are, for cov:ignore (default: the map's directory and up to 3 above)")
    a = ap.parse_args()
    cov = Coverage(a.dump, a.map, a.rst, a.region, a.src, a.excepts)
    rc = 0

    if a.diff:
        other = Coverage(a.diff, a.map, a.rst, a.region)
        names = sorted(set(cov.functions) | set(other.functions))
        diffs = []
        for n in names:
            x, y = cov.functions.get(n, (0, 0, "")), other.functions.get(n, (0, 0, ""))
            if (x[0] > 0) != (y[0] > 0):
                diffs.append(f"{'+' if y[0] else '-'} {n}  ({x[0]}/{x[1]} -> {y[0]}/{y[1]})")
            elif x[0] != y[0]:
                diffs.append(f"~ {n}  ({x[0]}/{x[1]} -> {y[0]}/{y[1]})")
        print("\n".join(diffs) if diffs else f"no differences ({sum(1 for f in cov.functions.values() if f[0])} functions ran in each)")
        return 1 if diffs else 0

    if a.lines:
        for n, (ran, total, src) in cov.lines(a.lines).items():
            if ran or not a.missed:
                print(f"{n:6d} {'ran ' if ran else 'MISS'} {ran:3d}/{total:<3d} {src}")
    if a.require_lines:
        missed = cov.missed_lines(a.require_lines)
        n = len(cov.lines(a.require_lines))
        ign = cov.ignored_lines(a.require_lines)
        if ign:
            print(f"{a.require_lines}: {len(ign)} lines left out (--except, cov:ignore): {', '.join(str(l) for l, _ in ign)}")
        if not n:
            print(f"{a.require_lines}: no lines with code in the listings")
            rc = 1
        for line, src in missed:
            print(f"{a.require_lines}:{line}: didn't run: {src}")
        print(f"{a.require_lines}: {n - len(missed)} of {n} lines with code ran")
        rc |= bool(missed)
    if a.require_functions:
        for name in a.require_functions:
            try:
                ok = cov.ran_function(name)
            except KeyError as e:
                print(e.args[0])
                ok = False
            if not ok:
                print(f"{name}: didn't run")
                rc = 1
    if not (a.lines or a.require_lines or a.require_functions):
        ran = 0
        for n, (r, total, mod) in sorted(cov.functions.items(), key=lambda kv: (kv[1][2], kv[0])):
            ran += r > 0
            if (r > 0 and not a.missed) or (r == 0 and (a.all or a.missed)):
                print(f"{r:5d}/{total:<5d} {n}  {mod}")
        print(f"{ran} of {len(cov.functions)} functions ran")
    return rc


if __name__ == "__main__":
    sys.exit(main())
