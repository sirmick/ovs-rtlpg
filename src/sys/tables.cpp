// tables.cpp - see tables.h. Ported from RTLPlayground's test/hw_mock.c.
#include "sys/tables.h"
#include "sys/report.h"

#include <algorithm>
#include <cstring>

enum : u16 {
	TBL_CTRL = 0x5cac, TBL_DATA_0 = 0x5cb0,
	DATA_IN_A = 0x5cb8, DATA_IN_B = 0x5cbc, DATA_IN_C = 0x5cc0,
	DATA_OUT_A = 0x5ccc, DATA_OUT_B = 0x5cd0, DATA_OUT_C = 0x5cd4,
	L2_FLUSH_CTRL = 0x53d4, L2_FLUSH_CNF = 0x53dc,
	STAT_GET = 0x0f60, STAT_V_HIGH = 0x0f64, STAT_V_LOW = 0x0f68,
};
enum : u32 { TBL_EXECUTE = 0x01, TBL_WRITE = 0x02, TBL_VLAN = 0x03, TBL_L2_UNICAST = 0x04, LUTREAD_MAC = 0, FLUSH_EXEC = 0x10000 };

void switch_tables::key(const l2_entry &e, u8 mac[6], u16 *vid)
{
	mac[0] = e.b >> 8; mac[1] = e.b;
	mac[2] = e.a >> 24; mac[3] = e.a >> 16; mac[4] = e.a >> 8; mac[5] = e.a;
	*vid = (e.b >> 16) & 0xfff;
}

// The table's geometry: 4096 entries, 1024 buckets of 4 (the RTL8376
// datasheet: a 4-way hash; the RTL8373 SDK: 4160 addresses, 4096 hashed and
// a 64-entry CAM, not modelled), an entry's index its bucket << 2 | its way,
// as the vendor's other switches have it (OpenWrt's rtl83xx l2.c). So every
// entry has an index of its own, as the firmware's next-index walk assumes.
// The bucket hash is a placeholder, not the chip's.
static u16 l2_hash(const u8 mac[6], u16 vid)
{
	u16 h = vid;
	for (int i = 0; i < 6; i++)
		h = u16(h * 31 + mac[i]);
	return h & 0x3ff;
}

switch_tables::switch_tables(switch_core &core) : m_core(core), m_l2(L2_MAX)
{
	m_core.on_write(TBL_CTRL, [this](u32 v) {
		if (m_busy.on) {
			if (v & TBL_EXECUTE)
				report("table-busy", "table: TBL_CTRL %08x written while %08x is busy: dropped", v, m_busy.ctrl);
			return m_busy.ctrl;			// it stays as it is, executing
		}
		if (v & TBL_EXECUTE) {
			if (busy_reads) {
				m_busy.on = true;
				m_busy.left = busy_reads;
				m_busy.ctrl = v;
				for (int i = 0; i < 4; i++)
					m_busy.in[i] = m_core.reg(i ? u16(DATA_IN_A + 4 * (i - 1)) : TBL_DATA_0);
				return v;
			}
			table_exec(v);
			return v & ~TBL_EXECUTE;
		}
		return v;
	});
	m_core.on_read(TBL_CTRL, [this](u32 v) {
		if (!m_busy.on)
			return v;
		if (m_busy.left) {
			m_busy.left--;
			return v;
		}
		table_done();
		return m_core.reg(TBL_CTRL);
	});
	m_core.on_write(L2_FLUSH_CTRL, [this](u32 v) {
		if (v & FLUSH_EXEC) {
			flush_exec(v);
			return v & ~FLUSH_EXEC;
		}
		return v;
	});
	m_core.on_write(STAT_GET, [this](u32 v) {
		if (v & 1) {
			stat_exec(v);
			return v & ~1u;
		}
		return v;
	});
}

switch_tables::l2_entry *switch_tables::l2_find(const u8 mac[6], u16 vid)
{
	for (auto &e : m_l2) {
		u8 m[6];
		u16 v;
		if (!e.present || is_l3mc(e))	// L3MC entries are not keyed by MAC
			continue;
		key(e, m, &v);
		if (v == vid && !memcmp(m, mac, 6))
			return &e;
	}
	return nullptr;
}

// An L3MC key is word A (SIP) and word B bits 27:0 (DIP); the port bits 31:30
// and the valid bits are not part of it.
switch_tables::l2_entry *switch_tables::l3mc_find(u32 a, u32 b)
{
	for (auto &e : m_l2)
		if (e.present && is_l3mc(e) && e.a == a && !((e.b ^ b) & 0x0fffffff))
			return &e;
	return nullptr;
}

static u16 l3mc_hash(u32 a, u32 b)		// a bucket, as l2_hash()
{
	u32 h = a * 31 + (b & 0x0fffffff);
	return u16((h ^ h >> 12 ^ h >> 24) & 0x3ff);
}

int switch_tables::lookup(const u8 mac[6], u16 vid) const
{
	const l2_entry *e = l2_find(mac, vid);
	return e && !aged_out(*e) && !(mac[0] & 1) ? int(port(*e)) : -1;
}

// Word layouts: tables.h; C bits 4:2 the age, which lookups need nonzero.
bool switch_tables::learn(const u8 mac[6], u16 vid, unsigned p)
{
	if (mac[0] & 1)
		return false;
	l2_entry *e = l2_find(mac, vid);
	if (e && (is_static(*e) || port(*e) == p))
		return false;
	if (!e) {
		e = l2_place(l2_hash(mac, vid));
		if (!e)
			return false;		// its bucket full: the frame is flooded
	}
	e->present = true;
	e->a = (u32(mac[2]) << 24) | (mac[3] << 16) | (mac[4] << 8) | mac[5];
	e->b = ((p & 3u) << 30) | (1u << 29) | (u32(vid & 0xfff) << 16) | (mac[0] << 8) | mac[1];
	e->c = (7u << 2) | (p >> 2);
	version++;
	return true;
}

bool switch_tables::age_out(const u8 mac[6], u16 vid, unsigned p)
{
	l2_entry *e = l2_find(mac, vid);
	if (!e || is_static(*e) || port(*e) != p)
		return false;
	e->present = false;
	version++;
	return true;
}

switch_tables::l2_entry *switch_tables::l2_free()
{
	for (auto &e : m_l2)
		if (!e.present)
			return &e;
	return nullptr;
}

// A free entry in `bucket`, its index set to the first free way; none if
// the bucket's four are taken (the frame is then flooded, not learned).
switch_tables::l2_entry *switch_tables::l2_place(u16 bucket)
{
	for (u16 way = 0; way < 4; way++) {
		u16 const idx = u16(bucket << 2 | way);
		bool taken = false;
		for (auto &x : m_l2)
			if (x.present && x.idx == idx) {
				taken = true;
				break;
			}
		if (!taken) {
			l2_entry *e = l2_free();
			if (e)
				e->idx = idx;
			return e;
		}
	}
	return nullptr;
}

void switch_tables::l2_sort()
{
	std::stable_sort(m_l2.begin(), m_l2.end(), [](const l2_entry &x, const l2_entry &y) {
		if (x.present != y.present)
			return x.present;
		return x.idx < y.idx;
	});
}

void switch_tables::l2_out(const l2_entry *e, u16 idx)
{
	m_core.set_reg(DATA_OUT_A, e ? e->a : 0);
	m_core.set_reg(DATA_OUT_B, e ? e->b : 0);
	m_core.set_reg(DATA_OUT_C, e ? e->c : 0);
	// Bit 12 hit, bit 13 miss: silicon sets 13 on a lookup miss and clears
	// it on a hit or a write (measured on the 8x, RTL8373).
	m_core.set_reg(TBL_DATA_0, (m_core.reg(TBL_DATA_0) & ~0x3fffu) | (e ? 0x1000 : 0x2000) | (idx & 0xfff));
}

void switch_tables::table_exec(u32 ctrl)
{
	u16 const idx = (ctrl >> 16) & 0xfff;
	u8 const type = ctrl >> 8;
	bool const write = ctrl & TBL_WRITE;

	if (type == TBL_VLAN) {
		if (write) {
			m_vlan[idx] = m_core.reg(DATA_IN_A);
			version++;
		}
		else
			m_core.set_reg(DATA_OUT_A, m_vlan[idx]);
		return;
	}
	if (type != TBL_L2_UNICAST)
		return;

	u32 const d0 = m_core.reg(TBL_DATA_0);
	u8 const method = (d0 >> 14) & 0x3;
	if (write)
		version++;
	bool const clear = (d0 >> 18) & 1;

	if (method == LUTREAD_MAC && is_l3mc({true, 0, 0, m_core.reg(DATA_IN_B), 0})) {
		// An L3MC key (SIP, DIP), as igmp_packet_handler looks one up and
		// writes it. On silicon a miss leaves the key in DATA_OUT A/B.
		u32 const a = m_core.reg(DATA_IN_A), b = m_core.reg(DATA_IN_B);
		l2_entry *e = l3mc_find(a, b);
		if (write) {
			if (clear) {
				// Silicon deletes the entry at the TBL_CTRL index field, not
				// by key: a hash write + clear with index 0 leaves the entry
				// (measured on switch-bench). The firmware's IGMP-leave passes
				// the looked-up index here.
				for (auto &x : m_l2)
					if (x.present && is_l3mc(x) && x.idx == idx) {
						x.present = false;
						break;
					}
				l2_out(nullptr, 0);
				return;
			}
			if (!e) {
				e = l2_place(l3mc_hash(a, b));
				if (!e)
					return;		// its bucket full
			}
			e->present = true;
			e->a = a; e->b = b; e->c = m_core.reg(DATA_IN_C);
			l2_out(e, e->idx);
			return;
		}
		if (e)
			l2_out(e, e->idx);
		else {
			l2_out(nullptr, 0);
			m_core.set_reg(DATA_OUT_A, a);
			m_core.set_reg(DATA_OUT_B, b);
		}
		return;
	}
	if (method == LUTREAD_MAC) {
		l2_entry in{true, 0, m_core.reg(DATA_IN_A), m_core.reg(DATA_IN_B), m_core.reg(DATA_IN_C)};
		u8 mac[6];
		u16 vid;
		key(in, mac, &vid);
		l2_entry *e = l2_find(mac, vid);
		if (write) {
			if (clear) {
				// On silicon a clear leaves TBL_DATA_0 and DATA_OUT as
				// they were (measured on the 8x).
				if (e)
					e->present = false;
				return;
			}
			if (!e) {
				e = l2_place(l2_hash(mac, vid));
				if (!e)
					return;		// its bucket full
			}
			e->present = true;
			e->a = in.a; e->b = in.b; e->c = in.c;
			l2_out(e, e->idx);
			// A unicast entry written with age 0 and no static bit is a
			// free slot (l2.delete, the old l2_delete: their delete).
			// Assumed, not yet measured on silicon (P7 review3, Q3).
			if (aged_out(*e) && !(mac[0] & 1))
				e->present = false;
			return;
		}
		l2_out(e, e ? e->idx : 0);
		if (!e) {
			// a miss leaves the key in DATA_OUT A/B, C zero (as for L3MC)
			m_core.set_reg(DATA_OUT_A, in.a);
			m_core.set_reg(DATA_OUT_B, in.b);
		}
		return;
	}

	// next-entry reads: the first present entry at or after idx, wrapping.
	// Method 3 (the firmware's `l2` walk) skips L3MC entries on silicon;
	// methods 1 and 2 return them.
	l2_sort();
	int n = 0;
	while (n < L2_MAX && m_l2[n].present)
		n++;
	auto const skip = [&](const l2_entry &x) { return method == 3 && is_l3mc(x); };
	l2_entry *e = nullptr;
	for (int i = 0; i < n && !e; i++)
		if (m_l2[i].idx >= idx && !skip(m_l2[i]))
			e = &m_l2[i];
	for (int i = 0; i < n && !e; i++)
		if (!skip(m_l2[i]))
			e = &m_l2[i];
	if (write) {
		// a write by index replaces the entry that sits there
		e = nullptr;
		for (int i = 0; i < n; i++)
			if (m_l2[i].idx == idx) { e = &m_l2[i]; break; }
		if (!e)
			e = l2_free();
		if (!e)
			return;
		// Valid on bit 29 (L2 unicast) or bit 28 (L3 multicast).
		e->present = ((m_core.reg(DATA_IN_B) >> 28) & 3) != 0;
		e->idx = idx;
		e->a = m_core.reg(DATA_IN_A);
		e->b = m_core.reg(DATA_IN_B);
		e->c = m_core.reg(DATA_IN_C);
		return;
	}
	l2_out(e, e ? e->idx : idx);
}

// The busy command, done now with the inputs it was started with; what the
// firmware has written to them since stays, but the outputs' bits of
// TBL_DATA_0 (13:0) are the command's.
void switch_tables::table_done()
{
	u16 const ins[4] = { TBL_DATA_0, DATA_IN_A, DATA_IN_B, DATA_IN_C };
	u32 now[4];
	for (int i = 0; i < 4; i++) {
		now[i] = m_core.reg(ins[i]);
		m_core.set_reg(ins[i], m_busy.in[i]);
	}
	m_busy.on = false;
	table_exec(m_busy.ctrl);
	u32 const d0 = m_core.reg(TBL_DATA_0);
	for (int i = 1; i < 4; i++)
		m_core.set_reg(ins[i], now[i]);
	m_core.set_reg(TBL_DATA_0, (now[0] & ~0x3fffu) | (d0 & 0x3fff));
	m_core.set_reg(TBL_CTRL, m_busy.ctrl & ~TBL_EXECUTE);
}

void switch_tables::flush_exec(u32 ctrl)
{
	u16 const mask = ctrl & 0x3ff;
	bool const dynamic_only = m_core.reg(L2_FLUSH_CNF) == 0;
	for (auto &e : m_l2) {
		if (!e.present || is_l3mc(e))	// L3MC entries age/flush on their own path
			continue;
		u8 mac[6];
		u16 vid;
		key(e, mac, &vid);
		bool const mc = mac[0] & 1;
		bool const st = (e.c >> 16) & 1;
		u16 const port = mc ? 0 : (((e.b >> 30) & 0x3) | ((e.c & 0x3) << 2));
		if (mc || (st && dynamic_only))
			continue;
		if ((mask >> port) & 1) {
			e.present = false;
			version++;
		}
	}
}

void switch_tables::stat_exec(u32 v)
{
	u8 const cnt = u8((((v >> 8) & 0xff) << 3) | ((v >> 5) & 0x7));
	u8 const port = (v >> 1) & 0xf;
	u64 const c = m_counters[port][cnt & 0x3f];
	m_core.set_reg(STAT_V_HIGH, u32(c >> 32));
	m_core.set_reg(STAT_V_LOW, u32(c));
}
