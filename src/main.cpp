// rtlsim - boot a flash image on the RTL837x model. UART out on stdout, in
// from stdin (so a test harness can drive the CLI over pipes).
//
//   rtlsim --image fw.bin [--flash-size 512K] [--cycles N] [--trace]
//          [--trace-sfr] [--trace-reg] [--trace-flash] [--quiet] [--stats]
//
// Exits 0 when the program halts ("sjmp ."), 2 when the cycle budget runs out
// (--cycles 0: no budget), 3 when stdin closes after input was given.
#include "sys/system.h"

#include <chrono>
#include <cstring>
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
	const char *image = nullptr;
	long long cycles = 100000000;
	u32 flash_size = 0x80000;
	bool trace = false, trace_sfr = false, trace_reg = false, trace_flash = false, quiet = false, stats = false;
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--image") && i + 1 < argc)
			image = argv[++i];
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
			fprintf(stderr, "usage: rtlsim --image fw.bin [--flash-size 512K] [--cycles N] [--trace] "
				"[--trace-sfr] [--trace-reg] [--trace-flash] [--quiet] [--stats]\n");
			return 1;
		}
	}
	if (!image) {
		fprintf(stderr, "rtlsim: --image is required\n");
		return 1;
	}
	if (flash_size & (flash_size - 1)) {
		fprintf(stderr, "rtlsim: --flash-size must be a power of two\n");
		return 1;
	}

	rtl837x_system sys(load(image), flash_size);
	sys.trace_insn = trace;
	sys.trace_sfr = trace_sfr;
	sys.core().trace = trace_reg;
	sys.core().log_unmodelled = !quiet;
	sys.flash().trace = trace_flash;

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
	double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
	if (stats)
		fprintf(stderr, "rtlsim: %llu machine cycles in %.3f s (%.1f M/s), %llu idle-skipped, %llu resets\n",
			(unsigned long long)sys.cycles, s, sys.cycles / s / 1e6,
			(unsigned long long)sys.cpu().idle_skipped, (unsigned long long)sys.resets);
	return rc;
}
