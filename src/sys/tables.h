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
//   Word B: bit 29 valid, bits 31:30 the low two port bits, 27:16 VID, 15:0
//   MAC octets 0-1. Word A: MAC octets 2-5. Word C: bit 16 static, bits 1:0 the
//   high port bits (multicast: bits 7:0 the member mask >> 2).
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
	// The port a unicast MAC is at in a VLAN, or -1.
	int lookup(const u8 mac[6], u16 vid) const;
	// Hardware learning: a dynamic entry for the source of a frame from
	// `port`, unless a static one has the MAC. False if nothing changed.
	bool learn(const u8 mac[6], u16 vid, unsigned port);
	// Ageing: drops the dynamic entry if it still says `port`.
	bool age_out(const u8 mac[6], u16 vid, unsigned port);
	// Bumped by every change to the VLAN or L2 table.
	u64 version = 0;
	void set_counter(u8 port, u8 counter, u64 v) { m_counters[port & 15][counter & 63] = v; }

private:
	void table_exec(u32 ctrl);
	void flush_exec(u32 ctrl);
	void stat_exec(u32 v);
	void l2_out(const l2_entry *e, u16 idx);
	l2_entry *l2_find(const u8 mac[6], u16 vid);
	const l2_entry *l2_find(const u8 mac[6], u16 vid) const { return const_cast<switch_tables *>(this)->l2_find(mac, vid); }
	l2_entry *l2_free();
	void l2_sort();

	switch_core &m_core;
	std::array<u32, 4096> m_vlan{};
	std::vector<l2_entry> m_l2;
	u64 m_counters[16][64] = {};
};

#endif
