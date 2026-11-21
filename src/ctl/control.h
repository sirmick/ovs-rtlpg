// control.h - rtlsim driven by a test over a socket, in firmware time.
//
// rtlsim --control PATH listens on a UNIX stream socket at PATH and starts
// paused: the firmware only runs when a request says so, and every input (a
// UART byte, a frame, a link change) goes in at the firmware time it is
// given at. Nothing reads the wall clock, so the same requests give the same
// replies, byte for byte, run after run.
//
// Firmware time is the machine cycle count, 31.25 M a second, written
// <ms>.<µs> ("2213.408"): a power cut doesn't restart it. The CPU runs in
// slices that end on multiples of 256 cycles (8.192 µs) of firmware time;
// what the model does between instructions (a UART byte in, INT0 released,
// a chip reset, the halt check) happens only there, never where a run
// stopped (a breakpoint, a UART match, the end of run <ms>), and idling
// fast-forwards in whole prescaler periods: however a test cuts up a run,
// an input goes in at the same firmware time, and the firmware does the same.
//
// One request per line; one reply line, "ok ..." or "err <why>", or for
// bulk "ok <n>" and n more lines. Times in requests are ms, with up to three
// decimals. The client stops by closing the socket or with quit.
//
//   time                              ok <cycles> <t>
//   run <ms>                          ok <t>, or ok halt <t> (sjmp .)
//   run until pc=<loc> [max <ms>]     ok hit <t> | ok timeout <t> | ok halt <t>
//       stops before the instruction at <loc> runs (a symbol, 0x1234, or
//       b<n>:0x5678 for a banked address); not on the first instruction
//   run until uart=/<re>/ [max <ms>]  as above: the output since the last
//       uart read matches <re>, checked after each byte the firmware sends.
//       POSIX extended regex, CRs and NULs removed; \n \r \t \xHH and \/ as
//       escapes
//   run until <loc> <op> <value> [max <ms>]
//       op == != < > or & (any bit set); the value at <loc> (1, 2 or 4
//       bytes, little-endian: the symbol's size if it is one of those, else
//       1; or <loc>/<n>) is checked at every slice's end, 256 cycles
//       max defaults to 60000 ms
//   sym <name>                        ok <space> 0x<addr> <size> (space as for peek;
//                                     size: up to the next global of its area)
//   peek <loc> <len>                  ok <hex>
//       <loc>: <space>:<addr or symbol>, or a symbol. Spaces: x xdata, i
//       idata (0-0xff), s SFR (as the CPU reads it), c code in the current
//       bank, b<n> code in bank n (below 0x4000: the common code RAM)
//   poke <loc> <hex>                  ok (x, i and s)
//   uart write <text>                 ok; onto the wire, after what is on it: a byte
//       arrives every character time (10 bit-times at the programmed baud
//       rate, 87 µs at RTLPlayground's 115200), whether the firmware reads
//       it or not; one that finds RI still set is lost (checks: uart-lost).
//       Escapes \\ \r \n \t \xHH; "hex:<hex>" for raw bytes. A power cut
//       drops what is still on the wire
//   uart read                         ok <n>, then a line per line of output since
//       the last read: "<t> <text>", t when its first byte was written to
//       SBUF (TI sets a character time later); the
//       last, if unfinished, "<t>+ <text>" (the rest comes in the next read);
//       one that a reset or power cut ended is "<t>+" too, and what follows
//       starts a new line.
//       CRs removed; \ and bytes outside 0x20-0x7e but tab as \\ and \xHH
//   nic inject <port> <hex>           ok taken | ok dropped (a frame on physical port,
//       dropped if the port has no link; on a forwarding port the L2 table
//       learns its unicast source, as the fabric's does (no ageing);
//       an untagged IGMP one on a port the firmware set to trap IGMP reaches
//       the CPU as it came in, with no 802.1Q tag, as the fabric's does; an
//       RLDP probe (0x8899, protocol 0x23) goes to the chip's loop detection,
//       sys/rldp.h, not the CPU)
//   nic stats                         ok rx <n> dropped <n> off <n> no_cpu_vlan <n>
//       ring_full <n> tx <n> tx_dropped <n> ring <used>/<limit>: the NIC's counts
//       since the last power-on (dropped = off + no_cpu_vlan + ring_full), and
//       the RX ring's 8-byte slots in use now, of what frames may take. The
//       ring holds --nic-rx-ring bytes (10 KiB, sys/nic.h); a frame that finds
//       it full is dropped, and checks lists the first of each run of such
//       drops as nic-rx-full
//   nic capture on|off                ok
//   nic capture read                  ok <n>, then "<t> <port> <hex>" per frame sent
//       out of a port with a link (the --link ones, and link up): the CPU's,
//       and the loop detection's probes (sys/rldp.h)
//   link <port> up [<speed>] | down   ok (physical port 0-9; speed as --link, 1g)
//   checks                            ok <n>, then "<t> <kind> <text>": everything
//       the model found since the start, t when it found it (kinds:
//       unmodelled, bank-ret, uart-lost, bank, flash-busy, early-reset,
//       nic-rx-full;
//       stack is to come). The
//       bank checker (--check-banks) sums up its findings when asked: its
//       lines carry the time of the checks request, not of the read.
//   cov reset | cov dump <file>       ok: which instructions ran since the start
//       or the last cov reset, over power cuts and resets, written to <file>
//       for tools/cov_report.py (format and regions: sys/coverage.h)
//   reg dump                          ok <n>, then the switch core's stored state,
//       sorted: "r <addr> <value>" per register, "s <id>.<page>.<reg> <value>"
//       per SerDes and "p <phy>.<mmd>.<reg> <value>" per PHY register (hex).
//       What was written or seeded, not what a read hook would make of it
//   table dump vlan                   ok <n>, then "<vid> <word>" per VID whose
//       entry isn't 0 (hex)
//   table dump l2                     ok <n>, then "<idx> <a> <b> <c>" per entry
//       present (hex; L3 multicast ones too), by index
//   flash read <addr> <len>           ok <hex>: the flash chip's bytes (up to 64 KiB)
//   reg hold <addr> <ormask>          ok: bits held set, to force a poll's timeout
//       (hex): every firmware read of switch register <addr> (the SFR
//       register-read path) returns its value | ormask; writes are stored as
//       usual. The models' own use of the register doesn't see it. E.g. 5cac
//       1 (TBL_CTRL execute), 0f60 1 (STAT_GET), 53d4 1ffff (L2 flush), 0418 1
//       (I2C_CTRL). Kept over a power cut, as link is
//   reg release <addr> | all          ok
//   phy hold <phy> <dev> <reg> <ormask>   ok: the same for a PHY register read
//       over SMI (phy and dev decimal, reg and mask hex), e.g. the RTL8224's
//       SDS_INDACS_CMD busy bit: <phy> 30 3f8 8000 (the firmware tests bit 15,
//       SFR_DATA_8 & 0x80). A mask on a register that reads 0 sets it: the
//       sim models no RTL8224, so its ID (MMD 30 reg 5, 0x8224) must be held
//       too for the firmware to take that path
//   phy release <phy> <dev> <reg> | all   ok
//   reg busy 5cac 1 <n> | reg busy off   ok: a table command takes time, as on
//       silicon: after a firmware write to TBL_CTRL with execute (bit 0) set,
//       the next n reads of TBL_CTRL (n decimal) return the bit still set, and
//       the table engine does the command at the read after them, with the
//       inputs (TBL_DATA_0, DATA_IN_A-C) it was started with; until then the
//       outputs keep their old contents. A command written while one is busy
//       is dropped, and checks lists it (table-busy). Kept over a power cut
//   stat set <port> <counter> <hex>   ok: MIB counter <counter> (the STAT_GET
//       index, decimal) of logical <port> holds the 64-bit value <hex>, as the
//       firmware's STAT_GET reads it (STAT_V_HIGH, STAT_V_LOW). Nothing counts
//       on its own; a power cut clears them, as a new chip
//   holds                             ok <n>, then "r <addr> <mask>", "p <phy>.<dev>.<reg>
//       <mask>", "flash wp" and "reg busy 5cac 1 <n>" for what is held
//   flash wp on|off                   ok: write protect: write enable (0x06) is
//       ignored, so the status's WEL stays clear and no program or erase
//       happens (flash_write_enable's WEL wait times out). Kept over a power cut
//   sfp <slot> <hex> | sfp <slot> empty   ok: a module in SFP cage slot (the
//       firmware's, 1-based), its EEPROM 256 or 512 bytes as for --sfp; or
//       pulled. Kept over a power cut, as link is
//   reset                             ok <t>: a chip reset, as the firmware's
//                                     (an early one hangs the chip too, below)
//   powercut                          ok <t>: off and on: a new chip around the same
//                                     flash; links stay as they are
//   quit                              ok, and rtlsim exits: 0; 7 if a chip reset
//                                     hung the chip (below); else 6 if the firmware
//                                     read the bank window while the flash was busy
//
// The flash takes its time (unless --no-flash-timing): a sector erase 40 ms,
// a page program of n bytes (the controller sends up to 4) 10.5 + (n - 1)
// µs, a controller command (status, write enable, program) 1 µs, a data
// read 8 µs: fitted to the 8x, where a 4-byte flash_write_bytes() takes
// 49-55 µs and a 256-byte one 1.71 ms (sys/flashctl.h); a 64K block erase
// 150 ms. While it is busy the bank window reads 0x00 (as the 8x's does) and every fetch, movc or
// movx read from it is a violation: checks lists the first of each
// operation as flash-busy, with PC, PSBANK, SP and the firmware time. A power
// cut while an operation is in flight leaves it done in part, in proportion
// to the time it had (an erase erased its first bytes, a program wrote its
// first ones).
//
// A chip reset made by the application (started by the loader) before the
// switch init hangs the chip, as the 8x's did (sys/system.h: the bench
// result, and the proxy for "the switch init", the exact silicon condition
// being unknown; a reset by the loader itself boots): the chip resets, then the CPU
// never runs again - no UART, frames in dropped (nic inject: ok dropped),
// runs time out - until a powercut. checks lists it once as early-reset,
// with PC, PSBANK and the firmware time of the reset; rtlsim exits with 7.
// The firmware's own resets and the reset request both count.
// --no-early-reset-hang: every reset boots, as before.
#ifndef RTLSIM_CONTROL_H
#define RTLSIM_CONTROL_H

#include "sys/system.h"

#include <functional>
#include <memory>

struct control_options {
	const char *path = nullptr;
	const char *map = nullptr;	// --map: the firmware's .map, for symbols
	bool quiet = false;		// findings only through checks, not on stderr
	bool flash_timing = true;	// the flash busy model (--no-flash-timing: off)
	bool early_reset_hang = true;	// --no-early-reset-hang: off
	u16 switch_init_reg = 0;	// --switch-init REG:MASK: the write that ends the switch init
	u32 switch_init_mask = 0;	// (none: no early-reset hang)
	unsigned nic_rx_ring = 10240;	// --nic-rx-ring: the NIC RX ring's capacity, bytes (sys/nic.h)
};

// Builds a system around a flash chip as the command line says (the board,
// its modules, the --link cables): at the start and after each power cut.
using system_factory = std::function<std::unique_ptr<rtl837x_system>(std::unique_ptr<flash_controller>)>;

int control_main(const control_options &opt, std::unique_ptr<rtl837x_system> sys, system_factory make);

#endif
