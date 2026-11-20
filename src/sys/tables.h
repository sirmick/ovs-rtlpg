// tables.h - the switch core's table engine: VLAN and L2 tables behind
// TBL_CTRL, the L2 flush and the MIB counter fetch.
//
// Semantics and entry layouts are RTLPlayground's test/hw_mock.c, ported, so
// the simulator and the firmware's host tests agree on them:
//   TBL_CTRL (0x5cac) = entry(31:16) type(15:8) cmd(7:0); cmd bit 0 executes
//   (cleared when done, at once), bit 1 writes. Type 3: the VLAN table, one
//   32-bit word per VID (DATA_IN_A in, L2_DATA_OUT_A out). Type 4: the L2
//   table, three words per entry (DATA_IN_A/B/C in, L2_DATA_OUT_A/B/C out),
//   looked up by MAC+VID or walked by index (method in TBL_DATA_0 bits 15:14,
//   clear in bit 18; found/index reported in TBL_DATA_0 bits 12:0).
//   The L2 table is 1024 buckets of 4 ways, an entry's index bucket << 2 |
//   way (tables.cpp, l2_hash()): every entry has its own; a full bucket
//   learns nothing more.
//   Word B: bit 29 valid, bits 31:30 the low two port bits, 27:16 VID, 15:0
//   MAC octets 0-1. Word A: MAC octets 2-5. Word C: bit 16 static, bits 4:2 the
//   age, bits 1:0 the high port bits (multicast: bits 7:0 the member mask >> 2).
//   A dynamic entry with age 0 isn't valid: lookups and the fabric skip it, so
//   its frames flood (the firmware's L2 delete writes one).
#ifndef RTLSIM_TABLES_H
#define RTLSIM_TABLES_H

#include "sys/switch_core.h"

#include <array>
#include <vector>

class switch_tables
{
public:
	struct l2_entry {
		bool present = false;
		u16 idx = 0;
		u32 a = 0, b = 0, c = 0;
	};
	static constexpr int L2_MAX = 4096;

	explicit switch_tables(switch_core &core);

	u32 vlan(u16 vid) const { return m_vlan[vid & 0xfff]; }
	const std::vector<l2_entry> &l2() const { return m_l2; }

	// An entry's fields (unicast: port 0-9; multicast: the member mask).
	static void key(const l2_entry &e, u8 mac[6], u16 *vid);
	static unsigned port(const l2_entry &e) { return ((e.b >> 30) & 0x3) | ((e.c & 0x3) << 2); }
	static bool is_static(const l2_entry &e) { return (e.c >> 16) & 1; }
	static bool aged_out(const l2_entry &e) { return !is_static(e) && !(e.c >> 2 & 7); }

	// An L3 multicast entry (IPMC_USES_L3MC): same TBL type 4, but valid on
	// word-B bit 28 (not 29), keyed on (SIP, DIP) with a member mask. See
	// doc/igmp-multicast.md for the layout. Its bucket is a placeholder hash
	// (l3mc_hash in tables.cpp), not the real RTL8372 one, so an entry's
	// index differs from silicon; lookups resolve by key and the
	// firmware never depends on a specific index, so this is immaterial.
	static bool is_l3mc(const l2_entry &e) { return ((e.b >> 28) & 1) && !((e.b >> 29) & 1); }
	static unsigned l3mc_mask(const l2_entry &e) { return ((e.b >> 30) & 0x3) | ((e.c & 0xff) << 2); }
	static void l3mc_sip(const l2_entry &e, u8 ip[4])
	{
		ip[0] = e.a >> 24; ip[1] = e.a >> 16; ip[2] = e.a >> 8; ip[3] = e.a;
	}
	static void l3mc_dip(const l2_entry &e, u8 ip[4])
	{
		ip[0] = 0xe0 | ((e.b >> 24) & 0xf); ip[1] = e.b >> 16; ip[2] = e.b >> 8; ip[3] = e.b;
	}
	// The port a unicast MAC is at in a VLAN, or -1.
	int lookup(const u8 mac[6], u16 vid) const;
	// Hardware learning: a dynamic entry for the source of a frame from
	// `port`, unless a static one has the MAC. False if nothing changed.
	bool learn(const u8 mac[6], u16 vid, unsigned port);
	// Ageing: drops the dynamic entry if it still says `port`.
	bool age_out(const u8 mac[6], u16 vid, unsigned port);
	// Bumped by every change to the VLAN or L2 table.
	u64 version = 0;
	// A table command that takes time (control mode's reg busy 5cac 1 <n>):
	// after a write to TBL_CTRL with execute set, the next busy_reads reads
	// of TBL_CTRL return execute still set, and the command is done at the
	// read after them, with the inputs (TBL_DATA_0, DATA_IN_A-C) it was
	// started with: until then the outputs keep their old contents. A
	// command written while one is busy is dropped (reported, table-busy).
	// 0: done at once, as it was.
	unsigned busy_reads = 0;
	// A busy command done now (a chip reset lasts longer than any command).
	void settle() { if (m_busy.on) table_done(); }
	void set_counter(u8 port, u8 counter, u64 v) { m_counters[port & 15][counter & 63] = v; }

private:
	void table_exec(u32 ctrl);
	void table_done();
	struct {
		bool on = false;
		unsigned left = 0;
		u32 ctrl = 0, in[4] = {};
	} m_busy;
	void flush_exec(u32 ctrl);
	void stat_exec(u32 v);
	void l2_out(const l2_entry *e, u16 idx);
	l2_entry *l2_find(const u8 mac[6], u16 vid);
	const l2_entry *l2_find(const u8 mac[6], u16 vid) const { return const_cast<switch_tables *>(this)->l2_find(mac, vid); }
	l2_entry *l3mc_find(u32 a, u32 b);
	l2_entry *l2_free();
	l2_entry *l2_place(u16 bucket);
	void l2_sort();

	switch_core &m_core;
	std::array<u32, 4096> m_vlan{};
	std::vector<l2_entry> m_l2;
	u64 m_counters[16][64] = {};
};

#endif
