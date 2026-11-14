// rtlsim - boot a flash image on the RTL837x model. UART out on stdout, in
// from stdin (so a test harness can drive the CLI over pipes).
//
//   rtlsim [--image fw.bin] [--flash state.bin] [--flash-size 512K] [--cycles N] [--trace]
//          [--trace-sfr] [--trace-reg] [--trace-flash] [--quiet] [--stats]
//          [--nic PATH --nic-port N] [--trace-nic] [--realtime|--no-realtime]
//          [--sfp SLOT=EEPROM.bin] [--trace-i2c] [--ofp PATH] [--trace-of] [--board FILE]
//          [--print-flows] [--ageing S] [--powercut-after N] [--flash-ops FILE]
//          [--rtl8238b-e8 N] [--rtl8238b-alt-arm] [--rtl8238b-dump FILE] [--trace-pse]
//          [--flash-timing] [--trace-reset] [--early-reset-hangs] [--flash-secreg FILE]
//
// --flash keeps the chip in a file: created (erased, with --image programmed
// at 0) when missing, used as it is otherwise, so the saved config and
// updates persist from run to run. What the firmware erases and programs
// reaches the file's storage within 50 ms (msync), so a crash, or a VM
// stopped without a shutdown, loses no more than that. Without it the chip is
// --image, in memory.
//
// --link PORT=SPEED (repeatable) plugs a cable into a physical port.
//
// --nic PATH --nic-port N wires physical port N straight to the CPU port,
// through a UNIX stream socket at PATH in QEMU -netdev stream framing (see
// net/stream.h); the port gets a 1G link unless --link says otherwise.
// --trace-nic logs every frame, with the CPU's busy cycles per reply.
//
// --ofp PATH makes rtlsim the OpenFlow controller of an OVS bridge, which
// connects to the UNIX socket at PATH (see net/openflow.h): OpenFlow port N
// is physical port N - 1, its link follows the OVS port's (at the --link
// speed if given, else 1G). The bridge's flows are compiled from the chip's
// state (fabric/fabric.h): the front ports switch as the firmware configured
// them, frames for the CPU are packet-ins, and the CPU's frames go out as
// packet-outs. --trace-of logs the connection, port changes, learning and
// ageing. --nic and --ofp can be used together, on different ports.
//
// --realtime keeps the firmware's clock (31.25 M machine cycles a second)
// from running ahead of the wall clock while a peer is connected, so a peer's
// TCP timers and the firmware's agree; it is on with --nic. With no peer
// nobody sees the clock, so the boot still takes a fraction of a second
// instead of silicon's 8 s. Without it the simulation runs as fast as it can.
//
// --board FILE describes the hardware around the chip (sys/board.h): the
// chip's identity, the flash chip (its size unless --flash-size says
// otherwise), the front ports and SFP cages, and the modules in them. Without
// it, the bench SWTG024AS (boards/swgt024_v2_0_unmanaged.json).
//
// --sfp SLOT=FILE puts a module in SFP cage SLOT (1, 2): FILE is its EEPROM,
// 256 bytes (SFF-8472 A0h) or 512 (A0h, then the A2h diagnostics); see
// tools/mksfp.py. --trace-i2c logs every I2C transfer.
//
// --ageing S sets the L2 ageing time (300 s), for tests that watch it.
//
// --print-flows prints the flow set the chip's state compiles to
// (fabric/compiler.h) on stdout when rtlsim ends.
//
// --flash-timing makes the flash chip take as long as a real one (a page
// program ~1.9 ms, a sector erase ~45 ms, sys/flashctl.h): updates, installs
// and config saves then take their real time. Off, everything is at once.
//
// --powercut-after N cuts the power at the flash's (N+1)th erase or page
// program since power-on: that one and anything after it doesn't happen, and
// rtlsim exits at once with 5. With --flash the file keeps what was written,
// so the next run is the next power-on. --flash-ops FILE logs each erase
// ("E addr") and page program ("P addr len") done; --stats counts them.
// --flash-secreg FILE is what the flash chip's security registers hold (read
// command 0x48; erased without it). Vendor firmware may check them.
//
// On a board with an RTL8238B PoE controller (sys/rtl8238b.h), --rtl8238b-e8
// N is what its chip-type register reads (2: the firmware loads image 1),
// --rtl8238b-alt-arm makes it arm only by the firmware's alternate sequence,
// --rtl8238b-dump FILE writes each image it takes, and --trace-pse
// logs its resets, arming and images.
//
// --trace-reset logs each chip reset (reg 0x24) to stderr with its PC and the
// UART lines sent before it, so a test can tell an early reset (before the
// boot's banner) from a late one, and whether setup_clock() had run (a write
// to HW_CONF, 0x6040, since the last reset).
//
// --early-reset-hangs makes a chip reset issued before setup_clock() hang
// until a power cycle, as silicon's boot0 resets do (the working hypothesis:
// resets after setup_clock() boot, resets before it hang). Off by default.
//
// --check-banks MAP reports constants read with movc from bytes that ran as
// code in the same bank (the const-bank bug class; see sys/bankcheck.h).
//
// Exits 0 when the program halts ("sjmp ."), 2 when the cycle budget runs out
// (--cycles 0: no budget), 3 when stdin closes after input was given, 4 when
// the bank check found something, 5 on a power cut (--powercut-after).
#include "sys/system.h"
#include "net/stream.h"
#include "net/openflow.h"
#include "fabric/fabric.h"

#include <chrono>
#include <cstring>
#include <strings.h>
#include <fstream>
#include <iterator>
#include <poll.h>
#include <signal.h>
#include <unistd.h>

static constexpr double CYCLES_PER_S = 125e6 / 4;	// the DW8051's machine cycles

static std::vector<u8> load(const char *path)
{
	std::ifstream f(path, std::ios::binary);
	if (!f) {
		fprintf(stderr, "rtlsim: cannot read %s\n", path);
		exit(1);
	}
	return std::vector<u8>(std::istreambuf_iterator<char>(f), {});
}

// --link PORT=SPEED: a physical port with a cable in at start.
static bool parse_link(const char *s, unsigned *port, int *speed)
{
	static const std::pair<const char *, int> speeds[] = {
		{"10m", 0}, {"100m", 1}, {"1g", 2}, {"10g", 4}, {"2g5", 5}, {"5g", 6}, {"down", rtl837x_system::LINK_DOWN},
	};
	const char *eq = strchr(s, '=');
	if (!eq)
		return false;
	*port = atoi(s);
	for (auto &[n, v] : speeds)
		if (!strcasecmp(eq + 1, n)) {
			*speed = v;
			return *port < 10;
		}
	return false;
}

static u32 parse_size(const char *s)
{
	char *end;
	unsigned long v = strtoul(s, &end, 0);
	if (*end == 'K' || *end == 'k')
		v <<= 10;
	else if (*end == 'M' || *end == 'm')
		v <<= 20;
	return v;
}

// Pass stdin to the UART; false once it is closed.
static bool pump_stdin(rtl837x_system &sys)
{
	struct pollfd p = { 0, POLLIN, 0 };
	if (poll(&p, 1, 0) <= 0)
		return true;
	char buf[256];
	ssize_t n = read(0, buf, sizeof buf);
	if (n <= 0)
		return false;
	sys.uart_input(std::string(buf, n));
	return true;
}

int main(int argc, char **argv)
{
	const char *image = nullptr, *flash_file = nullptr, *check_map = nullptr, *nic_path = nullptr, *ofp_path = nullptr;
	const char *board_file = nullptr;
	bool flash_size_given = false;
	int nic_port = -1, realtime = -1;
	bool trace_nic = false, trace_i2c = false, trace_of = false, print_flows = false;
	fabric_config fcfg;
	long powercut_after = -1;
	bool flash_timing = false;
	long pse_e8 = 0;
	bool pse_alt = false, trace_pse = false, trace_reset = false, early_reset_hangs = false;
	const char *pse_dump = nullptr;
	const char *flash_ops = nullptr;
	const char *flash_secreg = nullptr;
	std::vector<std::pair<unsigned, std::string>> sfps;
	std::vector<std::pair<unsigned, int>> links;
	long long cycles = 100000000;
	u32 flash_size = 0x80000;
	bool trace = false, trace_sfr = false, trace_reg = false, trace_flash = false, quiet = false, stats = false;
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--image") && i + 1 < argc)
			image = argv[++i];
		else if (!strcmp(argv[i], "--flash") && i + 1 < argc)
			flash_file = argv[++i];
		else if (!strcmp(argv[i], "--link") && i + 1 < argc) {
			unsigned p;
			int sp;
			if (!parse_link(argv[++i], &p, &sp)) {
				fprintf(stderr, "rtlsim: --link PORT=10m|100m|1g|2g5|5g|10g|down (physical port 0-9)\n");
				return 1;
			}
			links.emplace_back(p, sp);
		} else if (!strcmp(argv[i], "--nic") && i + 1 < argc)
			nic_path = argv[++i];
		else if (!strcmp(argv[i], "--nic-port") && i + 1 < argc)
			nic_port = atoi(argv[++i]);
		else if (!strcmp(argv[i], "--sfp") && i + 1 < argc) {
			const char *eq = strchr(argv[++i], '=');
			if (!eq) {
				fprintf(stderr, "rtlsim: --sfp SLOT=EEPROM.bin\n");
				return 1;
			}
			sfps.emplace_back(atoi(argv[i]), eq + 1);
		} else if (!strcmp(argv[i], "--board") && i + 1 < argc)
			board_file = argv[++i];
		else if (!strcmp(argv[i], "--ofp") && i + 1 < argc)
			ofp_path = argv[++i];
		else if (!strcmp(argv[i], "--flash-timing"))
			flash_timing = true;
		else if (!strcmp(argv[i], "--powercut-after") && i + 1 < argc)
			powercut_after = atol(argv[++i]);
		else if (!strcmp(argv[i], "--flash-ops") && i + 1 < argc)
			flash_ops = argv[++i];
		else if (!strcmp(argv[i], "--flash-secreg") && i + 1 < argc)
			flash_secreg = argv[++i];
		else if (!strcmp(argv[i], "--rtl8238b-e8") && i + 1 < argc)
			pse_e8 = strtol(argv[++i], nullptr, 0);
		else if (!strcmp(argv[i], "--rtl8238b-alt-arm"))
			pse_alt = true;
		else if (!strcmp(argv[i], "--rtl8238b-dump") && i + 1 < argc)
			pse_dump = argv[++i];
		else if (!strcmp(argv[i], "--trace-pse"))
			trace_pse = true;
		else if (!strcmp(argv[i], "--ageing") && i + 1 < argc)
			fcfg.ageing_s = atoi(argv[++i]);
		else if (!strcmp(argv[i], "--print-flows"))
			print_flows = true;
		else if (!strcmp(argv[i], "--trace-of"))
			trace_of = true;
		else if (!strcmp(argv[i], "--trace-i2c"))
			trace_i2c = true;
		else if (!strcmp(argv[i], "--trace-nic"))
			trace_nic = true;
		else if (!strcmp(argv[i], "--realtime"))
			realtime = 1;
		else if (!strcmp(argv[i], "--no-realtime"))
			realtime = 0;
		else if (!strcmp(argv[i], "--check-banks") && i + 1 < argc)
			check_map = argv[++i];
		else if (!strcmp(argv[i], "--cycles") && i + 1 < argc)
			cycles = atoll(argv[++i]);
		else if (!strcmp(argv[i], "--flash-size") && i + 1 < argc) {
			flash_size = parse_size(argv[++i]);
			flash_size_given = true;
		} else if (!strcmp(argv[i], "--trace"))
			trace = true;
		else if (!strcmp(argv[i], "--trace-sfr"))
			trace_sfr = true;
		else if (!strcmp(argv[i], "--trace-reg"))
			trace_reg = true;
		else if (!strcmp(argv[i], "--trace-flash"))
			trace_flash = true;
		else if (!strcmp(argv[i], "--quiet"))
			quiet = true;
		else if (!strcmp(argv[i], "--stats"))
			stats = true;
		else if (!strcmp(argv[i], "--trace-reset"))
			trace_reset = true;
		else if (!strcmp(argv[i], "--early-reset-hangs"))
			early_reset_hangs = true;
		else {
			fprintf(stderr, "usage: rtlsim [--image fw.bin] [--flash state.bin] [--flash-size 512K] [--cycles N] [--trace] "
				"[--trace-sfr] [--trace-reg] [--trace-flash] [--link P=SPEED] [--check-banks MAP] [--quiet] [--stats] "
				"[--nic PATH --nic-port N] [--trace-nic] [--realtime|--no-realtime] [--sfp SLOT=EEPROM.bin] [--trace-i2c] "
				"[--ofp PATH] [--trace-of] [--board FILE] [--print-flows] [--ageing S] "
				"[--powercut-after N] [--flash-ops FILE] [--rtl8238b-e8 N] [--rtl8238b-alt-arm] "
				"[--rtl8238b-dump FILE] [--trace-pse] [--flash-timing] [--trace-reset] [--early-reset-hangs] [--flash-secreg FILE]\n");
			return 1;
		}
	}
	if (!image && !flash_file) {
		fprintf(stderr, "rtlsim: --image or --flash is required\n");
		return 1;
	}
	if (!nic_path != (nic_port < 0) || nic_port > 9) {
		fprintf(stderr, "rtlsim: --nic PATH and --nic-port N (physical port 0-9) go together\n");
		return 1;
	}
	board brd = board_swtg024as();
	if (board_file) {
		try {
			brd = load_board(board_file);
		} catch (const std::exception &e) {
			fprintf(stderr, "rtlsim: %s\n", e.what());
			return 1;
		}
		if (!flash_size_given)
			flash_size = brd.flash_size;
	}
	if (flash_size & (flash_size - 1)) {
		fprintf(stderr, "rtlsim: --flash-size must be a power of two\n");
		return 1;
	}

	std::vector<u8> img = image ? load(image) : std::vector<u8>();
	std::unique_ptr<flash_controller> flash;
	try {
		flash = flash_file ? std::make_unique<flash_controller>(flash_file, img, flash_size, brd.jedec_mfr, brd.jedec_type)
				   : std::make_unique<flash_controller>(img, flash_size, brd.jedec_mfr, brd.jedec_type);
	} catch (const std::exception &e) {
		fprintf(stderr, "rtlsim: %s\n", e.what());
		return 1;
	}
	rtl837x_system sys(std::move(flash), brd);
	sys.trace_insn = trace;
	sys.trace_reset = trace_reset;
	sys.early_reset_hangs = early_reset_hangs;
	sys.trace_sfr = trace_sfr;
	sys.core().trace = trace_reg;
	sys.core().log_unmodelled = !quiet;
	sys.flash().trace = trace_flash;
	sys.flash().timing = flash_timing;
	if (powercut_after >= 0)
		sys.flash().power_cut_after(powercut_after);
	if (flash_secreg)
		sys.flash().secreg = load(flash_secreg);
	if (flash_ops && !(sys.flash().op_log = fopen(flash_ops, "w"))) {
		fprintf(stderr, "rtlsim: cannot write %s\n", flash_ops);
		return 1;
	}
	for (auto &[p, sp] : links)
		sys.set_link(p, sp);
	sys.i2c().trace = trace_i2c;
	if (auto *pse = sys.pse()) {
		pse->e8 = pse_e8;
		pse->alt_arm = pse_alt;
		pse->trace = trace_pse;
		if (pse_dump)
			pse->on_image = [pse_dump](const std::vector<u8> &img) {
				if (FILE *f = fopen(pse_dump, "wb")) {
					fwrite(img.data(), 1, img.size(), f);
					fclose(f);
				}
			};
	}
	for (auto &c : brd.sfp) {	// the board's modules, unless --sfp says otherwise
		bool given = false;
		for (auto &[slot, path] : sfps)
			given |= slot == c.slot;
		if (!c.module.empty() && !given)
			sfps.emplace_back(c.slot, c.module);
	}
	for (auto &[slot, path] : sfps) {
		std::vector<u8> e = load(path.c_str());
		if (e.size() != 256 && e.size() != 512) {
			fprintf(stderr, "rtlsim: %s: an SFP EEPROM is 256 or 512 bytes\n", path.c_str());
			return 1;
		}
		if (!sys.set_sfp(slot, e)) {
			fprintf(stderr, "rtlsim: the %s board has no SFP slot %u\n", sys.board_info().name.c_str(), slot);
			return 1;
		}
	}
	std::unique_ptr<stream_port> wire;
	std::unique_ptr<of_controller> ofp;
	try {
		if (nic_path)
			wire = std::make_unique<stream_port>(nic_path);
		if (ofp_path)
			ofp = std::make_unique<of_controller>(ofp_path);
	} catch (const std::exception &e) {
		fprintf(stderr, "rtlsim: %s\n", e.what());
		return 1;
	}
	signal(SIGPIPE, SIG_IGN);
	if (wire) {
		if (sys.link(nic_port) == rtl837x_system::LINK_DOWN)
			sys.set_link(nic_port, 2);
		sys.nic().peers = 1u << nic_port;
	}
	if (ofp) {
		ofp->trace = trace_of;
		ofp->port_status = [&](unsigned p, bool exists, bool up) {
			if (int(p) == nic_port)
				return;
			int speed = 2;
			for (auto &[lp, sp] : links)
				if (lp == p && sp != rtl837x_system::LINK_DOWN)
					speed = sp;
			sys.set_link(p, up ? speed : rtl837x_system::LINK_DOWN);
			sys.nic().peers = (sys.nic().peers & ~(1u << p)) | (u16(exists) << p);
		};
	}
	sys.nic().transmit = [&](unsigned p, const std::vector<u8> &f) {
		if (wire && int(p) == nic_port)
			wire->send(f);
		else if (ofp)
			ofp->packet_out(p, f);
	};
	std::unique_ptr<fabric> fab;
	if (ofp) {
		fab = std::make_unique<fabric>(sys, *ofp, fcfg);
		fab->trace = trace_of;
	}
	sys.nic().trace = trace_nic;
	if (realtime < 0)
		realtime = wire || ofp;
	if (check_map) {
		sys.bankcheck = std::make_unique<bank_checker>();
		if (!sys.bankcheck->load_map(check_map)) {
			fprintf(stderr, "rtlsim: cannot read %s\n", check_map);
			return 1;
		}
	}

	auto t0 = std::chrono::steady_clock::now();
	auto pace_t0 = t0, synced = t0;
	u64 pace_c0 = 0;
	bool stdin_open = !isatty(0) || true;
	int rc = 2;
	for (u64 done = 0; cycles == 0 || done < u64(cycles);) {
		// slices of 100 us of firmware time when paced, so frames wait little
		bool const paced = realtime && ((wire && wire->connected()) || (ofp && ofp->connected()));
		int const slice = paced ? 3125 : 100000;
		done += slice;
		if (stdin_open && sys.uart_idle())
			stdin_open = pump_stdin(sys);
		if (wire)
			wire->poll([&](const u8 *f, size_t n) { sys.nic().receive(nic_port, f, n); });
		if (ofp) {
			ofp->poll();
			fab->update();
		}
		if (!sys.run(slice)) {
			rc = 0;
			break;
		}
		if (auto now = std::chrono::steady_clock::now(); now - synced > std::chrono::milliseconds(50)) {
			sys.flash().sync();
			synced = now;
		}
		if (sys.flash().power_cut()) {
			sys.flash().sync();
			fprintf(stderr, "rtlsim: power cut after %u flash operations\n", sys.flash().ops());
			rc = 5;
			break;
		}
		fflush(stdout);	// a prompt doesn't end in a newline
		if (!paced) {
			pace_t0 = std::chrono::steady_clock::now();
			pace_c0 = sys.cycles;
		} else {
			// sleep while ahead of the wall clock, waking for input; when
			// behind by more than 100 ms, let it go rather than race
			double const wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - pace_t0).count();
			double const ahead = (sys.cycles - pace_c0) / CYCLES_PER_S - wall;
			if (ahead > 0) {
				struct pollfd p[3] = { { stdin_open ? 0 : -1, POLLIN, 0 }, { wire ? wire->fd() : -1, POLLIN, 0 },
						       { ofp ? ofp->fd() : -1, POLLIN, 0 } };
				struct timespec ts = { time_t(ahead), long((ahead - time_t(ahead)) * 1e9) };
				ppoll(p, 3, &ts, nullptr);
			} else if (ahead < -0.1) {
				pace_t0 = std::chrono::steady_clock::now();
				pace_c0 = sys.cycles;
			}
		}
		if (!stdin_open && !wire && !ofp && sys.uart_idle() && cycles == 0) {
			// input ended: let the firmware finish its answer, then stop
			sys.run(20000000);
			rc = 3;
			break;
		}
	}
	if (print_flows)
		printf("\n--- flows\n%s", flows_text(compile_flows(sys.core(), sys.tables(), sys.board_info(), fcfg)).c_str());
	sys.flash().sync();
	fflush(stdout);
	if (sys.bankcheck && sys.bankcheck->report(stderr) && rc != 2)
		rc = 4;
	double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
	if (stats) {
		fprintf(stderr, "rtlsim: %llu machine cycles in %.3f s (%.1f M/s), %llu busy, %llu idle-skipped, %llu resets\n",
			(unsigned long long)sys.cycles, s, sys.cycles / s / 1e6, (unsigned long long)sys.cpu().busy_cycles,
			(unsigned long long)sys.cpu().idle_skipped, (unsigned long long)sys.resets);
		auto &n = sys.nic().stats;
		auto &d = sys.flash().dev();
		fprintf(stderr, "rtlsim: flash: %u erases, %u programs, %u wrapped past the end, %u wrote without erase\n",
			d.erases, d.writes, d.wrapped_ops, d.wrote_without_erase);
		if (fab)
			fprintf(stderr, "rtlsim: fabric: %llu compiles, %zu flows, %llu learned, %llu aged out\n",
				(unsigned long long)fab->compiles, ofp->flows_installed(),
				(unsigned long long)fab->learned, (unsigned long long)fab->aged);
		if (wire || ofp)
			fprintf(stderr, "rtlsim: NIC rx %llu (%llu dropped), tx %llu (%llu dropped)\n",
				(unsigned long long)n.rx, (unsigned long long)n.rx_dropped,
				(unsigned long long)n.tx, (unsigned long long)n.tx_dropped);
	}
	return rc;
}
