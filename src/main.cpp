// rtlsim - phase 1 runner: boot a flash image on the RTL837x CPU model,
// UART to stdout.
//
//   rtlsim --image fw.bin [--cycles N] [--trace] [--trace-sfr] [--stats]
//
// Exits 0 when the program halts ("sjmp ."), 2 when the cycle budget runs out.
#include "sys/system.h"

#include <chrono>
#include <cstring>
#include <fstream>
#include <iterator>

static std::vector<u8> load(const char *path)
{
	std::ifstream f(path, std::ios::binary);
	if (!f) {
		fprintf(stderr, "rtlsim: cannot read %s\n", path);
		exit(1);
	}
	return std::vector<u8>(std::istreambuf_iterator<char>(f), {});
}

int main(int argc, char **argv)
{
	const char *image = nullptr;
	long long cycles = 100000000;
	bool trace = false, trace_sfr = false, stats = false;
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--image") && i + 1 < argc)
			image = argv[++i];
		else if (!strcmp(argv[i], "--cycles") && i + 1 < argc)
			cycles = atoll(argv[++i]);
		else if (!strcmp(argv[i], "--trace"))
			trace = true;
		else if (!strcmp(argv[i], "--trace-sfr"))
			trace_sfr = true;
		else if (!strcmp(argv[i], "--stats"))
			stats = true;
		else {
			fprintf(stderr, "usage: rtlsim --image fw.bin [--cycles N] [--trace] [--trace-sfr] [--stats]\n");
			return 1;
		}
	}
	if (!image) {
		fprintf(stderr, "rtlsim: --image is required\n");
		return 1;
	}

	rtl837x_system sys(load(image));
	sys.trace_insn = trace;
	sys.trace_sfr = trace_sfr;

	auto t0 = std::chrono::steady_clock::now();
	long long left = cycles;
	while (left > 0 && sys.run(int(std::min(left, 1000000LL))))
		left -= 1000000;
	double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
	if (stats)
		fprintf(stderr, "rtlsim: %llu machine cycles in %.3f s (%.1f M/s)\n",
			(unsigned long long)sys.cycles, s, sys.cycles / s / 1e6);
	return sys.halted() ? 0 : 2;
}
