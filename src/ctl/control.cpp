// control.cpp - see control.h.
#include "ctl/control.h"
#include "ctl/symmap.h"
#include "sys/rldp.h"
#include "sys/report.h"

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <map>
#include <cstring>
#include <regex.h>
#include <sstream>
#include <strings.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <tuple>
#include <unistd.h>

namespace {

constexpr u64 SLICE = 256;		// cycles; the CPU runs to multiples of it
constexpr u64 DEFAULT_MAX_US = 60000000;

struct ctl_error : std::runtime_error {
	using std::runtime_error::runtime_error;
};

std::string fmt_time(u64 cycles)
{
	u64 const us = cycles * 4 / 125;	// 31.25 cycles a µs
	char b[32];
	snprintf(b, sizeof b, "%llu.%03llu", (unsigned long long)(us / 1000), (unsigned long long)(us % 1000));
	return b;
}

// "12", "0.5", "2213.408" ms -> cycles
u64 parse_ms(const std::string &s)
{
	size_t dot = s.find('.');
	std::string const whole = s.substr(0, dot), frac = dot == std::string::npos ? "" : s.substr(dot + 1);
	if (whole.empty() || frac.size() > 3 || whole.find_first_not_of("0123456789") != std::string::npos ||
	    frac.find_first_not_of("0123456789") != std::string::npos)
		throw ctl_error("bad time (ms, up to three decimals): " + s);
	u64 us = std::stoull(whole) * 1000;
	if (!frac.empty())
		us += std::stoull(frac + std::string(3 - frac.size(), '0'));
	return us * 125 / 4;
}

u64 parse_num(const std::string &s)
{
	char *end;
	errno = 0;
	unsigned long long v = strtoull(s.c_str(), &end, 0);
	if (s.empty() || *end || errno)
		throw ctl_error("bad number: " + s);
	return v;
}

std::vector<u8> parse_hex(const std::string &s)
{
	if (s.size() % 2 || s.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
		throw ctl_error("bad hex: " + s);
	std::vector<u8> v;
	for (size_t i = 0; i < s.size(); i += 2)
		v.push_back(u8(std::stoul(s.substr(i, 2), nullptr, 16)));
	return v;
}

std::string hex(const u8 *p, size_t n)
{
	static const char d[] = "0123456789abcdef";
	std::string s;
	for (size_t i = 0; i < n; i++) {
		s += d[p[i] >> 4];
		s += d[p[i] & 15];
	}
	return s;
}

// \\ \r \n \t \xHH (and, for regexes, \/) -> bytes; any other \c stays as it is
std::string unescape(const std::string &s, bool regex)
{
	std::string o;
	for (size_t i = 0; i < s.size(); i++) {
		if (s[i] != '\\' || i + 1 == s.size()) {
			o += s[i];
			continue;
		}
		char const c = s[++i];
		if (c == 'n')
			o += '\n';
		else if (c == 'r')
			o += '\r';
		else if (c == 't')
			o += '\t';
		else if (c == 'x' && i + 2 < s.size() && isxdigit(u8(s[i + 1])) && isxdigit(u8(s[i + 2]))) {
			o += char(std::stoul(s.substr(i + 1, 2), nullptr, 16));
			i += 2;
		} else if (c == '/' && regex)
			o += '/';
		else if (c == '\\' && !regex)
			o += '\\';
		else {
			o += '\\';
			o += c;
		}
	}
	return o;
}

std::string escape(const std::string &s)
{
	std::string o;
	for (u8 c : s) {
		if (c == '\\')
			o += "\\\\";
		else if ((c < 0x20 && c != '\t') || c > 0x7e) {
			char b[8];
			snprintf(b, sizeof b, "\\x%02x", c);
			o += b;
		} else
			o += char(c);
	}
	return o;
}

std::vector<std::string> words(const std::string &s)
{
	std::istringstream in(s);
	std::vector<std::string> w;
	for (std::string x; in >> x;)
		w.push_back(x);
	return w;
}

int parse_speed(const std::string &s)
{
	static const std::pair<const char *, int> speeds[] = {
		{"10m", 0}, {"100m", 1}, {"1g", 2}, {"10g", 4}, {"2g5", 5}, {"5g", 6},
	};
	for (auto &[n, v] : speeds)
		if (!strcasecmp(s.c_str(), n))
			return v;
	throw ctl_error("bad speed (10m 100m 1g 2g5 5g 10g): " + s);
}

class controller
{
public:
	controller(const control_options &o, std::unique_ptr<rtl837x_system> s, system_factory f)
		: m_opt(o), m_sys(std::move(s)), m_make(std::move(f))
	{
		if (o.map && !m_map.load(o.map))
			throw std::runtime_error(std::string("cannot read ") + o.map);
		report_sink = [this](const char *kind, const std::string &text) {
			m_findings.emplace_back(now(), kind, text);
			if (!m_opt.quiet)
				fprintf(stderr, "%s\n", text.c_str());
		};
		attach();
	}

	// One request; false after quit.
	bool request(const std::string &line, std::string &reply);
	// The exit code: 7 if a reset hung the chip, else 6 if the firmware
	// broke the flash busy rule.
	int exit_code() const { return m_early_resets || m_sys->held() ? 7 : m_busy_violations + m_sys->flash_busy_violations ? 6 : 0; }

private:
	enum class stop { done, hit, halt };
	struct loc {
		char space;	// x i s c b
		u8 bank;	// b
		u32 addr;
		u32 size;	// a symbol's, else 0
	};
	struct uart_line {
		u64 t;
		std::string text;
		bool done;
	};

	u64 now() const { return m_base + m_sys->cpu().cycles_total; }
	void attach();
	void peers();
	stop advance(u64 target, const std::function<bool()> &on_stop, const std::function<bool()> &on_slice);
	loc parse_loc(const std::string &s) const;
	std::vector<u8> peek(const loc &l, u32 len);
	void poke(const loc &l, const std::vector<u8> &d);
	bool uart_matches();
	std::string run_until(const std::vector<std::string> &w);
	void powercut();
	void learn(unsigned port, const std::vector<u8> &frame);
	static std::string block(const std::vector<std::string> &lines);

	control_options m_opt;
	std::unique_ptr<rtl837x_system> m_sys;
	std::unique_ptr<rldp_engine> m_rldp;	// the chip's loop detection, as the fabric's
	system_factory m_make;
	symbol_map m_map;
	u64 m_base = 0;				// firmware time at the last power-on
	u64 m_busy_violations = 0;		// in the systems before the last power cut
	u64 m_early_resets = 0;			// the same, chips an early reset hung
	coverage m_cov;				// over power cuts, until cov reset
	std::map<unsigned, int> m_links;	// link requests, kept over a power cut
	std::map<unsigned, std::vector<u8>> m_sfps;	// sfp requests, the same
	std::map<u16, u32> m_holds;		// reg hold, the same
	std::map<u32, u16> m_phy_holds;		// phy hold, the same
	bool m_flash_wp = false;		// flash wp, the same
	unsigned m_tbl_busy = 0;		// reg busy 5cac, the same
	bool m_ring_full = false;		// dropping for want of room in the RX ring

	std::vector<uart_line> m_uart;		// since the last uart read
	std::string m_uart_text;		// the same, for run until uart
	bool m_uart_open = false;		// the last line is unfinished
	regex_t *m_uart_re = nullptr;		// while running until it matches

	bool m_capture = false;
	std::vector<std::tuple<u64, unsigned, std::vector<u8>>> m_frames;
	std::vector<std::tuple<u64, std::string, std::string>> m_findings;
};

// What the system does that control mode watches: everything it sends, and
// the flash's clock in firmware time.
void controller::attach()
{
	rtl837x_system &s = *m_sys;
	s.slice = SLICE;
	s.slice_phase = m_base % SLICE;
	s.cpu().exact_time = true;
	s.cpu().cov = &m_cov;
	s.flash().timing = m_opt.flash_timing;
	s.flash().t.sector_erase = 1250000;	// 40 ms
	s.flash().t.program_first_ns = 10500;	// a program: 10.5 + (n - 1) 1 µs, and
	s.flash().t.program_byte_ns = 1000;	// a command 1 µs, fitted to the 8x
	s.flash().t.command = 32;		// (flashctl.h)
	s.flash().t.page_max_ns = 400000;	// tPP, for a whole page
	s.flash().t.block_erase = 4687500;	// 150 ms (W25Q16JV, typical)
	s.flash().keep_before = true;
	s.flash_busy_check = m_opt.flash_timing;
	s.early_reset_hang = m_opt.early_reset_hang;
	s.cpu().uart_timing = true;
	s.cpu().uart_lost = [this](u8 c, bool ri) {
		report("uart-lost", "uart: received 0x%02x lost, %s", c, ri ? "RI still set (the last byte unread)" : "reception off (REN)");
	};
	s.core().log_unmodelled = true;
	s.flash().now = [this]() { return now(); };
	s.cpu().uart_tx = [this](u8 c) {
		if (c == '\n')
			m_sys->uart_lines++;
		if (c == '\r')
			return;
		if (!m_uart_open) {
			m_uart.push_back({now(), "", false});
			m_uart_open = true;
		}
		if (c == '\n') {
			m_uart.back().done = true;
			m_uart_open = false;
		} else
			m_uart.back().text += char(c);
		if (c)			// regexes see no NULs (uart read does, as \x00)
			m_uart_text += char(c);
		if (m_uart_re)
			m_sys->cpu().stop_request = true;
	};
	s.nic().rx_ring_cap = m_opt.nic_rx_ring;
	m_ring_full = false;
	s.nic().ring_full = [this](unsigned p, size_t len, unsigned used, unsigned limit) {
		if (!m_ring_full)
			report("nic-rx-full", "nic: RX ring full, a %zu-byte frame from port %u dropped (%u of %u slots in use), "
			       "and any more until a frame fits", len, p, used, limit);
		m_ring_full = true;
	};
	s.nic().transmit = [this](unsigned p, const std::vector<u8> &f) {
		if (m_capture)
			m_frames.emplace_back(now(), p, f);
	};
	m_rldp = std::make_unique<rldp_engine>(s);
	m_rldp->out = [this](unsigned p, const std::vector<u8> &f) {
		if (m_capture && m_sys->link(p) != rtl837x_system::LINK_DOWN)
			m_frames.emplace_back(now(), p, f);
	};
	for (auto &[p, sp] : m_links)
		s.set_link(p, sp);
	for (auto &[slot, e] : m_sfps)
		s.set_sfp(slot, e);
	s.core().held = m_holds;
	s.core().phy_held = m_phy_holds;
	s.flash().write_protect = m_flash_wp;
	s.tables().busy_reads = m_tbl_busy;
	peers();
}

// A port with a cable has something on the wire: frames the CPU sends out
// of it are captured.
void controller::peers()
{
	u16 m = 0;
	for (unsigned p = 0; p < 10; p++)
		if (m_sys->link(p) != rtl837x_system::LINK_DOWN)
			m |= 1u << p;
	m_sys->nic().peers = m;
}

// Run to `target` in slices ending on multiples of SLICE; on_stop decides
// whether a stop of the CPU (breakpoint, UART byte) ends it, on_slice is
// asked at each slice's end.
controller::stop controller::advance(u64 target, const std::function<bool()> &on_stop, const std::function<bool()> &on_slice)
{
	auto &cpu = m_sys->cpu();
	while (now() < target) {
		u64 const t = now();
		u64 const n = std::min(target - t, SLICE - t % SLICE);
		cpu.stopped = false;
		bool const alive = m_sys->run(int(n));
		m_rldp->tick();
		bool const stopped = cpu.stopped;
		cpu.stopped = false;
		cpu.stop_request = false;
		if (!alive)
			return stop::halt;
		if (stopped) {
			if (on_stop && on_stop())
				return stop::hit;
		} else if (on_slice && on_slice())
			return stop::hit;
	}
	return stop::done;
}

controller::loc controller::parse_loc(const std::string &s) const
{
	size_t const colon = s.find(':');
	std::string sp = colon == std::string::npos ? "" : s.substr(0, colon);
	std::string const what = colon == std::string::npos ? s : s.substr(colon + 1);
	loc l{};
	if (!what.empty() && isdigit(u8(what[0]))) {
		l.addr = u32(parse_num(what));
	} else {
		const map_symbol *m = m_map.find(what);
		if (!m)
			throw ctl_error(m_map.size() ? "no symbol " + what : "no symbol " + what + " (no --map)");
		if (m->space == 'b')
			throw ctl_error(what + " is a bit");
		l.addr = m->addr & 0xffff;
		l.size = m->size;
		if (sp.empty()) {
			sp = std::string(1, m->space);
			if (m->space == 'c')
				sp = "b" + std::to_string(m->bank());
		}
		if (m->space == 'x' || m->space == 'i')
			l.addr = m->addr;
	}
	if (sp.empty())
		sp = "c";
	if (sp.size() > 1 && sp[0] == 'b') {
		l.space = 'b';
		l.bank = u8(parse_num(sp.substr(1)));
	} else if (sp == "x" || sp == "i" || sp == "s" || sp == "c")
		l.space = sp[0];
	else
		throw ctl_error("bad space " + sp + " (x i s c b<n>)");
	return l;
}

std::vector<u8> controller::peek(const loc &l, u32 len)
{
	if (len > 0x10000)
		throw ctl_error("too long");
	std::vector<u8> d(len);
	for (u32 i = 0; i < len; i++) {
		u32 const a = l.addr + i;
		switch (l.space) {
		case 'x': d[i] = m_sys->peek_xdata(u16(a)); break;
		case 'i': d[i] = m_sys->cpu().iram(u8(a)); break;
		case 's': d[i] = m_sys->peek_sfr(u8(a)); break;
		case 'c': d[i] = m_sys->code_read(u16(a)); break;
		case 'b': d[i] = m_sys->bank_byte(l.bank, u16(a)); break;
		}
	}
	return d;
}

void controller::poke(const loc &l, const std::vector<u8> &d)
{
	for (u32 i = 0; i < d.size(); i++) {
		u32 const a = l.addr + i;
		switch (l.space) {
		case 'x': m_sys->poke_xdata(u16(a), d[i]); break;
		case 'i': m_sys->cpu().iram_w(u8(a), d[i]); break;
		case 's': m_sys->poke_sfr(u8(a), d[i]); break;
		default: throw ctl_error("poke: x, i or s");
		}
	}
}

bool controller::uart_matches()
{
	// m_uart_text has no NULs, so the whole of it is the C string (musl,
	// the VM's build, has no REG_STARTEND)
	return regexec(m_uart_re, m_uart_text.c_str(), 0, nullptr, 0) == 0;
}

std::string controller::block(const std::vector<std::string> &lines)
{
	std::string r = "ok " + std::to_string(lines.size());
	for (auto &l : lines)
		r += "\n" + l;
	return r;
}

// run until ...: w[0] "run", w[1] "until"
std::string controller::run_until(const std::vector<std::string> &w)
{
	auto max_after = [&](size_t i) -> u64 {
		if (i == w.size())
			return DEFAULT_MAX_US * 125 / 4;
		if (i + 2 == w.size() && w[i] == "max")
			return parse_ms(w[i + 1]);
		throw ctl_error("expected: max <ms>");
	};
	auto answer = [this](stop s) {
		return std::string(s == stop::hit ? "ok hit " : s == stop::halt ? "ok halt " : "ok timeout ") + fmt_time(now());
	};
	if (w.size() < 3)
		throw ctl_error("run until pc=<loc> | uart=/<re>/ | <loc> <op> <value>");
	if (w[2].rfind("pc=", 0) == 0) {
		loc const l = parse_loc(w[2].substr(3));
		if (l.space != 'c' && l.space != 'b')
			throw ctl_error("pc= takes a code address");
		u64 const target = now() + max_after(3);
		auto &cpu = m_sys->cpu();
		cpu.break_pc = int(l.addr & 0xffff);
		cpu.break_bank = l.space == 'b' && l.addr >= 0x4000 ? l.bank : -1;
		stop s;
		try {
			s = advance(target, [] { return true; }, nullptr);
		} catch (...) {
			cpu.break_pc = cpu.break_bank = -1;
			throw;
		}
		cpu.break_pc = cpu.break_bank = -1;
		return answer(s);
	}
	// <loc> <op> <value>
	if (w.size() < 5)
		throw ctl_error("run until <loc> <op> <value> [max <ms>]");
	std::string spec = w[2];
	u32 width = 0;
	if (size_t sl = spec.rfind('/'); sl != std::string::npos) {
		width = u32(parse_num(spec.substr(sl + 1)));
		spec = spec.substr(0, sl);
	}
	loc const l = parse_loc(spec);
	if (!width)
		width = l.size == 1 || l.size == 2 || l.size == 4 ? l.size : 1;
	if (width != 1 && width != 2 && width != 4)
		throw ctl_error("width: 1, 2 or 4");
	std::string const op = w[3];
	if (op != "==" && op != "!=" && op != "<" && op != ">" && op != "&")
		throw ctl_error("op: == != < > &");
	u64 const v = parse_num(w[4]);
	u64 const target = now() + max_after(5);
	auto cond = [&]() {
		auto d = peek(l, width);
		u64 x = 0;
		for (u32 i = 0; i < width; i++)
			x |= u64(d[i]) << (8 * i);
		return op == "==" ? x == v : op == "!=" ? x != v : op == "<" ? x < v : op == ">" ? x > v : (x & v) != 0;
	};
	if (cond())
		return answer(stop::hit);
	return answer(advance(target, nullptr, cond));
}

// Hardware learning, as the fabric does it (fabric.cpp, the learn table): a
// frame in on a forwarding port (MSTP_STATES 11) teaches the L2 table its
// unicast source in its VLAN - the frame's tag, else the port's PVID. No
// ageing here (the fabric ages entries by idle flows): an entry stays until
// the firmware flushes it.
void controller::learn(unsigned p, const std::vector<u8> &f)
{
	switch_core &c = m_sys->core();
	if ((c.reg(0x5310) >> (2 * p) & 3) != 3)
		return;
	u16 const vid = f[12] == 0x81 && f[13] == 0x00 && f.size() >= 16 ? ((f[14] << 8) | f[15]) & 0xfff
		: (c.reg(u16(0x4e1c + (p >> 1) * 4)) >> ((p & 1) ? 12 : 0)) & 0xfff;
	m_sys->tables().learn(&f[6], vid, p);
}

void controller::powercut()
{
	u64 const t = now();
	auto bc = std::move(m_sys->bankcheck);
	m_busy_violations += m_sys->flash_busy_violations;
	m_early_resets += m_sys->held();
	auto flash = m_sys->take_flash();
	m_sys.reset();
	flash->power_on(t);
	m_sys = m_make(std::move(flash));
	if (!m_sys)
		throw std::runtime_error("cannot rebuild the system");
	m_sys->bankcheck = std::move(bc);
	m_base = t;
	attach();
}

bool controller::request(const std::string &line, std::string &reply)
{
	auto w = words(line);
	try {
		if (w.empty())
			throw ctl_error("empty request");
		std::string const &c = w[0];
		if (c == "time" && w.size() == 1) {
			reply = "ok " + std::to_string(now()) + " " + fmt_time(now());
		} else if (c == "run" && w.size() >= 3 && w[1] == "until" && w[2].rfind("uart=/", 0) == 0) {
			// the regex runs to the first unescaped '/', and may hold spaces
			size_t const start = line.find("uart=/") + 6;
			size_t end = start;
			while (end < line.size() && line[end] != '/')
				end += line[end] == '\\' ? 2 : 1;
			if (end >= line.size())
				throw ctl_error("uart=/<re>/: no closing /");
			std::string const re = unescape(line.substr(start, end - start), true);
			auto rest = words(line.substr(end + 1));
			u64 max = DEFAULT_MAX_US * 125 / 4;
			if (rest.size() == 2 && rest[0] == "max")
				max = parse_ms(rest[1]);
			else if (!rest.empty())
				throw ctl_error("expected: max <ms>");
			regex_t rx;
			if (int e = regcomp(&rx, re.c_str(), REG_EXTENDED | REG_NOSUB)) {
				char b[128];
				regerror(e, &rx, b, sizeof b);
				throw ctl_error(std::string("regex: ") + b);
			}
			m_uart_re = &rx;
			stop s = stop::hit;
			if (!uart_matches())
				s = advance(now() + max, [this] { return uart_matches(); }, nullptr);
			m_uart_re = nullptr;
			regfree(&rx);
			reply = std::string(s == stop::hit ? "ok hit " : s == stop::halt ? "ok halt " : "ok timeout ") + fmt_time(now());
		} else if (c == "run" && w.size() >= 2 && w[1] == "until") {
			reply = run_until(w);
		} else if (c == "run" && w.size() == 2) {
			stop s = advance(now() + parse_ms(w[1]), nullptr, nullptr);
			reply = (s == stop::halt ? "ok halt " : "ok ") + fmt_time(now());
		} else if (c == "sym" && w.size() == 2) {
			const map_symbol *m = m_map.find(w[1]);
			if (!m)
				throw ctl_error("no symbol " + w[1]);
			std::string sp = m->space == 'c' ? "b" + std::to_string(m->bank()) : std::string(1, m->space);
			if (m->space == 'c' && m->bank() == 0)
				sp = "c";
			char b[64];
			snprintf(b, sizeof b, "ok %s 0x%04x %u", sp.c_str(), m->space == 'c' ? m->addr & 0xffff : m->addr, m->size);
			reply = b;
		} else if (c == "peek" && w.size() == 3) {
			auto d = peek(parse_loc(w[1]), u32(parse_num(w[2])));
			reply = "ok " + hex(d.data(), d.size());
		} else if (c == "poke" && w.size() == 3) {
			poke(parse_loc(w[1]), parse_hex(w[2]));
			reply = "ok";
		} else if (c == "uart" && w.size() >= 2 && w[1] == "write") {
			size_t const p = line.find("write") + 5;
			std::string t = p < line.size() ? line.substr(p + 1) : "";
			if (t.rfind("hex:", 0) == 0) {
				auto d = parse_hex(t.substr(4));
				t.assign(d.begin(), d.end());
			} else
				t = unescape(t, false);
			m_sys->cpu().uart_rx_queue(t);
			reply = "ok";
		} else if (c == "uart" && w.size() == 2 && w[1] == "read") {
			std::vector<std::string> l;
			for (auto &u : m_uart)
				l.push_back(fmt_time(u.t) + (u.done ? " " : "+ ") + escape(u.text));
			m_uart.clear();
			m_uart_text.clear();
			m_uart_open = false;
			reply = block(l);
		} else if (c == "nic" && w.size() == 4 && w[1] == "inject") {
			unsigned const p = unsigned(parse_num(w[2]));
			if (p > 9)
				throw ctl_error("port: 0-9");
			auto f = parse_hex(w[3]);
			// The IGMP trap, as the fabric has it (fabric/compiler.cpp): with the
			// port's IGMP_PORT_CFG trap bits set, an untagged IGMP frame reaches
			// the CPU as it came in, with no 802.1Q tag
			bool const igmp = f.size() >= 24 && f[12] == 0x08 && f[13] == 0x00 && f[23] == 2 &&
					  (m_sys->core().reg(u16(0x52a0 + 4 * p)) & 0x2a) == 0x2a;
			if (m_sys->link(p) != rtl837x_system::LINK_DOWN && f.size() >= 14)
				learn(p, f);
			if (m_sys->link(p) == rtl837x_system::LINK_DOWN)
				reply = "ok dropped";			// nothing arrives on a port without a link
			else if (f.size() >= 15 && f[12] == 0x88 && f[13] == 0x99 && f[14] == 0x23) {
				m_rldp->rx(p, f.data(), f.size());	// a loop-detection probe: the chip's
				reply = "ok taken";
			} else
				{
				bool const taken = !m_sys->held() && m_sys->nic().receive(p, f.data(), f.size(), igmp);
				if (taken)
					m_ring_full = false;
				reply = taken ? "ok taken" : "ok dropped";
			}
		} else if (c == "nic" && w.size() == 2 && w[1] == "stats") {
			auto &n = m_sys->nic();
			char b[256];
			snprintf(b, sizeof b, "ok rx %llu dropped %llu off %llu no_cpu_vlan %llu ring_full %llu tx %llu tx_dropped %llu ring %u/%u",
				 (unsigned long long)n.stats.rx, (unsigned long long)n.stats.rx_dropped, (unsigned long long)n.stats.rx_off,
				 (unsigned long long)n.stats.rx_no_cpu_vlan, (unsigned long long)n.stats.rx_ring_full,
				 (unsigned long long)n.stats.tx, (unsigned long long)n.stats.tx_dropped, n.rx_used(), n.rx_limit());
			reply = b;
		} else if (c == "nic" && w.size() == 3 && w[1] == "capture" && (w[2] == "on" || w[2] == "off")) {
			m_capture = w[2] == "on";
			reply = "ok";
		} else if (c == "nic" && w.size() == 3 && w[1] == "capture" && w[2] == "read") {
			std::vector<std::string> l;
			for (auto &[t, p, f] : m_frames)
				l.push_back(fmt_time(t) + " " + std::to_string(p) + " " + hex(f.data(), f.size()));
			m_frames.clear();
			reply = block(l);
		} else if (c == "link" && (w.size() == 3 || w.size() == 4) && (w[2] == "up" || w[2] == "down")) {
			unsigned const p = unsigned(parse_num(w[1]));
			if (p > 9)
				throw ctl_error("port: 0-9");
			int const sp = w[2] == "down" ? rtl837x_system::LINK_DOWN : w.size() == 4 ? parse_speed(w[3]) : 2;
			m_links[p] = sp;
			m_sys->set_link(p, sp);
			peers();
			reply = "ok";
		} else if (c == "checks" && w.size() == 1) {
			std::vector<std::string> l;
			for (auto &[t, k, x] : m_findings)
				l.push_back(fmt_time(t) + " " + k + " " + escape(x));
			if (m_sys->bankcheck) {
				char *buf = nullptr;
				size_t len = 0;
				FILE *f = open_memstream(&buf, &len);
				m_sys->bankcheck->report(f);
				fclose(f);
				std::istringstream in(std::string(buf, len));
				free(buf);
				for (std::string x; std::getline(in, x);)
					if (!x.empty())
						l.push_back(fmt_time(now()) + " bank " + escape(x));
			}
			reply = block(l);
		} else if (c == "reg" && w.size() == 2 && w[1] == "dump") {
			char b[64];
			std::vector<std::pair<u32, u32>> r;
			for (auto &[a, v] : m_sys->core().regs())
				r.emplace_back(a, v);
			std::sort(r.begin(), r.end());
			std::vector<std::string> l;
			for (auto &[a, v] : r) {
				snprintf(b, sizeof b, "r %04x %08x", a, v);
				l.push_back(b);
			}
			for (auto *m : {&m_sys->core().sds_regs(), &m_sys->core().phy_regs()}) {
				std::vector<std::pair<u32, u16>> x(m->begin(), m->end());
				std::sort(x.begin(), x.end());
				for (auto &[k, v] : x) {
					snprintf(b, sizeof b, "%c %x.%02x.%04x %04x", m == &m_sys->core().sds_regs() ? 's' : 'p',
						 k >> 24, (k >> 16) & 0xff, k & 0xffff, v);
					l.push_back(b);
				}
			}
			reply = block(l);
		} else if (c == "reg" && w.size() == 4 && w[1] == "hold") {
			u16 const a = u16(parse_num("0x" + w[2]));
			m_holds[a] = u32(parse_num("0x" + w[3]));
			m_sys->core().held = m_holds;
			reply = "ok";
		} else if (c == "reg" && w.size() == 3 && w[1] == "release") {
			if (w[2] == "all")
				m_holds.clear();
			else
				m_holds.erase(u16(parse_num("0x" + w[2])));
			m_sys->core().held = m_holds;
			reply = "ok";
		} else if (c == "phy" && w.size() == 6 && w[1] == "hold") {
			m_phy_holds[switch_core::phy_key(u8(parse_num(w[2])), u8(parse_num(w[3])), u16(parse_num("0x" + w[4])))] =
				u16(parse_num("0x" + w[5]));
			m_sys->core().phy_held = m_phy_holds;
			reply = "ok";
		} else if (c == "phy" && w.size() >= 3 && w[1] == "release") {
			if (w.size() == 3 && w[2] == "all")
				m_phy_holds.clear();
			else if (w.size() == 5)
				m_phy_holds.erase(switch_core::phy_key(u8(parse_num(w[2])), u8(parse_num(w[3])), u16(parse_num("0x" + w[4]))));
			else
				throw ctl_error("phy release <phy> <dev> <reg> | all");
			m_sys->core().phy_held = m_phy_holds;
			reply = "ok";
		} else if (c == "holds" && w.size() == 1) {
			char b[64];
			std::vector<std::string> l;
			for (auto &[a, m] : m_holds) {
				snprintf(b, sizeof b, "r %04x %08x", a, m);
				l.push_back(b);
			}
			for (auto &[k, m] : m_phy_holds) {
				snprintf(b, sizeof b, "p %x.%02x.%04x %04x", k >> 24, (k >> 16) & 0xff, k & 0xffff, m);
				l.push_back(b);
			}
			if (m_flash_wp)
				l.push_back("flash wp");
			if (m_tbl_busy)
				l.push_back("reg busy 5cac 1 " + std::to_string(m_tbl_busy));
			reply = block(l);
		} else if (c == "reg" && w.size() == 5 && w[1] == "busy") {
			if (parse_num("0x" + w[2]) != 0x5cac || parse_num("0x" + w[3]) != 1)
				throw ctl_error("reg busy: only 5cac 1 (TBL_CTRL execute)");
			m_tbl_busy = unsigned(parse_num(w[4]));
			m_sys->tables().busy_reads = m_tbl_busy;
			reply = "ok";
		} else if (c == "reg" && w.size() == 3 && w[1] == "busy" && w[2] == "off") {
			m_tbl_busy = 0;
			m_sys->tables().busy_reads = 0;
			reply = "ok";
		} else if (c == "stat" && w.size() == 5 && w[1] == "set") {
			unsigned const port = unsigned(parse_num(w[2])), cnt = unsigned(parse_num(w[3]));
			if (port > 15 || cnt > 63)
				throw ctl_error("stat set: port 0-15, counter 0-63");
			m_sys->tables().set_counter(u8(port), u8(cnt), std::stoull(w[4], nullptr, 16));
			reply = "ok";
		} else if (c == "flash" && w.size() == 3 && w[1] == "wp" && (w[2] == "on" || w[2] == "off")) {
			m_flash_wp = w[2] == "on";
			m_sys->flash().write_protect = m_flash_wp;
			reply = "ok";
		} else if (c == "table" && w.size() == 3 && w[1] == "dump" && (w[2] == "vlan" || w[2] == "l2")) {
			char b[64];
			std::vector<std::string> l;
			auto &t = m_sys->tables();
			if (w[2] == "vlan") {
				for (unsigned vid = 0; vid < 4096; vid++)
					if (t.vlan(u16(vid))) {
						snprintf(b, sizeof b, "%03x %08x", vid, t.vlan(u16(vid)));
						l.push_back(b);
					}
			} else {
				std::vector<switch_tables::l2_entry> e;
				for (auto &x : t.l2())
					if (x.present)
						e.push_back(x);
				std::sort(e.begin(), e.end(), [](auto &p, auto &q) { return p.idx < q.idx; });
				for (auto &x : e) {
					snprintf(b, sizeof b, "%03x %08x %08x %08x", x.idx, x.a, x.b, x.c);
					l.push_back(b);
				}
			}
			reply = block(l);
		} else if (c == "flash" && w.size() == 4 && w[1] == "read") {
			u32 const a = u32(parse_num(w[2])), n = u32(parse_num(w[3]));
			if (n > 0x10000)
				throw ctl_error("too long");
			std::vector<u8> d(n);
			for (u32 i = 0; i < n; i++)
				d[i] = m_sys->flash().byte(a + i);
			reply = "ok " + hex(d.data(), d.size());
		} else if (c == "sfp" && w.size() == 3) {
			unsigned const slot = unsigned(parse_num(w[1]));
			std::vector<u8> e;
			if (w[2] != "empty") {
				e = parse_hex(w[2]);
				if (e.size() != 256 && e.size() != 512)
					throw ctl_error("an SFP EEPROM is 256 or 512 bytes");
			}
			if (!m_sys->set_sfp(slot, e))
				throw ctl_error("no SFP slot " + std::to_string(slot) + " on this board");
			m_sfps[slot] = e;
			reply = "ok";
		} else if (c == "cov" && w.size() == 2 && w[1] == "reset") {
			m_cov.reset();
			reply = "ok";
		} else if (c == "cov" && w.size() == 3 && w[1] == "dump") {
			if (!m_cov.dump(w[2]))
				throw ctl_error("cannot write " + w[2]);
			reply = "ok";
		} else if (c == "reset" && w.size() == 1) {
			m_uart_open = false;
			m_sys->chip_reset();
			reply = "ok " + fmt_time(now());
		} else if (c == "powercut" && w.size() == 1) {
			m_uart_open = false;
			powercut();
			reply = "ok " + fmt_time(now());
		} else if (c == "quit" && w.size() == 1) {
			reply = "ok";
			return false;
		} else
			throw ctl_error("unknown request: " + line);
	} catch (const ctl_error &e) {
		reply = std::string("err ") + e.what();
	}
	m_sys->flash().sync();
	return true;
}

bool send_all(int fd, const std::string &s)
{
	for (size_t off = 0; off < s.size();) {
		ssize_t n = write(fd, s.data() + off, s.size() - off);
		if (n <= 0)
			return false;
		off += n;
	}
	return true;
}

}	// namespace

int control_main(const control_options &opt, std::unique_ptr<rtl837x_system> sys, system_factory make)
{
	std::unique_ptr<controller> ctl;
	try {
		ctl = std::make_unique<controller>(opt, std::move(sys), std::move(make));
	} catch (const std::exception &e) {
		fprintf(stderr, "rtlsim: %s\n", e.what());
		return 1;
	}
	int ls = socket(AF_UNIX, SOCK_STREAM, 0);
	struct sockaddr_un a = {};
	a.sun_family = AF_UNIX;
	if (strlen(opt.path) >= sizeof a.sun_path) {
		fprintf(stderr, "rtlsim: control socket path too long\n");
		return 1;
	}
	strcpy(a.sun_path, opt.path);
	unlink(opt.path);
	if (ls < 0 || bind(ls, (struct sockaddr *)&a, sizeof a) < 0 || listen(ls, 1) < 0) {
		fprintf(stderr, "rtlsim: cannot listen on %s: %s\n", opt.path, strerror(errno));
		return 1;
	}
	int fd = accept(ls, nullptr, nullptr);
	close(ls);
	unlink(opt.path);
	if (fd < 0) {
		fprintf(stderr, "rtlsim: accept: %s\n", strerror(errno));
		return 1;
	}
	std::string in;
	char buf[4096];
	for (;;) {
		size_t nl;
		while ((nl = in.find('\n')) == std::string::npos) {
			ssize_t n = read(fd, buf, sizeof buf);
			if (n <= 0)
				return ctl->exit_code();	// the client went away
			in.append(buf, n);
		}
		std::string line = in.substr(0, nl);
		in.erase(0, nl + 1);
		if (!line.empty() && line.back() == '\r')
			line.pop_back();
		std::string reply;
		bool const more = ctl->request(line, reply);
		if (!send_all(fd, reply + "\n") || !more)
			return ctl->exit_code();
	}
}
