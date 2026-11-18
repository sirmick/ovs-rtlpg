// symmap.cpp - see symmap.h.
#include "ctl/symmap.h"

#include <algorithm>
#include <fstream>
#include <regex>
#include <vector>

bool symbol_map::load(const std::string &path)
{
	std::ifstream f(path);
	if (!f)
		return false;
	// "XSEG    00000001    000041B1 =   16817. bytes (REL,CON,XDATA)"
	std::regex const area(R"(^(\w+)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8}) =)");
	// "D:   00000EC8  _ticks    rtlplayground"
	std::regex const sym(R"(^([CD]:)?\s+([0-9A-Fa-f]{8})\s+(\S+)\s+\S+\s*$)");
	struct area_syms {
		std::string name;
		uint32_t end;
		std::vector<std::pair<std::string, map_symbol>> syms;
	};
	std::map<std::string, area_syms> areas;	// a long area's table spans pages, each repeating its header
	area_syms *cur = nullptr;
	std::string line;
	std::smatch m;
	while (std::getline(f, line)) {
		if (!line.empty() && line.back() == '\r')
			line.pop_back();
		if (std::regex_search(line, m, area)) {
			std::string const name = m[1];
			uint32_t const addr = std::stoul(m[2], nullptr, 16), len = std::stoul(m[3], nullptr, 16);
			cur = &areas[name];
			cur->name = name;
			cur->end = addr + len;
		} else if (cur && std::regex_match(line, m, sym)) {
			std::string const pre = m[1];
			char space = pre == "C:" ? 'c' : pre == "D:" ? 'x' : 'i';
			if (pre.empty() && (cur->name == "BSEG" || cur->name == "BIT_BANK"))
				space = 'b';
			uint32_t const a = std::stoul(m[2], nullptr, 16);
			cur->syms.push_back({m[3], map_symbol{space, a, 0}});
		}
	}
	for (auto &[n, a] : areas) {
		auto &v = a.syms;
		std::sort(v.begin(), v.end(), [](auto &x, auto &y) { return x.second.addr < y.second.addr; });
		v.erase(std::unique(v.begin(), v.end(), [](auto &x, auto &y) { return x.first == y.first; }), v.end());
		for (size_t i = 0; i < v.size(); i++) {
			uint32_t next = a.end;
			for (size_t j = i + 1; j < v.size() && next == a.end; j++)
				if (v[j].second.addr > v[i].second.addr)
					next = v[j].second.addr;
			v[i].second.size = next > v[i].second.addr ? next - v[i].second.addr : 0;
			m_syms.emplace(v[i].first, v[i].second);
		}
	}
	return true;
}

const map_symbol *symbol_map::find(const std::string &name) const
{
	auto it = m_syms.find(name);
	if (it == m_syms.end())
		it = m_syms.find("_" + name);
	return it == m_syms.end() ? nullptr : &it->second;
}
