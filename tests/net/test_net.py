#!/usr/bin/env python3
"""test_net.py - the firmware's TCP/IP stack against a real Linux one.

    tests/net/test_net.py IMAGE [--rtlsim build/rtlsim] [--port 4]

rtlsim wires a physical port (default 4, front port 1) to its --nic socket,
tests/net/tapbridge.py relays that to a tap in a private network namespace,
and the namespace's Linux pings the switch and loads its web UI. Needs sudo
(for the namespace and the tap); skipped without it. Nothing outside the
namespace is touched.

Also measures: the firmware's machine cycles per reply (from --trace-nic),
the time to load the web UI, and TCP throughput. A 10G SFP module
(tools/mksfp.py) sits in slot 1, so the pages that read its EEPROM have one.
"""
import argparse, json, os, re, shutil, statistics, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT

CYCLES_PER_S = 125e6 / 4
# Every JSON page app.js fetches
PAGES = ["information", "ports", "counters", "traffic", "vlan", "vlanlist", "lag", "isolation", "mirror",
         "storm", "bandwidth", "eee", "lldp", "stp", "stpcnt"]
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def sudo(*cmd, **kw):
    return subprocess.run(["sudo", "-n", *cmd], capture_output=True, text=True, **kw)


def costs(trace):
    """Pairs each reply with the frame that came in before it:
    {kind: [busy cycles]}."""
    out, last = {}, None
    for l in trace.splitlines():
        m = re.match(r"NIC RX p\d+ \d+B (.*)", l)
        if m:
            last = m.group(1)
            continue
        m = re.match(r"NIC TX p\d+ \d+B (.*?)  \+(\d+) cycles", l)
        if not m or last is None:
            continue
        rx, tx, n = last, m.group(1), int(m.group(2))
        last = None
        if rx.startswith("ARP"):
            kind = "ARP request -> reply"
        elif rx.startswith("ICMP type 8"):
            kind = "ICMP echo -> reply"
        elif re.search(r"flags 02 ", rx):
            kind = "TCP SYN -> SYN-ACK"
        elif re.search(r"flags 1[08] len [1-9]", rx):
            kind = "TCP request segment -> reply"
        elif re.search(r"flags 10 len 0", rx) and re.search(r"len [1-9]", tx):
            kind = "TCP ACK -> next full segment"
        else:
            continue
        out.setdefault(kind, []).append(n)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    ap.add_argument("--port", type=int, default=4)
    a = ap.parse_args()
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
        print("net: no passwordless sudo, skipped")
        return 0

    d = tempfile.mkdtemp(prefix="rtlsim-net-")
    sock, ns, tap = os.path.join(d, "nic.sock"), f"rtlsim{os.getpid()}", "rtap0"
    err = open(os.path.join(d, "rtlsim.err"), "w+")
    sfp = os.path.join(d, "sfp.bin")
    subprocess.run([sys.executable, os.path.join(HERE, "../../tools/mksfp.py"), sfp], check=True)
    c = Console(a.rtlsim, a.image, ["--nic", sock, "--nic-port", str(a.port), "--trace-nic",
                                    "--sfp", f"1={sfp}", "--link", "8=10g"], stderr=err)
    bridge = None
    try:
        check(c.expect(PROMPT, 30), "boots to the prompt")
        start = len(c.log)
        c.send("ip")
        c.expect(PROMPT, 10)
        m = re.search(rb"Current IP: ([\d.]+)", c.log[start:])
        ip = m.group(1).decode() if m else "192.168.2.2"
        host = re.sub(r"\.\d+$", ".1" if not ip.endswith(".1") else ".2", ip)
        print(f"switch {ip}, Linux {host} in netns {ns}")

        for cmd in (["ip", "netns", "add", ns],
                    ["ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up"],
                    ["ip", "netns", "exec", ns, "ip", "tuntap", "add", "dev", tap, "mode", "tap"],
                    ["ip", "netns", "exec", ns, "ip", "addr", "add", f"{host}/24", "dev", tap],
                    ["ip", "netns", "exec", ns, "ip", "link", "set", tap, "up"]):
            r = sudo(*cmd)
            if r.returncode:
                check(False, f"{' '.join(cmd)}: {r.stderr.strip()}")
                return 1
        bridge = subprocess.Popen(["sudo", "-n", "ip", "netns", "exec", ns, sys.executable,
                                   os.path.join(HERE, "tapbridge.py"), sock, tap])
        nsx = ["ip", "netns", "exec", ns]

        r = sudo(*nsx, "ping", "-c", "5", "-i", "0.2", "-W", "2", ip, timeout=30)
        rtt = re.search(r"= [\d.]+/([\d.]+)/", r.stdout)
        check(r.returncode == 0, f"ping {ip}" + (f" (avg rtt {rtt.group(1)} ms)" if rtt else f": {r.stdout[-200:]}"))

        # A browser's visit: the login page, the login, then the UI itself
        jar = os.path.join(d, "cookies")
        fmt = r"%{url} %{http_code} %{size_download} %{time_total}\n"
        curl = [*nsx, "curl", "-s", "--max-time", "60", "-b", jar, "-c", jar, "-w", fmt]
        t = time.time()
        r1 = sudo(*curl, "-o", "/dev/null", f"http://{ip}/login.html", timeout=90)
        sudo(*curl, "-o", "/dev/null", "-d", "pwd=1234", f"http://{ip}/login", timeout=90)
        login = time.time() - t
        logged_in = os.path.exists(jar) and "session" in open(jar).read()
        check(r1.stdout.split()[1:2] == ["200"] and logged_in, f"login.html and the login (password 1234) in {login:.2f} s")
        t = time.time()
        r = sudo(*curl, "-o", "/dev/null", "-o", "/dev/null", f"http://{ip}/", f"http://{ip}/app.js", timeout=90)
        load = time.time() - t
        rows = [l.split() for l in r.stdout.splitlines()]
        ok = len(rows) == 2 and all(row[1] == "200" for row in rows)
        sizes = "+".join(row[2] for row in rows) if ok else ""
        check(ok, f"the web UI (/ and /app.js, {sizes} bytes) loads in {load:.2f} s"
              + ("" if ok else f": {r.stdout} {r.stderr}"))

        bad, seen = [], ""
        for page in PAGES:
            r = sudo(*nsx, "curl", "-s", "--max-time", "10", "-b", jar, "-w", r"\n%{http_code}",
                     f"http://{ip}/{page}.json", timeout=30)
            body, _, code = r.stdout.rpartition("\n")
            try:
                json.loads(body)
                ok = code == "200"
            except ValueError:
                ok = False
            if not ok:
                bad.append(f"{page} ({code}: {body[:60]!r})")
            seen += body
        check(not bad, f"all {len(PAGES)} JSON pages answer with JSON" + (f"; not: {', '.join(bad)}" if bad else ""))
        check("RTLSIM" in seen, "the SFP module's vendor shows in the web UI's data")

        r = sudo(*curl, "-o", "/dev/null", f"http://{ip}/app.js", timeout=90)
        _, code, size, secs = (r.stdout.split() + ["", "", "0", "0"])[:4]
        rate = int(size) / max(float(secs), 1e-6)
        check(code == "200" and int(size) > 0,
              f"TCP throughput: app.js, {size} bytes in {float(secs):.2f} s = {rate / 1024:.1f} KiB/s")

        err.flush()
        err.seek(0)
        trace = err.read()
        print("busy machine cycles per reply (median, min-max, count):")
        for kind, v in costs(trace).items():
            med = statistics.median(v)
            print(f"  {kind:32} {med:8.0f} ({med / CYCLES_PER_S * 1e6:6.0f} us)  {min(v)}-{max(v)}  n={len(v)}")
        drops = re.findall(r"NIC (?:RX|TX).*dropped.*", trace)
        print(f"NIC drops: {len(drops)}" + (f", first: {drops[0]}" if drops else ""))
    finally:
        if bridge:
            bridge.terminate()
            bridge.wait()
        sudo("ip", "netns", "del", ns)
        c.close()
        err.close()
        sudo("rm", "-f", os.path.join(d, "cookies"))	# root's, from curl
        shutil.rmtree(d, ignore_errors=True)

    print(f"test_net: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
