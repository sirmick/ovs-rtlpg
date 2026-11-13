#!/usr/bin/env python3
"""test_net.py - the firmware's TCP/IP stack against a real Linux one.

    tests/net/test_net.py IMAGE [--rtlsim build/rtlsim] [--port 4] [--via tap|ovs]

Linux, in a private network namespace, reaches a physical port of the switch
(default 4, front port 1), pings the switch and loads its web UI. --via tap:
rtlsim's --nic socket, relayed to a tap by tests/net/tapbridge.py. --via ovs:
an OVS bridge with rtlsim as its OpenFlow controller (--ofp), set up by
tools/rtlswitch.py: every front port a veth, Linux on the port and a second
Linux on another one; also pulls the port's cable and checks the firmware
sees the link go.
Needs sudo (for the namespace, the tap, OVS); skipped without it. Nothing
outside the namespace is touched.

Also measures: the firmware's machine cycles per reply (from --trace-nic),
the time to load the web UI, and TCP throughput. A 10G SFP module
(tools/mksfp.py) sits in slot 1, so the pages that read its EEPROM have one.
"""
import argparse, json, os, re, shutil, statistics, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT
sys.path.insert(0, os.path.join(HERE, "../../tools"))
from rtlswitch import Lab

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


def link_change(c, port, up, timeout):
    """Waits for the firmware's "<new link: S9..S0, was ...>" line (a speed
    digit per physical port, 0 for down) to show `port` up or down."""
    start, end = len(c.log), time.time() + timeout
    while time.time() < end:
        c.expect(b">", 0.5)
        for m in re.finditer(rb"<new link: ([0-9a-f]{10}),", c.log[start:]):
            if (m.group(1)[9 - port:10 - port] != b"0") == up:
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    ap.add_argument("--port", type=int, default=4)
    ap.add_argument("--via", choices=["tap", "ovs"], default="tap")
    a = ap.parse_args()
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
        print("net: no passwordless sudo, skipped")
        return 0

    d = tempfile.mkdtemp(prefix="rtlsim-net-")
    os.chmod(d, 0o755)	# root's OVS reads and writes in here
    sock, ns, tap = os.path.join(d, "nic.sock"), f"rtlsim{os.getpid()}", "rtap0"
    board = json.load(open(os.path.join(HERE, "../../boards/swtg024as.json")))
    fronts = [p["front"] for p in board["ports"] if "sfp" not in p]
    front = next(p["front"] for p in board["ports"] if p["phys"] == a.port)
    other = next(f for f in fronts if f != front)
    err = open(os.path.join(d, "rtlsim.err"), "w+")
    sfp = os.path.join(d, "sfp.bin")
    subprocess.run([sys.executable, os.path.join(HERE, "../../tools/mksfp.py"), sfp], check=True)
    extra = ["--trace-nic", "--sfp", f"1={sfp}", "--link", "8=10g"]
    bridge = lab = None
    if a.via == "tap":
        c = Console(a.rtlsim, a.image, ["--nic", sock, "--nic-port", str(a.port), *extra], stderr=err)
    else:
        lab = Lab(a.image, hosts={front: None, other: None}, extra=["--trace-of", *extra], rtlsim=a.rtlsim,
                  console=True, stderr=err).start(attach=False)
        c = lab.console
    try:
        check(c.expect(PROMPT, 30), "boots to the prompt")
        start = len(c.log)
        c.send("ip")
        c.expect(PROMPT, 10)
        m = re.search(rb"Current IP: ([\d.]+)", c.log[start:])
        ip = m.group(1).decode() if m else "192.168.2.2"
        host = re.sub(r"\.\d+$", ".1" if not ip.endswith(".1") else ".2", ip)
        if a.via == "tap":
            print(f"switch {ip}, Linux {host} in netns {ns}, via a tap")
            nsx = ["ip", "netns", "exec", ns]
            for cmd in (["ip", "netns", "add", ns], [*nsx, "ip", "link", "set", "lo", "up"],
                        [*nsx, "ip", "tuntap", "add", "dev", tap, "mode", "tap"],
                        [*nsx, "ip", "addr", "add", f"{host}/24", "dev", tap], [*nsx, "ip", "link", "set", tap, "up"]):
                r = sudo(*cmd)
                if r.returncode:
                    check(False, f"{' '.join(cmd)}: {r.stderr.strip()}")
                    return 1
            bridge = subprocess.Popen(["sudo", "-n", *nsx, sys.executable, os.path.join(HERE, "tapbridge.py"), sock, tap])
        else:
            ns, tap = lab.host_ns(front), "eth0"
            nsx = ["ip", "netns", "exec", ns]
            second = re.sub(r"\.\d+$", ".3", host)
            lab.address(front, f"{host}/24")
            lab.address(other, f"{second}/24")
            print(f"switch {ip}, Linux {host} on port {front} and {second} on port {other}, via OVS")
            lab.attach()
            check(link_change(c, a.port, True, 10), f"the veth's link comes up on physical port {a.port}")

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

        if lab:
            r = sudo("ip", "netns", "exec", lab.host_ns(other), "ping", "-c", "3", "-i", "0.2", "-W", "2", ip, timeout=30)
            check(r.returncode == 0, f"Linux on port {other} reaches the switch too")
            # pull the cable: the OVS port goes down, the firmware sees it
            sudo(*nsx, "ip", "link", "set", tap, "down")
            down = link_change(c, a.port, False, 10)
            sudo(*nsx, "ip", "link", "set", tap, "up")
            up = link_change(c, a.port, True, 10)
            check(down and up, "a cable pull on the veth: the firmware sees the link go and come back")
            r = sudo(*nsx, "ping", "-c", "2", "-W", "2", ip, timeout=30)
            check(r.returncode == 0, "and the switch answers again after it")
            flows = lab.ovs.ofctl("dump-flows", lab.name)
            print("OVS flows:", " | ".join(l.strip() for l in flows.splitlines() if "actions" in l))

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
        if lab:
            lab.stop()
        sudo("ip", "netns", "del", ns)
        c.close()
        err.close()
        sudo("rm", "-rf", d)	# root's files, from curl and OVS
        shutil.rmtree(d, ignore_errors=True)

    print(f"test_net: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
