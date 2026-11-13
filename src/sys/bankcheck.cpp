// bankcheck.cpp - see bankcheck.h.
#include "sys/bankcheck.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <regex>

static bool is_hex2(const std::string &t)
{
	return t.size() == 2 && isxdigit(t[0]) && isxdigit(t[1]);
}

bool bank_checker::load_map(const std::string &path)
{
	std::ifstream f(path);
	if (!f)
		return false;
	std::regex area(R"(^BANK(\d+)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8}))");
	std::string line;
	std::smatch m;
	while (std::getline(f, line))
		if (std::regex_search(line, m, area) && std::stoul(m[3], nullptr, 16))
			m_banks.insert(u8(std::stoul(m[1])));
	auto dir = std::filesystem::path(path).parent_path();
	if (dir.empty())
		dir = ".";
	for (auto &e : std::filesystem::directory_iterator(dir))
		if (e.path().extension() == ".rst")
			load_rst(e.path().string());
	return true;
}

// An .rst line: address in columns 6-11, up to 7 bytes in 13-32, then the
// source line number and the source. Continuation lines carry more bytes and
// no address.
void bank_checker::load_rst(const std::string &path)
{
	std::ifstream f(path);
	m_modules.push_back(std::filesystem::path(path).stem().string());
	u16 const mod = m_modules.size();
	std::string line;
	u32 addr = 0, label = 0;
	bool code = false;
	while (std::getline(f, line)) {
		if (line.size() < 13)
			continue;
		std::string const a = line.substr(6, 6);
		bool const has_addr = a.find_first_not_of("0123456789ABCDEFabcdef") == std::string::npos;
		if (has_addr) {
			addr = std::stoul(a, nullptr, 16);
			// skip the source line number, then the separating whitespace
			std::string src = line.size() > 33 ? line.substr(33) : "";
			size_t p = src.find_first_not_of(" ");
			p = p == std::string::npos ? src.size() : src.find_first_not_of("0123456789", p);
			p = p == std::string::npos ? src.size() : src.find_first_not_of(" \t", p);
			src = p == std::string::npos ? "" : src.substr(p);
			if (!src.empty() && src.back() == ':' && src.find_first_of(" \t") == std::string::npos) {
				// function and data labels; SDCC's local "00101$:" ones are not names
				if (src.find('$') == std::string::npos) {
					label = addr;
					m_labels[addr] = src.substr(0, src.size() - 1);
				}
				continue;
			}
			static const std::regex data(R"(^\.(ascii|asciz|db|dw|byte|word|str|strz|ds|fcb|fdb)\b)");
			code = !src.empty() && src[0] != '.' && src[0] != ';';
			if (std::regex_search(src, data))
				code = false;
		} else if (line.compare(0, 13, std::string(13, ' ')) != 0) {
			continue;
		}
		std::string const bytes = line.substr(13, std::min<size_t>(20, line.size() - 13));
		size_t i = 0;
		while (i + 2 <= bytes.size()) {
			std::string t = bytes.substr(i, 2);
			if (is_hex2(t)) {
				byte_info &b = m_bytes[addr & 0x3fffff];
				b.module = mod;
				b.code = code;
				b.label = label;
				addr++;
				m_listed++;
			}
			i += 3;
		}
	}
}

std::string bank_checker::where(u32 linear) const
{
	const byte_info &b = at(linear);
	char buf[160];
	auto it = m_labels.find(b.label);
	const char *mod = b.module ? m_modules[b.module - 1].c_str() : "?";
	if (b.module && it != m_labels.end())
		snprintf(buf, sizeof buf, "%s:%s+0x%x%s", mod, it->second.c_str(), unsigned(linear - b.label), b.code ? " (code)" : "");
	else
		snprintf(buf, sizeof buf, "%s%s", mod, b.code ? " (code)" : "");
	return buf;
}

void bank_checker::movc_read(u8 pc_bank, u16 pc, u8 bank, u16 addr)
{
	bank &= 0x3f;
	if (addr < 0x4000 || !m_banks.count(bank))
		return;
	m_reads++;
	u32 const reader = lin(pc_bank, pc), target = lin(bank, addr);
	u32 const caller = pc < 0x4000 ? m_caller : reader;	// whose read it is
	const byte_info &t = at(target);
	u16 const rmod = at(caller).module;
	// where the reader's module has its own data at this address, if in another bank
	std::string own;
	for (u8 b : m_banks) {
		const byte_info &o = at(lin(b, addr));
		if (b != bank && rmod && o.module == rmod && !o.code) {
			own = "; " + m_modules[rmod - 1] + " has its constant there in bank " + std::to_string(b) + ": " + where(lin(b, addr));
			break;
		}
	}
	// otherwise, the data other banks hold at this address: likely what was
	// meant (a hint only: it doesn't make a finding by itself)
	if (own.empty() && t.code) {
		for (u8 b : m_banks) {
			const byte_info &o = at(lin(b, addr));
			if (b != bank && o.module && !o.code)
				own += "; bank " + std::to_string(b) + " has data there: " + where(lin(b, addr));
		}
	}
	std::string why;
	if (t.code)
		why = "reads code as data" + own;
	else if (rmod && t.module != rmod && own.find(" has its constant there") != std::string::npos)
		why = "reads another module's data" + own;
	else
		return;
	auto key = std::make_pair(reader, t.label ? t.label : target);
	auto it = m_findings.find(key);
	if (it == m_findings.end())
		m_findings[key] = {reader, caller, target, target, 1, why};
	else {
		it->second.lo = std::min(it->second.lo, target);
		it->second.hi = std::max(it->second.hi, target);
		it->second.count++;
	}
}

int bank_checker::report(FILE *out) const
{
	int n = 0;
	for (auto &[k, f] : m_findings) {
		if (!n++)
			fprintf(out, "bank check: constants read through the wrong bank:\n");
		std::string by = where(f.reader);
		if (f.caller != f.reader)
			by += ", called from " + where(f.caller);
		fprintf(out, "  %02x:%04x %s\n      read %02x:%04x-%04x (%u bytes) %s\n      %s\n", f.reader >> 16, f.reader & 0xffff,
			by.c_str(), f.lo >> 16, f.lo & 0xffff, f.hi & 0xffff, f.count, where(f.lo).c_str(), f.why.c_str());
	}
	if (!n)
		fprintf(out, "bank check: clean (%zu banked constant reads, %zu listed bytes)\n", m_reads, m_listed);
	return n;
}
