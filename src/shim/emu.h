// emu.h - the slice of MAME's device framework that the mcs51 core uses,
// so third_party/mame/mcs51 compiles unchanged outside MAME.
//
// Address spaces are small handler tables (SFR, idata) or a pair of external
// callbacks (program, xdata) that the system installs. Save states, the state
// interface and the debugger are no-ops, except the instruction hook.
#ifndef RTLSIM_SHIM_EMU_H
#define RTLSIM_SHIM_EMU_H

#include <array>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <memory>
#include <ostream>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

using u8 = uint8_t;
using u16 = uint16_t;
using u32 = uint32_t;
using u64 = uint64_t;
using s8 = int8_t;
using s16 = int16_t;
using s32 = int32_t;
using s64 = int64_t;
using offs_t = u32;

#define ATTR_COLD
#define NAME(x) x, #x
#define FUNC(x) &x, #x
#define DEVICE_SELF ""

template <typename T, typename U> constexpr T BIT(T x, U n) noexcept { return (x >> n) & T(1); }
template <typename T, typename U, typename V> constexpr T BIT(T x, U n, V w) { return (x >> n) & ((T(1) << w) - 1); }

enum endianness_t { ENDIANNESS_LITTLE, ENDIANNESS_BIG };
enum { AS_PROGRAM = 0, AS_DATA = 1, AS_IO = 2, AS_OPCODES = 3, AS_MAX = 8 };
enum { STATE_GENPC = -1, STATE_GENPCBASE = -2, STATE_GENFLAGS = -3 };
enum { CLEAR_LINE = 0, ASSERT_LINE = 1, HOLD_LINE = 2 };

// --- formatting (MAME's typesafe printf, reduced to snprintf) ---------------
namespace shim {
template <typename T> auto fmt_arg(const T &v)
{
	if constexpr (std::is_same_v<T, std::string>)
		return v.c_str();
	else if constexpr (std::is_enum_v<T>)
		return static_cast<long long>(v);
	else
		return v;
}
template <typename... A> std::string format(const char *fmt, const A &...a)
{
	char buf[512];
	if constexpr (sizeof...(A) == 0)
		snprintf(buf, sizeof buf, "%s", fmt);
	else
		snprintf(buf, sizeof buf, fmt, fmt_arg(a)...);
	return buf;
}
}

namespace util {
template <typename... A> std::string string_format(const char *fmt, const A &...a) { return shim::format(fmt, a...); }
template <typename... A> void stream_format(std::ostream &s, const char *fmt, const A &...a) { s << shim::format(fmt, a...); }
}
using util::string_format;
namespace util {

class disasm_interface
{
public:
	enum : u32 {
		LENGTHMASK = 0x0000ffff,
		STEP_COND = 0x00040000,
		STEP_OUT = 0x00020000,
		STEP_OVER = 0x00010000,
		SUPPORTED = 0x80000000,
	};
	class data_buffer
	{
	public:
		explicit data_buffer(std::function<u8(offs_t)> f) : m_f(std::move(f)) {}
		u8 r8(offs_t pc) const { return m_f(pc); }
	private:
		std::function<u8(offs_t)> m_f;
	};
	virtual ~disasm_interface() = default;
	virtual u32 opcode_alignment() const = 0;
	virtual offs_t disassemble(std::ostream &stream, offs_t pc, const data_buffer &opcodes, const data_buffer &params) = 0;
};
}

// --- device types, machine ---------------------------------------------------
struct machine_config {};
struct device_type_impl { const char *shortname; const char *fullname; };
using device_type = const device_type_impl *;
#define DECLARE_DEVICE_TYPE(Type, Class) extern const device_type Type;
#define DEFINE_DEVICE_TYPE(Type, Class, Short, Full) \
	static const device_type_impl Type##_impl{Short, Full}; \
	const device_type Type = &Type##_impl;

struct attotime { std::string to_string() const { return "-"; } };
class running_machine
{
public:
	bool side_effects_disabled() const { return m_disabled > 0; }
	struct side_effects_guard {
		running_machine *m;
		~side_effects_guard() { m->m_disabled--; }
	};
	side_effects_guard disable_side_effects() { m_disabled++; return side_effects_guard{this}; }
	attotime time() const { return {}; }
private:
	int m_disabled = 0;
};

// --- address spaces ------------------------------------------------------------
using read8_fn = std::function<u8(offs_t)>;
using write8_fn = std::function<void(offs_t, u8)>;

class shim_space
{
public:
	void init(int addr_bits)
	{
		m_bits = addr_bits;
		if (addr_bits <= 8) {
			m_r.assign(1u << addr_bits, nullptr);
			m_w.assign(1u << addr_bits, nullptr);
		}
	}
	u8 read_byte(offs_t a)
	{
		if (const u8 *p = m_fast[(a >> 8) & 0xff])
			return p[a & 0xff];
		if (!m_r.empty() && m_r[a & 0xff])
			return m_r[a & 0xff](a);
		return m_ext_r ? m_ext_r(a) : 0xff;
	}
	// Reads of the 256-byte page `page` come straight from p (nullptr: through
	// the handlers). For large spaces only; the system keeps it in step with
	// whatever it maps there (banking).
	void set_fast_page(u8 page, const u8 *p) { m_fast[page] = p; }
	void write_byte(offs_t a, u8 d)
	{
		if (!m_w.empty() && m_w[a & 0xff]) {
			m_w[a & 0xff](a, d);
			return;
		}
		if (m_ext_w)
			m_ext_w(a, d);
	}
	template <typename C> void cache(C &c) { c.m_space = this; }
	template <typename C> void specific(C &c) { c.m_space = this; }

	// handlers for one address (small spaces); a is absolute
	void set_r(offs_t a, read8_fn f) { m_r[a] = std::move(f); }
	void set_w(offs_t a, write8_fn f) { m_w[a] = std::move(f); }
	// everything not in the table (or the whole of a large space)
	void set_external(read8_fn r, write8_fn w) { m_ext_r = std::move(r); m_ext_w = std::move(w); }
	std::vector<u8> &ram() { return m_ram; }

private:
	int m_bits = 0;
	const u8 *m_fast[256] = {};
	std::vector<read8_fn> m_r;
	std::vector<write8_fn> m_w;
	read8_fn m_ext_r;
	write8_fn m_ext_w;
	std::vector<u8> m_ram;
};

template <int AddrWidth, int DataWidth, int Shift, endianness_t E>
struct memory_access
{
	struct specific {
		shim_space *m_space = nullptr;
		u8 read_byte(offs_t a) { return m_space->read_byte(a); }
		void write_byte(offs_t a, u8 d) { m_space->write_byte(a, d); }
	};
	using cache = specific;
};

class device_t;

template <typename T> class required_shared_ptr
{
public:
	required_shared_ptr(device_t &, const char *) {}
	T *target() const { return m_ptr; }
	operator T *() const { return m_ptr; }
	T &operator[](size_t i) const { return m_ptr[i]; }
	void set_target(T *p) { m_ptr = p; }
private:
	T *m_ptr = nullptr;
};

namespace shim {
template <typename T> struct memfn;
template <typename R, typename C, typename... A> struct memfn<R (C::*)(A...)> { using cls = C; static constexpr size_t arity = sizeof...(A); };
template <typename R, typename C, typename... A> struct memfn<R (C::*)(A...) const> { using cls = C; static constexpr size_t arity = sizeof...(A); };
}

class address_map;

class address_map_entry
{
public:
	address_map_entry(address_map &m, offs_t lo, offs_t hi) : m_map(m), m_lo(lo), m_hi(hi) {}

	template <typename R> address_map_entry &r(R f, const char * = nullptr) { install_r(make_r(f)); return *this; }
	template <typename W> address_map_entry &w(W f, const char * = nullptr) { install_w(make_w(f)); return *this; }
	template <typename R, typename W> address_map_entry &rw(R rf, const char *, W wf, const char * = nullptr) { r(rf); w(wf); return *this; }
	template <typename R> address_map_entry &lr8(R f, const char *) { return r(f); }
	template <typename W> address_map_entry &lw8(W f, const char *) { return w(f); }
	template <typename R, typename W> address_map_entry &lrw8(R rf, const char *, W wf, const char *) { r(rf); w(wf); return *this; }
	address_map_entry &ram();
	address_map_entry &rom() { return *this; }
	address_map_entry &region(const char *, offs_t) { return *this; }
	address_map_entry &share(required_shared_ptr<u8> &p);
	address_map_entry &mirror(offs_t) { return *this; }
	address_map_entry &nopw() { install_w([](offs_t, u8) {}); return *this; }
	address_map_entry &nopr() { install_r([](offs_t) -> u8 { return 0; }); return *this; }
	address_map_entry &noprw() { nopr(); return nopw(); }

private:
	template <typename F> read8_fn make_r(F f);
	template <typename F> write8_fn make_w(F f);
	void install_r(read8_fn f);
	void install_w(write8_fn f);

	address_map &m_map;
	offs_t m_lo, m_hi;
};

class address_map
{
public:
	address_map(shim_space &s, device_t *dev) : m_space(s), m_device(dev) {}
	address_map_entry &operator()(offs_t lo, offs_t hi)
	{
		m_entries.push_back(std::make_unique<address_map_entry>(*this, lo, hi));
		return *m_entries.back();
	}
	shim_space &space() { return m_space; }
	device_t *device() { return m_device; }
private:
	shim_space &m_space;
	device_t *m_device;
	std::vector<std::unique_ptr<address_map_entry>> m_entries;
};

template <typename F> read8_fn address_map_entry::make_r(F f)
{
	offs_t lo = m_lo;
	if constexpr (std::is_member_function_pointer_v<F>) {
		using C = typename shim::memfn<F>::cls;
		C *obj = static_cast<C *>(m_map.device());
		if constexpr (shim::memfn<F>::arity == 0)
			return [obj, f](offs_t) -> u8 { return (obj->*f)(); };
		else
			return [obj, f, lo](offs_t a) -> u8 { return (obj->*f)(a - lo); };
	} else if constexpr (std::is_invocable_v<F>) {
		return [f](offs_t) -> u8 { return f(); };
	} else {
		return [f, lo](offs_t a) -> u8 { return f(a - lo); };
	}
}

template <typename F> write8_fn address_map_entry::make_w(F f)
{
	offs_t lo = m_lo;
	if constexpr (std::is_member_function_pointer_v<F>) {
		using C = typename shim::memfn<F>::cls;
		C *obj = static_cast<C *>(m_map.device());
		if constexpr (shim::memfn<F>::arity == 1)
			return [obj, f](offs_t, u8 d) { (obj->*f)(d); };
		else
			return [obj, f, lo](offs_t a, u8 d) { (obj->*f)(a - lo, d); };
	} else if constexpr (std::is_invocable_v<F, u8>) {
		return [f](offs_t, u8 d) { f(d); };
	} else {
		return [f, lo](offs_t a, u8 d) { f(a - lo, d); };
	}
}

inline void address_map_entry::install_r(read8_fn f)
{
	for (offs_t a = m_lo; a <= m_hi; a++)
		m_map.space().set_r(a, f);
}

inline void address_map_entry::install_w(write8_fn f)
{
	for (offs_t a = m_lo; a <= m_hi; a++)
		m_map.space().set_w(a, f);
}

inline address_map_entry &address_map_entry::ram()
{
	auto &ram = m_map.space().ram();
	ram.assign(m_hi + 1, 0);
	u8 *p = ram.data();
	install_r([p](offs_t a) -> u8 { return p[a]; });
	install_w([p](offs_t a, u8 d) { p[a] = d; });
	return *this;
}

inline address_map_entry &address_map_entry::share(required_shared_ptr<u8> &p)
{
	p.set_target(m_map.space().ram().data());
	return *this;
}

class address_map_constructor
{
public:
	address_map_constructor() = default;
	template <typename C, typename D>
	address_map_constructor(void (C::*fn)(address_map &), const char *, D *obj) : m_f([obj, fn](address_map &m) { (static_cast<C *>(obj)->*fn)(m); }) {}
	template <typename L>
	address_map_constructor(L l, const char *) : m_f(std::move(l)) {}
	void operator()(address_map &m) const { if (m_f) m_f(m); }
private:
	std::function<void(address_map &)> m_f;
};

class address_space_config
{
public:
	address_space_config(const char *name, endianness_t, int data_width, int addr_width, int shift, address_map_constructor ctor = {})
		: m_name(name), m_addr_width(addr_width), m_ctor(std::move(ctor)) { (void)data_width; (void)shift; }
	const char *m_name;
	int m_addr_width;
	address_map_constructor m_ctor;
};

// --- callbacks -----------------------------------------------------------------
class devcb_read8
{
public:
	template <unsigned N> class array : public std::array<devcb_read8, N>
	{
	public:
		array(device_t &, u8 dflt = 0) { for (auto &c : *this) c.m_default = dflt; }
	};
	// a binder that refers back to the callback, as MAME's does (callers keep it by value)
	struct binder { devcb_read8 *c; template <typename F> void set(F f) { c->m_f = std::move(f); } };
	binder bind() { return binder{this}; }
	u8 operator()() { return m_f ? m_f() : m_default; }
private:
	std::function<u8()> m_f;
	u8 m_default = 0;
};

class devcb_write8
{
public:
	template <unsigned N> class array : public std::array<devcb_write8, N>
	{
	public:
		array(device_t &) {}
	};
	// a binder that refers back to the callback, as MAME's does (callers keep it by value)
	struct binder { devcb_write8 *c; template <typename F> void set(F f) { c->m_f = std::move(f); } };
	binder bind() { return binder{this}; }
	void operator()(u8 d) { if (m_f) m_f(d); }
private:
	std::function<void(u8)> m_f;
};

// --- devices -----------------------------------------------------------------
class device_state_entry
{
public:
	int index() const { return m_index; }
	device_state_entry &formatstr(const char *) { return *this; }
	device_state_entry &mask(u64) { return *this; }
	device_state_entry &noshow() { return *this; }
	device_state_entry &callimport() { return *this; }
	device_state_entry &callexport() { return *this; }
	int m_index = 0;
};

class device_t
{
public:
	device_t(const machine_config &, device_type type, const char *tag, device_t *, u32 clock)
		: m_type(type), m_tag(tag ? tag : ""), m_clock(clock) {}
	virtual ~device_t() = default;

	running_machine &machine() const { return m_machine; }
	u32 clock() const { return m_clock; }
	const char *tag() const { return m_tag.c_str(); }

	template <typename... A> void logerror(const char *fmt, const A &...a) const
	{
		fprintf(stderr, "[%s] %s", m_tag.c_str(), shim::format(fmt, a...).c_str());
	}
	template <typename... A> void save_item(A &&...) {}

protected:
	virtual void device_start() {}
	virtual void device_reset() {}

	device_type m_type;
	std::string m_tag;
	u32 m_clock;
	mutable running_machine m_machine;
};

class device_memory_interface
{
public:
	using space_config_vector = std::vector<std::pair<int, const address_space_config *>>;
};

class cpu_device : public device_t, public device_memory_interface
{
public:
	using device_t::device_t;

	// rtlsim's side ----------------------------------------------------------
	// Build the address spaces from the device's configs, then start and reset.
	void start()
	{
		set_icountptr_default();
		for (auto &[n, cfg] : memory_space_config()) {
			m_spaces[n].init(cfg->m_addr_width);
			address_map map(m_spaces[n], this);
			cfg->m_ctor(map);
		}
		device_start();
		device_reset();
	}
	void reset() { device_reset(); }
	// Run for about `cycles` machine cycles; returns the cycles actually used.
	int run(int cycles)
	{
		*m_icountptr = cycles;
		execute_run();
		return cycles - *m_icountptr;
	}
	void set_input_line(int line, int state) { execute_set_input(line, state); }
	shim_space &space(int n = AS_PROGRAM) { return m_spaces[n]; }
	std::function<void(offs_t)> m_insn_hook;	// called before each instruction
	std::unique_ptr<util::disasm_interface> disassembler() { return create_disassembler(); }

protected:
	virtual u64 execute_clocks_to_cycles(u64 clocks) const noexcept { return clocks; }
	virtual u64 execute_cycles_to_clocks(u64 cycles) const noexcept { return cycles; }
	virtual u32 execute_min_cycles() const noexcept { return 1; }
	virtual u32 execute_max_cycles() const noexcept { return 1; }
	virtual void execute_run() = 0;
	virtual void execute_set_input(int, int) {}
	virtual space_config_vector memory_space_config() const = 0;
	virtual void state_string_export(const device_state_entry &, std::string &) const {}
	virtual std::unique_ptr<util::disasm_interface> create_disassembler() { return nullptr; }

	void set_icountptr(int &icount) { m_icountptr = &icount; }
	template <typename... A> device_state_entry &state_add(A &&...) { return m_dummy_state; }
	void standard_irq_callback(int, offs_t) {}
	void debugger_instruction_hook(offs_t pc) { if (m_insn_hook) m_insn_hook(pc); }
	void debugger_wait_hook() {}

private:
	void set_icountptr_default() { if (!m_icountptr) m_icountptr = &m_own_icount; }
	shim_space m_spaces[AS_MAX];
	int *m_icountptr = nullptr;
	int m_own_icount = 0;
	device_state_entry m_dummy_state;
};

#endif
