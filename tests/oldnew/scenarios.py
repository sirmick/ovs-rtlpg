"""scenarios.py - the reference scenarios (rtlpg-sched doc/sched/testing.md,
layer 4), each a script of inputs at fixed firmware times.

A scenario is a function of a Run (oldnew.py). Each of its steps owns a slot
of firmware time: its input goes in at the slot's start, its result is what
came back within the slot, and the next step starts when the slot ends,
whatever the firmware did. So two images get every input at the same
firmware time, and a reply that misses its slot is a difference ("timeout").

Ports are the board's copper front ports by index: r.P[i] is the physical
port (rtlsim's numbering) of the i-th, r.F[i] its name on the CLI; r.cages
the SFP cages. The host (the harness's HTTP client) is on front port 1,
r.P[0]. Four copper ports are enough for every scenario.
"""
import os, re, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "..", "tools")


def _sfp(speed):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sfp.bin")
        subprocess.run(["python3", os.path.join(TOOLS, "mksfp.py"), "--speed", speed, p], check=True,
                       stdout=subprocess.DEVNULL)
        return open(p, "rb").read()


def boot(r):
    """Power on to the prompt; the state the boot leaves."""
    r.boot()
    r.wait(2000)


CLI_TOUR = ["version", "ip", "gw", "netmask", "mac", "hostname", "stat", "port", "vlan show", "pvid",
            "lag show", "laghash", "lacp show", "stp", "stp status", "loop show", "igmp show", "l2",
            "mirror", "isolate", "ingress", "mtu", "eee", "bw", "storm", "sfp", "temp", "time", "health",
            "history", "dns", "ntp", "syslog", "lldp", "led", "session", "nosuchcommand"]


def cli(r):
    """Every read-only console command, and one that doesn't exist."""
    r.boot()
    for c in CLI_TOUR:
        r.cli(c)


WEB_PAGES = ["/index.html", "/login.html", "/app.js", "/favicon.ico", "/ports.json", "/traffic.json",
             "/information.json", "/vlan.json?vid=1", "/counters.json?port=1", "/eee.json", "/bandwidth.json",
             "/storm.json", "/l2.json?idx=0", "/mirror.json", "/isolation.json", "/lag.json", "/stp.json",
             "/lldp.json", "/loop.json", "/stpcnt.json", "/vlanlist.json", "/config", "/cmd_log", "/nosuchpage"]


def web(r):
    """Every page, before and after logging in."""
    r.boot()
    r.get("/information.json", login=False)
    r.get("/index.html", login=False)
    r.login()
    for p in WEB_PAGES:
        r.get(p)


def vlan(r):
    """VLANs and PVIDs, set from the console and from the web."""
    P, F = r.P, r.F
    r.boot()
    for i in (1, 2, 3):
        r.link(P[i], True)
    r.login()
    for c in [f"vlan 10 {F[1]} {F[2]}t {F[3]}t", f"vlan 20 {F[3]} {F[2]}t", f"pvid {F[1]} 10", f"pvid {F[3]} 20",
              f"vlan 30 {F[2]}", "vlan 30 d", "vlan show", "pvid"]:
        r.cli(c)
    r.post_cmd(f"vlan 40 {F[2]} {F[3]}t")
    for p in ["/vlanlist.json", "/vlan.json?vid=10", "/vlan.json?vid=40", "/ports.json"]:
        r.get(p)
    r.cli("l2")				# the host's MAC, learned on front port 1
    r.get("/l2.json?idx=0")


def stp(r):
    """RSTP on, with a loop between front ports 3 and 4 and the host on 1:
    the loop's port is blocked, BPDUs flow."""
    P = r.P
    r.boot()
    r.link(P[1], True)
    r.cable(P[2], P[3])
    r.cli("stp on")
    r.wait(35000)
    for c in ["stp status", "stp"]:
        r.cli(c)
    r.login()
    for p in ["/stp.json", "/stpcnt.json"]:
        r.get(p)
    r.cli("stp off")
    r.wait(2000)
    r.cli("stp status")


def lacp(r):
    """LACP on front ports 3 and 4 against a partner (lacp_partner.py):
    both ports aggregate; the partner goes silent and the switch times it
    out; it comes back. Then a static LAG of 2 and 3."""
    P, F = r.P, r.F
    r.boot()
    for i in (2, 3):
        r.link(P[i], True)
    partner = r.partner([P[2], P[3]])
    r.cli(f"lag 1 lacp {F[2]} {F[3]}")
    r.wait(5000)
    r.cli("lacp show")
    r.cli("lag show")
    r.partner_view()
    r.login()
    r.get("/lag.json")
    r._now()
    partner.stop()
    r.wait(6000)
    r.cli("lacp show")
    r._now()
    partner.start()
    r.wait(5000)
    r.cli("lacp show")
    r.partner_view()
    r.cli("lag 1 lacp off")
    r.cli(f"lag 2 {F[1]} {F[2]}")
    r.cli("lag show")
    r.get("/lag.json")


def lacp_slow(r):
    """LACP against a partner that asks for the long timeout (lacp_partner.py,
    short=False): the switch sends at its slow periodic, every 30 s, while
    the partner sends every second (the switch asks for the short timeout).
    Two slow periods, then the partner silent - the switch's own timeout is
    still the short one - and back."""
    P, F = r.P, r.F
    r.boot()
    for i in (2, 3):
        r.link(P[i], True)
    partner = r.partner([P[2], P[3]], short=False)
    r.cli(f"lag 1 lacp {F[2]} {F[3]}")
    r.wait(5000)
    r.cli("lacp show")
    r.partner_view()
    r.wait(65000)
    r.cli("lacp show")
    r.partner_view()			# the switch's LACPDUs: one every 30 s
    r._now()
    partner.stop()
    r.wait(6000)
    r.cli("lacp show")
    r._now()
    partner.start()
    r.wait(5000)
    r.cli("lacp show")
    r.partner_view()


def loop(r):
    """Loop detection (the chip's RLDP engine, sys/rldp.h) with a cable
    between front ports 5 and 6 (3 and 4 on a board with fewer copper
    ports): the firmware finds the loop and blocks a port; unplugged, it
    lets go."""
    P = r.P
    a, b = (4, 5) if len(P) >= 6 else (2, 3)
    r.boot()
    r.link(P[a], True)
    r.link(P[b], True)
    r.cli("loop on")
    r.wait(1000)
    r.cable(P[a], P[b])
    r.wait(10000)
    r.cli("loop show")
    r.cli("stat")
    r.login()
    r.get("/loop.json")
    r.uncable(P[a])
    r.wait(10000)
    r.cli("loop show")
    r.cli("loop off")


def igmp(r):
    """IGMP snooping: a querier on front port 3, a member report from 2, a
    leave."""
    P = r.P
    r.boot()
    for i in (1, 2):
        r.link(P[i], True)
    r.cli("igmp on")
    r.wait(1000)
    r.igmp(P[2], 0x11, "224.0.0.1", "0.0.0.0")	# a general query, from a router
    r.wait(500)
    r.igmp(P[1], 0x16, "239.1.2.3", "239.1.2.3")	# joins 239.1.2.3
    r.wait(2000)
    r.cli("igmp show")
    r.cli("l2")
    r.tables()					# the group's L3 multicast entry
    r.igmp(P[1], 0x17, "224.0.0.2", "239.1.2.3")	# and leaves
    r.wait(3000)
    r.cli("igmp show")
    r.tables()
    r.cli("igmp off")


def config_text(r):
    F = r.F
    return f"hostname oldnew\nvlan 10 {F[1]} {F[2]}t\npvid {F[1]} 10\nstp on\nloop on\n"


def config(r):
    """Settings, saved from the web as the UI does it (POST /config), read
    back; then a power cut: the boot applies them."""
    r.boot()
    r.login()
    r.post_cmd("hostname changed")
    r.get("/cmd_log")
    r.post_config(config_text(r))
    r.get("/config")
    r.get("/cmd_log_clear")
    r.powercut()
    r.boot()
    for c in ["hostname", "vlan show", "pvid", "stp", "loop show"]:
        r.cli(c)


def upload(r):
    """A firmware upload from the web (the reference's application image):
    the switch checks it and resets; the loader installs it; the boot after."""
    r.boot()
    r.login()
    r.post_upload(r.payload, slot=20000)
    r.boot(slot=60000)
    r.cli("version")


def sfp(r):
    """An SFP+ module into cage 1, with light; out again."""
    if 1 not in r.cages:
        r.note("no SFP cage on this board")
        return
    port = r.cages[1][0]
    r.boot()
    r.login()
    r.sfp(1, _sfp("10g"))
    r.link(port, True, "10g")
    r.wait(3000)
    r.cli("sfp")
    r.cli("stat")
    r.get("/ports.json")
    r.link(port, False)
    r.sfp(1, None)
    r.wait(3000)
    r.cli("sfp")
    r.cli("stat")
    r.get("/ports.json")


def flaps(r):
    """Links up and down at several speeds; the port table and the JSON."""
    P = r.P
    r.boot()
    r.login()
    for i, up, sp in [(1, True, "1g"), (2, True, "2g5"), (3, True, "100m"), (1, False, None),
                      (3, True, "10m"), (2, False, None), (1, True, "2g5")]:
        r.link(P[i], up, sp)
        r.wait(1500)
        r.cli("stat")
    r.get("/ports.json")
    for i in range(10):			# a fast flap
        r.link(P[3], i % 2 == 1, "1g")
        r.wait(50)
    r.wait(2000)
    r.cli("stat")
    r.get("/ports.json")
    r.cli("l2")


SCENARIOS = {f.__name__: f for f in (boot, cli, web, vlan, stp, lacp, lacp_slow, loop, igmp, config, upload, sfp, flaps)}
