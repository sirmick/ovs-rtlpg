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
//       POSIX extended regex, CRs removed; \n \r \t \xHH and \/ as escapes
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
//   uart write <text>                 ok; queued for the firmware to read. Escapes
//                                     \\ \r \n \t \xHH; "hex:<hex>" for raw bytes
//   uart read                         ok <n>, then a line per line of output since
//       the last read: "<t> <text>", t when its first byte went out; the
//       last, if unfinished, "<t>+ <text>" (the rest comes in the next read);
//       one that a reset or power cut ended is "<t>+" too, and what follows
//       starts a new line.
//       CRs removed; \ and bytes outside 0x20-0x7e but tab as \\ and \xHH
//   nic inject <port> <hex>           ok taken | ok dropped (a frame on physical port)
//   nic capture on|off                ok
//   nic capture read                  ok <n>, then "<t> <port> <hex>" per frame sent
//       out of a port with a link (the --link ones, and link up)
//   link <port> up [<speed>] | down   ok (physical port 0-9; speed as --link, 1g)
//   checks                            ok <n>, then "<t> <kind> <text>": everything
//       the model found since the start, t when it found it (kinds:
//       unmodelled, bank-ret, bank; flash-busy and stack are to come). The
//       bank checker (--check-banks) sums up its findings when asked: its
//       lines carry the time of the checks request, not of the read.
//   cov reset | cov dump <file>       ok (coverage: not yet; dump says err)
//   reset                             ok <t>: a chip reset, as the firmware's
//   powercut                          ok <t>: off and on: a new chip around the same
//                                     flash; links stay as they are
//   quit                              ok, and rtlsim exits 0
#ifndef RTLSIM_CONTROL_H
#define RTLSIM_CONTROL_H

#include "sys/system.h"

#include <functional>
#include <memory>

struct control_options {
	const char *path = nullptr;
	const char *map = nullptr;	// --map: the firmware's .map, for symbols
	bool quiet = false;		// findings only through checks, not on stderr
};

// Builds a system around a flash chip as the command line says (the board,
// its modules, the --link cables): at the start and after each power cut.
using system_factory = std::function<std::unique_ptr<rtl837x_system>(std::unique_ptr<flash_controller>)>;

int control_main(const control_options &opt, std::unique_ptr<rtl837x_system> sys, system_factory make);

#endif
