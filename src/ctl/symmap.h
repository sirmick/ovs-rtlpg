// symmap.h - the global symbols of an SDCC link, from its .map.
//
// The map lists each area (DSEG, XSEG, CSEG, BANK2 ...) with its globals.
// A value's prefix says its space: "C:" code, as bank << 16 | address
// (common code below 0x4000 is bank 0), "D:" xdata; none, the internal RAM
// (DSEG, OSEG, ...) or, in BSEG and BIT_BANK, a bit address. The map has no
// sizes: a symbol's is up to the next global of its area, or the area's end.
#ifndef RTLSIM_SYMMAP_H
#define RTLSIM_SYMMAP_H

#include <cstdint>
#include <map>
#include <string>

struct map_symbol {
	char space;		// 'c' code, 'x' xdata, 'i' internal RAM, 'b' bit
	uint32_t addr;		// code: bank << 16 | address
	uint32_t size;
	uint8_t bank() const { return space == 'c' ? uint8_t(addr >> 16) : 0; }
};

class symbol_map
{
public:
	bool load(const std::string &path);
	// `name` as in the map, or a C name without its leading '_'.
	const map_symbol *find(const std::string &name) const;
	size_t size() const { return m_syms.size(); }

private:
	std::map<std::string, map_symbol> m_syms;
};

#endif
