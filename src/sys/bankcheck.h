// bankcheck.h - catch constants read through the wrong code bank, at run time.
//
// The bug class: code reads a __code constant with movc while PSBANK
// selects a bank other than the one the constant was linked into, so it
// reads whatever that bank holds at the address. The RTLPlayground repack
// moved files' codeseg but not their constseg: "IGMP-Entry FOUND" printed as
// "ntry FOUND", another module's string read in the wrong bank.
//
// Ground truth comes from the link: the SDCC relocated listings (*.rst next
// to the map) give every byte's final 24-bit address (bank << 16 | addr),
// whether it is code or data (.ascii/.db/.dw ...), its module and its label.
// A movc read at addr with bank b mapped is reported when
//   - the byte there in bank b is code (data read from instructions), or
//   - the reading code's module has its own data at addr in another bank,
//     and bank b holds something else there (the repack bug).
// A read by common code (print_string() and the like) counts as its banked
// caller's: the last code run in a bank window.
// Reads from banks the map links nothing into (read_flash() over raw flash)
// are not checked.
#ifndef RTLSIM_BANKCHECK_H
#define RTLSIM_BANKCHECK_H

#include "emu.h"

#include <map>
#include <set>
#include <string>
#include <vector>

class bank_checker
{
public:
	// The map (banks, global symbols) and every *.rst in its directory.
	bool load_map(const std::string &path);
	size_t listed_bytes() const { return m_listed; }

	// Every instruction run in a bank window: the caller of common code
	// (print_string() and the like) is the last of these.
	void banked_pc(u8 bank, u16 pc) { m_caller = lin(bank, pc); }
	void movc_read(u8 pc_bank, u16 pc, u8 bank, u16 addr);
	// Print the suspicious reads; returns how many.
	int report(FILE *out) const;

private:
	struct byte_info {
		u16 module = 0;		// index + 1 into m_modules; 0 = not listed
		bool code = false;
		u32 label = 0;		// linear address of the label it follows
	};
	static u32 lin(u8 bank, u16 a) { return a < 0x4000 ? a : (u32(bank & 0x3f) << 16) | a; }
	void load_rst(const std::string &path);
	std::string where(u32 linear) const;
	const byte_info &at(u32 linear) const { return m_bytes[linear & 0x3fffff]; }

	std::set<u8> m_banks;			// banks the map links code into
	std::vector<std::string> m_modules;
	std::vector<byte_info> m_bytes = std::vector<byte_info>(64 << 16);
	std::map<u32, std::string> m_labels;	// linear address -> label
	size_t m_listed = 0;
	struct finding { u32 reader, caller, lo, hi; unsigned count; std::string why; };
	std::map<std::pair<u32, u32>, finding> m_findings;
	size_t m_reads = 0;
	u32 m_caller = 0;
};

#endif
