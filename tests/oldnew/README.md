# Old vs new

The old-vs-new harness of the RTLPlayground scheduler work (rtlpg-sched
`doc/sched/testing.md`, layer 4). It runs the same scenarios on two firmware
images in `rtlsim --control`, with the same inputs at the same firmware times,
and compares the **results**, not traces: the scheduler deliberately changes
the order and timing of register writes.

    make oldnew OLD=<boot image> NEW=<boot image> [BOARD=boards/<board>.json] [EXPECT=file] [ONLY="boot cli"]
    tests/oldnew/oldnew.py --old ... --new ... [--board ...] [--expect ...] [--only ...] [--save DIR] [-j N]

Both images are boot images, the loader and the application
(`rtlplayground-boot-*.bin`). Each scenario runs once on each image in its own
rtlsim, and all of them run in parallel. The full set takes about 30 s on the
8x and 20 s on the SWGT024. It exits with 0 when every scenario matches.

## Files

- `bench.py`: one image in rtlsim with the console and devices on its ports:
  a host on front port 1 that speaks ARP, TCP and HTTP, and an LACP partner.
  It also provides cables between ports, SFP modules, and a count of the
  frames the chip sends, by port and kind.
- `scenarios.py`: the reference scenarios.
- `oldnew.py`: the run, the results, and the comparison.
- `lacp_partner.py`: an LACP partner (802.1AX), deterministic, on the bench.
- `mutation.expect`: the expected differences of the check build (below).

## Scenarios

| name | what |
|---|---|
| boot | power-on to the prompt |
| cli | every read-only console command, and an unknown one |
| web | every page and JSON endpoint, before and after login |
| vlan | VLANs and PVIDs from the console and from the web (POST /cmd) |
| stp | RSTP on, with a loop cable between front 3 and 4: one port blocks, BPDUs flow |
| lacp | LACP on front 3 and 4 against a partner (`lacp_partner.py`): both ports aggregate, then the partner goes silent and the switch times it out, then it comes back; then a static LAG |
| lacp_slow | LACP against a partner that asks for the long timeout: the switch sends at its 30 s slow periodic for two periods; then the partner silent (the switch's own timeout is still the short one), and back |
| loop | loop detection, with a cable between front 5 and 6 (3 and 4 on a smaller board): the loop is found, a port blocked, then released when unplugged |
| igmp | snooping: a query, a report (the group's L3 multicast entry), a leave |
| config | a setting from the web, the config saved as the UI saves it (POST /config) and read back, then a power cut: the boot applies it |
| upload | a firmware upload (the reference's application image): checked, reset, installed by the loader, booted |
| sfp | an SFP+ module into cage 1 and out again |
| flaps | links up and down at several speeds, then a fast flap |

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
- table snapshots where a scenario takes them;
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

`--save DIR` writes every result as JSON. `--expect FILE` lists the differences
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
