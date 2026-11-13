// tables.cpp - see tables.h. Ported from RTLPlayground's test/hw_mock.c.
#include "sys/tables.h"

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

static void l2_key(const switch_tables::l2_entry &e, u8 mac[6], u16 *vid)
{
	mac[0] = e.b >> 8; mac[1] = e.b;
	mac[2] = e.a >> 24; mac[3] = e.a >> 16; mac[4] = e.a >> 8; mac[5] = e.a;
	*vid = (e.b >> 16) & 0xfff;
}

static u16 l2_hash(const u8 mac[6], u16 vid)
{
	u16 h = vid;
	for (int i = 0; i < 6; i++)
		h = u16(h * 31 + mac[i]);
	return h & 0xfff;
}

switch_tables::switch_tables(switch_core &core) : m_core(core), m_l2(L2_MAX)
{
	m_core.on_write(TBL_CTRL, [this](u32 v) {
		if (v & TBL_EXECUTE) {
			table_exec(v);
			return v & ~TBL_EXECUTE;
		}
		return v;
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
		if (!e.present)
			continue;
		l2_key(e, m, &v);
		if (v == vid && !memcmp(m, mac, 6))
			return &e;
	}
	return nullptr;
}

switch_tables::l2_entry *switch_tables::l2_free()
{
	for (auto &e : m_l2)
		if (!e.present)
			return &e;
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
	m_core.set_reg(TBL_DATA_0, (m_core.reg(TBL_DATA_0) & ~0x1fffu) | (e ? 0x1000 : 0) | (idx & 0xfff));
}

void switch_tables::table_exec(u32 ctrl)
{
	u16 const idx = (ctrl >> 16) & 0xfff;
	u8 const type = ctrl >> 8;
	bool const write = ctrl & TBL_WRITE;

	if (type == TBL_VLAN) {
		if (write)
			m_vlan[idx] = m_core.reg(DATA_IN_A);
		else
			m_core.set_reg(DATA_OUT_A, m_vlan[idx]);
		return;
	}
	if (type != TBL_L2_UNICAST)
		return;

	u32 const d0 = m_core.reg(TBL_DATA_0);
	u8 const method = (d0 >> 14) & 0x3;
	bool const clear = (d0 >> 18) & 1;

	if (method == LUTREAD_MAC) {
		l2_entry key{true, 0, m_core.reg(DATA_IN_A), m_core.reg(DATA_IN_B), m_core.reg(DATA_IN_C)};
		u8 mac[6];
		u16 vid;
		l2_key(key, mac, &vid);
		l2_entry *e = l2_find(mac, vid);
		if (write) {
			if (clear) {
				if (e)
					e->present = false;
				l2_out(nullptr, 0);
				return;
			}
			if (!e) {
				e = l2_free();
				if (!e)
					return;		// table full
				e->idx = l2_hash(mac, vid);
			}
			e->present = true;
			e->a = key.a; e->b = key.b; e->c = key.c;
			l2_out(e, e->idx);
			return;
		}
		l2_out(e, e ? e->idx : 0);
		return;
	}

	// next-entry reads: the first present entry at or after idx, wrapping
	l2_sort();
	int n = 0;
	while (n < L2_MAX && m_l2[n].present)
		n++;
	l2_entry *e = nullptr;
	for (int i = 0; i < n; i++)
		if (m_l2[i].idx >= idx) { e = &m_l2[i]; break; }
	if (!e && n)
		e = &m_l2[0];
	if (write) {
		// a write by index replaces the entry that sits there
		e = nullptr;
		for (int i = 0; i < n; i++)
			if (m_l2[i].idx == idx) { e = &m_l2[i]; break; }
		if (!e)
			e = l2_free();
		if (!e)
			return;
		e->present = (m_core.reg(DATA_IN_B) >> 29) & 1;
		e->idx = idx;
		e->a = m_core.reg(DATA_IN_A);
		e->b = m_core.reg(DATA_IN_B);
		e->c = m_core.reg(DATA_IN_C);
		return;
	}
	l2_out(e, e ? e->idx : idx);
}

void switch_tables::flush_exec(u32 ctrl)
{
	u16 const mask = ctrl & 0x3ff;
	bool const dynamic_only = m_core.reg(L2_FLUSH_CNF) == 0;
	for (auto &e : m_l2) {
		if (!e.present)
			continue;
		u8 mac[6];
		u16 vid;
		l2_key(e, mac, &vid);
		bool const mc = mac[0] & 1;
		bool const st = (e.c >> 16) & 1;
		u16 const port = mc ? 0 : (((e.b >> 30) & 0x3) | ((e.c & 0x3) << 2));
		if (mc || (st && dynamic_only))
			continue;
		if ((mask >> port) & 1)
			e.present = false;
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
