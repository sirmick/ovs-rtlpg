# Old vs new

The old-vs-new harness of the RTLPlayground scheduler work (rtlpg-sched
`doc/sched/testing.md`, layer 4). It runs the same scenarios on two firmware
images in `rtlsim --control`, with the same inputs at the same firmware times,
and compares the **results**, not traces: the scheduler deliberately changes
the order and timing of register writes.

    make oldnew OLD=<boot image> NEW=<boot image> [BOARD=boards/<board>.json] [EXPECT=file] [ONLY="boot cli"]
    tests/oldnew/oldnew.py --old ... --new ... [--board ...] [--expect ...] [--only ...] [--save DIR] [--cov DIR] [-j N]

Both images are boot images, the loader and the application
(`rtlplayground-boot-*.bin`). Each scenario runs once on each image in its own
rtlsim, and all of them run in parallel. The full set takes about 30 s on the
8x and 20 s on the SWGT024 (fork main; a scheduler build is slower to simulate). It exits with 0 when every scenario matches.

## Files

- `bench.py`: one image in rtlsim with the console and devices on its ports:
  a host on front port 1 that speaks ARP, TCP and HTTP, and an LACP partner.
  It also provides cables between ports, SFP modules, and a count of the
  frames the chip sends, by port and kind.
- `scenarios.py`: the reference scenarios.
- `oldnew.py`: the run, the results, and the comparison.
- `lacp_partner.py`: an LACP partner (802.1AX), deterministic, on the bench.
- `mutation.expect`: the expected differences of the check build (below).
- `checks.py`: the new-only checks on one image (below); `known-failures`,
  today's.
- `translate.py`, `om.expect`, `om_samples.json`: an object-model image,
  compared by value (below).

## Scenarios

| name | what |
|---|---|
| boot | power-on to the prompt |
| cli | every read-only console command, and an unknown one |
| web | every page and JSON endpoint, before and after login |
| vlan | VLANs and PVIDs from the console and from the web (POST /cmd) |
| stp | RSTP on, with a loop cable between front 3 and 4: one port blocks, BPDUs flow |
| lacp | LACP on front 3 and 4 against a partner (`lacp_partner.py`): both ports aggregate, then the partner goes silent and the switch times it out, then it comes back; then a static LAG |
| lacp_slow | LACP against a partner that asks for the long timeout: the switch sends at its 30 s slow periodic for two periods; then the partner silent for 96 s, past its 90 s long timeout (the switch's own receive timeout is the short one: it defaults the partner in ~4 s and stays defaulted, at its slow periodic), then the rejoin |
| lldp | LLDP on with a 23-character hostname, named VLANs 10 and 20 and a 31-character port name; 70 s; the frames compared TLV by TLV (`<n> lldp frames`), their counts, `vlan show`, /vlanlist.json and /lldp.json |
| loop | loop detection, with a cable between front 5 and 6 (3 and 4 on a smaller board): the loop is found, a port blocked, then released when unplugged |
| igmp | snooping: a query, a report (the group's L3 multicast entry), a leave |
| config | a setting from the web, the config saved as the UI saves it (POST /config) and read back, then a power cut: the boot applies it |
| upload | a firmware upload (the reference's application image): checked, reset, installed by the loader, booted |
| sfp | an SFP+ module into cage 1 and out again |
| flaps | links up and down at several speeds, then a fast flap |
| l2_big | 1000 hosts learned behind front 2 (rtlsim's learning); `l2` prints them all, /l2.json paged as the UI does |
| lacp_l2 | lacp and l2_big together: the LAG up, then the 1000-entry `l2` and /l2.json while the LACPDUs are timed |
| stp_upload | stp and upload together: RSTP converged on the loop cable, then a firmware upload while the BPDUs are timed |

The upload scenarios end with the flash, region by region (`flash app`,
`flash slot`, `flash staged`, `flash config`), each said against the image
uploaded in 4 KiB runs: the upload's own bytes (`the upload's a..b`), blank,
or a hash. `--new-payload` gives NEW an image of its own to upload (its own
application, say); the regions then compare equal when each side's flash
holds what it was sent.

l2_big's MACs are sequential (02:00:00:01:00:00 on); the sim's L2 table is
the chip's geometry, 1024 buckets of 4 with an index per entry, its hash a
placeholder: a MAC whose bucket is full is not learned, as on the chip. The
scenario checks that all 1000 are in the table (else it errs, on either
side), so a learning regression can't hide by being on both.

## An object-model image: compared by value

RTLPlayground's object model (feat/om) replaces commands with paths: the
old image takes `pvid 2 10`, the new one `set port.2.pvid 10` and `apply`.
`--new-om OBJS` (`NEW_OM=`; `--old-om` for OLD) names the objects NEW has
moved, and `translate.py` holds, per object, a row for each old command
form: a setting (the new image is given `set` lines, `apply`, then `get`),
a read (`get` lines; the old reply parsed into the same paths), or a usage
error (the new image is given nothing). A row's command is then compared on
both sides as `path value` lines, or `refused`; every other line is typed
as it is and compared as text. `om.expect` lists the intended
differences; an expect line may end in `-/RE/` and `+/RE/`, the lines a
result may lose and gain (each matched in full), so a boot log that loses
its port table differs in nothing else:

    make oldnew OLD=<53ee574 boot image> NEW=<feat/om boot image> NEW_OM=port EXPECT=tests/oldnew/om.expect \
        ONLY="boot vlan stp lacp lldp loop igmp sfp"

The printers and JSON pages are mapped too (`Read` rows and `page()`s):
every field `stat`, `version`, `temp`, `health tasks`, `ip`, `gw`,
`netmask`, `ntp`, `time`, `dns`, `loop show`, `sfp`, `stp status`,
`lacp show`, `lag show`, `l2`, `eee status`, `port N show` and
`port N cabletest show` print, and every key of /information.json,
/ports.json, /traffic.json, /counters.json, /stp.json, /stpcnt.json,
/lag.json, /loop.json, /l2.json, /eee.json and /isolation.json, each to
its path (DESIGN 6 and 7), with the conversions (hex, speed codes, SFP
DDM words to C/V/mA/dBm with external calibration, logical masks, STP
flag bits, local time to UTC, the old surfaces' truncations applied to the
new side's full value). `PATHS` gives each path its object and how `get`
prints it; an image that has moved an object reads its values with `get`,
one that hasn't from the old surface, and a surface gone on one image (its
owners moved) is compared only on what both can show. Anything on a
surface no path took shows as `~ line`. Objects not yet on the switch are
in `PENDING`: naming one is refused until its node takes it out (and
corrects what its schema named otherwise). A config the config scenario
saves goes to the new image as set lines and an apply (`Run.convert()`, a
stand-in for `rtlpg_client.py convert`).

A POST /cmd of a moved object raises until the web side is translated
too. `test_translate.py` (in `make test`) checks the rows and the line
items without rtlsim: every old surface's samples (`om_samples.json`, from
53ee574 in rtlsim) placed on paths, each row's old text against the same
values as get prints them, and the conversions.

## The new-only checks

`testing.md`'s new-only acceptance checks are measured on each image, not
compared: they are what the scheduler must do better than fork main.

    make oldnew-checks IMAGE=<boot image> [BOARD=...] [KNOWN=tests/oldnew/known-failures] [ONLY=...]
    tests/oldnew/checks.py --image ... [--board ...] [--known ...] [--only ...] [--payload ...]

`checks.py` runs the scenarios with a check (lacp stp upload l2_big lacp_l2
stp_upload) on one image and prints a verdict per check; the upload
scenarios upload the image's own application. `oldnew.py` prints them too,
for both sides, under each scenario (`check old|new ...`); they fail its run
only with `--checks` (`CHECKS=1`), and then NEW's.

| check | where | bound |
|---|---|---|
| lacp gap | lacp (aggregated, before the silence and after the rejoin), lacp_l2 (the `l2` dump and /l2.json) | the switch's LACPDUs on each LAG port < 1.2 s apart |
| stp hello | stp (converged), stp_upload (from 4 s before the upload to its answer) | the BPDUs on every port sending them 2 s +-50 ms apart |
| long step \<STATE\> | every scenario, at its end | `health tasks`: no state's longest step over 50 ms (decision 25: the limit 50 ms, the target 5; FL_ERASE has no exemption, its longest is reported); skipped (`SKIP long steps`) on a build without the command (fork main) |

A gap is measured between the frames' firmware send times (rtlsim's
capture: what the partner or the cable's far end gets); every gap that
overlaps the window counts, and so does the silence from the last frame to
its end. Each verdict says the window and the min/max gap per port.

Verdicts: PASS, FAIL, SKIP, and KNOWN: a failure that `--known FILE` lists
for that scenario, within the line's ceiling:

    <scenario glob>[,<scenario glob>...]: <check glob> (<= <n> ms|s [by cli <commands>|*] | unbounded)   # why

The ceiling bounds the check's worst measure (a long step's maximum, a gap
check's largest gap over its upper bound); above it the check FAILs, naming
the line. A gap under the lower bound (hello < 1.95 s) FAILs whatever the
lines say. `health tasks` saturates at >400ms: such a step is bounded only by `by cli
<commands>|*`: the longest console reply, in firmware time from typed to
prompt (a reply holds every step its command ran), of every command the
console was given, the harness's own `ip` and `health tasks` too, whichever
saturated it; the named commands must have been typed. Or the line says
`unbounded`, as a line with no ceiling must. `known-failures` is today's
(dated in its header): at F1 (feat/sched 72cee59) the new build fails no
check, and the two lines left are fork main's, the l2 gap and the hello
during the upload, fixed by E1 and E3. A line that names a scenario that ran
but matched no failed check is flagged: a fix shows as a stale line.
`checks.py` exits with 1 if any check FAILs.

Each step of a scenario owns a slot of firmware time:
- Its input goes in at the slot's start.
- Its result is what came back within the slot.
- The next step starts when the slot ends.

So both images get every input at the same firmware time, and a reply that
misses its slot is a difference (`<timeout>`). Inside a step, the HTTP client
answers the switch as it goes (TCP is a conversation). The bench looks at the
wire on a fixed grid of firmware time: frames cross a cable, and the host
answers, at grid points.

## Results

These are named and compared one by one:
- every console command's reply, from the command to the prompt;
- everything else the console said, between two steps, as `<n> console`
  (n is the step it came before): link changes, STP, SFP, loop and IGMP
  events, what followed a prompt. No UART byte is dropped;
- every web response (status, headers and body; the session cookie's value
  is left out);
- the boot log;
- table snapshots and frame counts (`<n> frames <kind>`, per port, so far)
  where a scenario takes them;
- the LLDP frames sent, decoded TLV by TLV, per port in order (`<n> lldp
  frames`; only the version string in the system description is masked, and
  its length shown as the rest plus the version's);
- at the end:
  - the switch core's stored registers, SerDes and PHY registers;
  - the VLAN and L2 tables;
  - the saved config (flash 0x7f000);
  - the frames sent out of each port, by kind.

The two builds' identity is scrubbed from every result: the version string
and the build dates. So are two things that are the program, not its
behaviour:
- the register window the loader loads code through;
- the image CRC that the default config's copy brings into the config sector.

Lines the firmware prints on its own (`bench.ASYNC`, from its sources: `<new
link ...>`, `STP: ...`, the SFP module events, loop and IGMP entries, LAG trunk
changes) are taken out of a reply and kept with what followed its prompt. So a
line printed a scheduler pass before or after a prompt lands in the same
`console` result either way. A `console` result is compared as it is, in
order: two async lines that the scheduler legitimately prints in the other
order are a difference, to list with `--expect`. The blocks are cut at the
steps' starts, which are fixed firmware times: only a line printed within a
pass of a step's start could fall on the other side.

`--save DIR` writes every result as JSON. `--cov DIR` dumps each run's
coverage at its end (`<scenario>.<side>.cov`); `cov_diff.py DIR --old-map
OLD.map --new-map NEW.map` lists, by name, the functions one side ran in any
scenario and the other didn't. `--expect FILE` lists the differences
a change means to make, one per line, as `<scenario glob> <result glob>  #
why`. Those are reported as expected and don't fail the run.

## The reference build and the check build

The reference ("old") is fork main, b635486, in a worktree of its own:

    git -C ~/src/RTLPlayground worktree add --detach ~/src/rtlpg-ref b635486
    cd ~/src/rtlpg-ref
    tools/docker-make.sh MACHINE=KEEPLINK_KP_9000_9XHPML_V1_2 LAYOUT=compact LOADER=1
    tools/docker-make.sh MACHINE=SWGT024_V2_0_UNMANAGED LAYOUT=compact LOADER=1

`make oldnew` with no OLD takes the 8x one from there.

The check that the harness catches a change uses the same commit with two
edits, built in `~/src/rtlpg-ref-mut`:
- `print_sw_version()` prints "Build date" before "Software version";
- `rtl8373_init()` writes 0x1051 to 0x7124 instead of 0x1050.

Old against it fails every scenario, with the diff pointing at both edits.
With `EXPECT=tests/oldnew/mutation.expect` it passes.

## What the sim adds for it

Control mode (`src/ctl/control.h`) gives the harness what it needs:
- `reg dump`, `table dump vlan|l2`, `flash read` and `sfp`;
- the IGMP trap on `nic inject`;
- the chip's loop detection (`src/sys/rldp.h`, the fabric's RLDP engine, now
  shared);
- a frame injected on a port without a link is dropped;
- the NIC RX ring's capacity (10 KiB, `--nic-rx-ring`) and its drops (`nic stats`);
- the link-change latch (0x5f88) is write-1-to-clear: without it, LACP
  expired every port once a second.
- MAC learning on `nic inject`, as the fabric's (no ageing): `l2` and
  `/l2.json` compare a real table, and the switch's unicast goes to the
  learned port instead of flooding.
