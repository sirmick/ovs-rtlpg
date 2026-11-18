// coverage.cpp - see coverage.h.
#include "sys/coverage.h"

#include <algorithm>
#include <cstdio>

void coverage::reset()
{
	for (auto &b : bits)
		std::fill(b.begin(), b.end(), 0);
}

bool coverage::dump(const std::string &path) const
{
	FILE *f = fopen(path.c_str(), "wb");
	if (!f)
		return false;
	fwrite("RTLCOV1\n", 1, 8, f);
	for (int r = 0; r < 2; r++)
		for (unsigned bank = 0; bank < BANKS; bank++) {
			u8 const *p = &bits[r][bank << 13];
			if (std::all_of(p, p + 8192, [](u8 v) { return v == 0; }))
				continue;
			u8 const hdr[4] = {u8(r), u8(bank), 0, 0};
			fwrite(hdr, 1, 4, f);
			fwrite(p, 1, 8192, f);
		}
	return fclose(f) == 0;
}
