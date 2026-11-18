// coverage.h - which instructions ran: a bit per code address, by bank.
//
// An address is linear as in the link: bank << 16 | address, the common
// area (below 0x4000) as bank 0, bank b = PSBANK (1 if its bit 6 is set).
// A bit is set when an instruction starting there runs.
//
// Two regions, for the common area's two lives on a boot image with
// RTLPlayground's loader (LOADER=1): region 0, "boot", is what runs from
// the common code as reset loads it from flash (the loader on such an
// image, the application on any other); region 1, "loaded", is what runs
// once software has loaded the common code RAM itself (the loader's
// app_start(): the application). The application's coverage is region 1
// if there is any, else region 0; tools/cov_report.py picks so.
//
// The dump, for tools/cov_report.py: "RTLCOV1\n", then per (region, bank)
// with any bit set: region (1 byte), bank (1 byte), 2 zero bytes, and the
// 65536 bits of addresses 0-0xffff (8 KiB, address a is bit a & 7 of byte
// a >> 3).
#ifndef RTLSIM_COVERAGE_H
#define RTLSIM_COVERAGE_H

#include "emu.h"

#include <string>
#include <vector>

struct coverage {
	static constexpr unsigned BANKS = 64;
	std::vector<u8> bits[2] = {std::vector<u8>(BANKS << 13), std::vector<u8>(BANKS << 13)};

	void hit(int region, u8 psbank, u16 pc)
	{
		u32 const bank = pc < 0x4000 ? 0 : (psbank & 0x40) ? 1 : (psbank & 0x3f);
		u32 const a = bank << 16 | pc;
		bits[region][a >> 3] |= u8(1 << (a & 7));
	}
	void reset();
	bool dump(const std::string &path) const;
};

#endif
