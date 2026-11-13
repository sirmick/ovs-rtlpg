#!/usr/bin/env python3
"""test_update.py - a firmware update through the web UI, on a persistent flash.

    tests/net/test_update.py IMAGE [--rtlsim build/rtlsim]

The switch boots IMAGE on a --flash file; Linux in a private network
namespace (a tap on the --nic socket, as test_net.py) logs in and uploads
images as RTLPlayground's tools/fwupdate.py does (a multipart POST to
/upload). Checks:
- a good image is staged, verified at the next boot, copied into place, and
  the switch comes back with its saved config. Its upload runs across
  loader_confirm(), 15 s (LDR_CONFIRM_S) after the boot, which reads the
  loader's boot counter: before RTLPlayground 56db072 it read it into
  flash_buf, where the upload keeps its next page between packets, and the
  read-back refused the image ("checksum failed");
- a corrupted image is refused, and the switch carries on;
- if IMAGE can resume an install a reset broke off ("Resuming the update"),
  a power cut in the middle of the copy (rtlsim killed: the flash file is
  written through, so it keeps what was programmed) and a power-on finish
  the install.
The peer connects at power-on, so the firmware's clock runs with the wall
clock from its boot (rtlsim --realtime), and the test knows the firmware's
time. Needs sudo; skipped without it. Real time: ~1.5 min.
"""
import argparse, os, re, shutil, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT

failures = 0
IP = "192.168.2.2"
CONFIRM_S = 15		# LDR_CONFIRM_S


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def sudo(*cmd, **kw):
    return subprocess.run(["sudo", "-n", *cmd], capture_output=True, text=True, **kw)


class Switch:
    """rtlsim on the flash file, with Linux on port 4 through a tap. The image
    only fills a new flash file; an existing one is powered on as it is."""

    def __init__(self, a, d, ns):
        self.d, self.ns, self.sock = d, ns, os.path.join(d, "nic.sock")
        self.c = Console(a.rtlsim, a.image, ["--flash", os.path.join(d, "flash.bin"), "--nic", self.sock, "--nic-port", "4"])
        self.bridge = None

    def connect(self):
        self.bridge = subprocess.Popen(["sudo", "-n", "ip", "netns", "exec", self.ns, sys.executable,
                                        os.path.join(HERE, "tapbridge.py"), self.sock, "rtap0"])
        self.booted = time.time()

    def uptime(self):
        """Seconds of firmware time since its last (re)boot."""
        return time.time() - self.booted

    def past_confirm(self):
        time.sleep(max(0, CONFIRM_S + 1 - self.uptime()))

    def curl(self, *args, timeout=120):
        jar = os.path.join(self.d, "cookies")
        return sudo("ip", "netns", "exec", self.ns, "curl", "-s", "--max-time", str(timeout), "-b", jar, "-c", jar,
                    *args, timeout=timeout + 30)

    def login(self):
        self.curl("-o", "/dev/null", "-d", "pwd=1234", f"http://{IP}/login")

    def upload(self, path, *opts):
        r = self.curl(*opts, "-F", f"uploadedfile=@{path};type=application/octet-stream", "-H", "Connection: close",
                      f"http://{IP}/upload")
        return r.stdout.strip()

    def wait_boots(self, n, timeout):
        """Waits for the n-th boot to reach its prompt; notes when it began."""
        end = time.time() + timeout
        while time.time() < end and boots(self.c.log.decode(errors="replace")) < n:
            self.c.expect(PROMPT, 1)
        self.booted = time.time()
        return self.c.expect(PROMPT, 30)

    def close(self):
        if self.bridge:
            self.bridge.terminate()
            self.bridge.wait()
        self.c.close()


def boots(log):
    return log.count("Software version:")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
        print("update: no passwordless sudo, skipped")
        return 0
    img = open(a.image, "rb").read()
    resumes = b"Resuming the update" in img

    d = tempfile.mkdtemp(prefix="rtlsim-update-")
    ns = f"rtlsimu{os.getpid()}"
    nsx = ["ip", "netns", "exec", ns]
    for cmd in (["ip", "netns", "add", ns], [*nsx, "ip", "tuntap", "add", "dev", "rtap0", "mode", "tap"],
                [*nsx, "ip", "addr", "add", "192.168.2.1/24", "dev", "rtap0"], [*nsx, "ip", "link", "set", "rtap0", "up"]):
        sudo(*cmd)
    bad = os.path.join(d, "bad.bin")
    open(bad, "wb").write(img[:0x5000] + bytes([img[0x5000] ^ 0xff]) + img[0x5001:])
    sw = None
    try:
        sw = Switch(a, d, ns)
        sw.connect()		# the firmware's clock runs with ours from here
        check(sw.c.expect(PROMPT, 30) and b"Written default config to flash" in sw.c.log,
              "first boot on an empty flash writes the default config")

        sw.login()
        t0 = sw.uptime()
        verdict = sw.upload(a.image, "--limit-rate", "20k")	# ~12 s, across loader_confirm()
        t1 = sw.uptime()
        up = sw.wait_boots(3, 120)
        log = sw.c.log.decode(errors="replace")
        third = log[log.rfind("Software version:"):]
        why = [w for w, bad in (("3 boots", boots(log) != 3),
                                ("upload across 15 s", not t0 < CONFIRM_S - 1 < CONFIRM_S + 1 < t1),
                                ("verified", not re.search(r"found update image!\nChecking integrity\.*Checksum OK", log)),
                                ("staging cleared", "no update image found" not in third),
                                ("config kept", "Written default config" in third)) if bad]
        check(up and not why, f"a good image, uploaded from {t0:.0f} to {t1:.0f} s after the boot "
              f"({verdict[:30]!r}), is staged, verified, copied, and the switch comes back with its config"
              + (f"; not: {why}" if why else ""))

        sw.past_confirm()
        sw.login()		# the reboots ended the session
        verdict = sw.upload(bad)
        sw.c.expect(b"nothing", 2)
        check("checksum" in verdict.lower() and boots(sw.c.log.decode(errors="replace")) == 3,
              f"a corrupted image is refused ({verdict[:60]!r}) and the switch carries on")
        sw.close()
        sw = None

        if not resumes:
            print("power cut: this image can't resume a broken-off install, skipped")
        else:
            os.remove(os.path.join(d, "flash.bin"))
            sw = Switch(a, d, ns)
            sw.connect()
            sw.c.expect(PROMPT, 30)
            sw.past_confirm()
            sw.login()
            sw.upload(a.image)
            # cut the power once the copy has started (its first progress dot)
            cut = sw.c.expect(b"Moving firmware to start of flash.", 60)
            sw.c.p.kill()
            sw.c.p.wait()
            done = sw.c.log.decode(errors="replace").split("Moving firmware to start of flash")[-1]
            sw.close()
            sw = Switch(a, d, ns)
            up = sw.c.expect(PROMPT, 60)
            log = sw.c.log.decode(errors="replace")
            check(cut and "Done." not in done and "Resuming the update" in log and up
                  and log.rstrip().endswith(">"),
                  "a power cut in the middle of the copy: the next power-on resumes it and the switch comes up")
            sw.c.send("ip")
            check(sw.c.expect(b"Current IP", 10), "and its CLI answers")
    finally:
        if sw:
            sw.close()
        sudo("ip", "netns", "del", ns)
        sudo("rm", "-rf", d)

    print(f"test_update: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
