// rtlsim - boot a flash image on the RTL837x model. UART out on stdout, in
// from stdin (so a test harness can drive the CLI over pipes).
//
//   rtlsim [--image fw.bin] [--flash state.bin] [--flash-size 512K] [--cycles N] [--trace]
//          [--trace-sfr] [--trace-reg] [--trace-flash] [--quiet] [--stats]
//
// --flash keeps the chip in a file: created (erased, with --image programmed
// at 0) when missing, used as it is otherwise, so the saved config and
// updates persist from run to run. Without it the chip is --image, in memory.
//
// --link PORT=SPEED (repeatable) plugs a cable into a physical port.
//
// --check-banks MAP reports constants read with movc from bytes that ran as
// code in the same bank (the const-bank bug class; see sys/bankcheck.h).
//
// Exits 0 when the program halts ("sjmp ."), 2 when the cycle budget runs out
// (--cycles 0: no budget), 3 when stdin closes after input was given, 4 when
// the bank check found something.
#include "sys/system.h"

#include <chrono>
#include <cstring>
#include <strings.h>
#include <fstream>
#include <iterator>
#include <poll.h>
#include <unistd.h>

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
	const char *image = nullptr, *flash_file = nullptr, *check_map = nullptr;
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
		} else if (!strcmp(argv[i], "--check-banks") && i + 1 < argc)
			check_map = argv[++i];
		else if (!strcmp(argv[i], "--cycles") && i + 1 < argc)
			cycles = atoll(argv[++i]);
		else if (!strcmp(argv[i], "--flash-size") && i + 1 < argc)
			flash_size = parse_size(argv[++i]);
		else if (!strcmp(argv[i], "--trace"))
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
		else {
			fprintf(stderr, "usage: rtlsim [--image fw.bin] [--flash state.bin] [--flash-size 512K] [--cycles N] [--trace] "
				"[--trace-sfr] [--trace-reg] [--trace-flash] [--link P=SPEED] [--check-banks MAP] [--quiet] [--stats]\n");
			return 1;
		}
	}
	if (!image && !flash_file) {
		fprintf(stderr, "rtlsim: --image or --flash is required\n");
		return 1;
	}
	if (flash_size & (flash_size - 1)) {
		fprintf(stderr, "rtlsim: --flash-size must be a power of two\n");
		return 1;
	}

	std::vector<u8> img = image ? load(image) : std::vector<u8>();
	std::unique_ptr<flash_controller> flash;
	try {
		flash = flash_file ? std::make_unique<flash_controller>(flash_file, img, flash_size)
				   : std::make_unique<flash_controller>(img, flash_size);
	} catch (const std::exception &e) {
		fprintf(stderr, "rtlsim: %s\n", e.what());
		return 1;
	}
	rtl837x_system sys(std::move(flash));
	sys.trace_insn = trace;
	sys.trace_sfr = trace_sfr;
	sys.core().trace = trace_reg;
	sys.core().log_unmodelled = !quiet;
	sys.flash().trace = trace_flash;
	for (auto &[p, sp] : links)
		sys.set_link(p, sp);
	if (check_map) {
		sys.bankcheck = std::make_unique<bank_checker>();
		if (!sys.bankcheck->load_map(check_map)) {
			fprintf(stderr, "rtlsim: cannot read %s\n", check_map);
			return 1;
		}
	}

	auto t0 = std::chrono::steady_clock::now();
	bool stdin_open = !isatty(0) || true;
	int rc = 2;
	for (long long done = 0; cycles == 0 || done < cycles; done += 100000) {
		if (stdin_open && sys.uart_idle())
			stdin_open = pump_stdin(sys);
		if (!sys.run(100000)) {
			rc = 0;
			break;
		}
		fflush(stdout);	// a prompt doesn't end in a newline
		if (!stdin_open && sys.uart_idle() && cycles == 0) {
			// input ended: let the firmware finish its answer, then stop
			sys.run(20000000);
			rc = 3;
			break;
		}
	}
	fflush(stdout);
	if (sys.bankcheck && sys.bankcheck->report(stderr) && rc != 2)
		rc = 4;
	double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
	if (stats)
		fprintf(stderr, "rtlsim: %llu machine cycles in %.3f s (%.1f M/s), %llu idle-skipped, %llu resets\n",
			(unsigned long long)sys.cycles, s, sys.cycles / s / 1e6,
			(unsigned long long)sys.cpu().idle_skipped, (unsigned long long)sys.resets);
	return rc;
}
