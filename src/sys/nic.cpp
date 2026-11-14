// nic.cpp - see nic.h.
#include "sys/nic.h"

#include <cstdio>

enum : u16 {
	NIC_BUFFSIZE_TX = 0x7844, NIC_RXBUFF_RX = 0x7848, NIC_RXCMD = 0x784c, NIC_TXCMD = 0x7850,
	NIC_INT_STS = 0x7854, NIC_INT_MSK = 0x7858, RX_CTRL = 0x785c, TX_CTRL = 0x7860,
	NIC_RX_BUFF_DATA = 0x7874, CPU_RX_CURR_PKT = 0x787c, NIC_RX_CURR_PKT = 0x7880, NIC_TX_CURR_PKT = 0x7884, CPU_TX_CURR_PKT = 0x7890,
	PVID_BASE = 0x4e1c,
};
enum : u8 { SFR_DATA_L = 0xb3, SFR_DATA_H, SFR_RING_L, SFR_RING_H, SFR_CTRL };
enum : u32 { NIC_INT_RXIS = 0x02 };
// The VLAN table entry (rtl837x_port.c vlan_create()): members in bits 9:0,
// the untag mask in bits 19:10, valid bit 25.
enum : u32 { VLAN_VALID = 1u << 25 };
enum : u16 { TPID_8021Q = 0x8100, TAG_CPU = 0x8899, CPU_TAG_KEEP = 0x0080, PMASK_ALLOW = 0x8000 };

static constexpr size_t NIC_MEM = 0x8000;
static constexpr size_t ETH_MIN = 60;

static u16 be16(const u8 *p) { return (p[0] << 8) | p[1]; }
static void put_be16(u8 *p, u16 v) { p[0] = v >> 8; p[1] = v; }

cpu_nic::cpu_nic(switch_core &core, switch_tables &tables)
	: m_core(core), m_tables(tables), m_rx(NIC_MEM), m_tx(NIC_MEM)
{
	m_core.on_read(NIC_RX_BUFF_DATA, [this](u32) { return u32(m_rx_used); });
	m_core.on_read(CPU_RX_CURR_PKT, [this](u32) { return u32(m_frames.empty() ? m_rx_tail : m_frames.front().slot); });
	m_core.on_read(NIC_RX_CURR_PKT, [this](u32) { return u32(m_rx_tail); });
	m_core.on_write(NIC_RXCMD, [this](u32 v) {
		if (v & 1)
			rx_release();
		return v & ~1u;
	});
	m_core.on_read(CPU_TX_CURR_PKT, [this](u32) { return u32(m_tx_slot); });
	m_core.on_read(NIC_TX_CURR_PKT, [this](u32) { return u32(m_tx_slot); });
	m_core.on_write(NIC_TXCMD, [this](u32 v) {
		if (v & 1)
			tx_send();
		return v & ~1u;
	});
	m_core.on_read(NIC_INT_STS, [this](u32) { return m_sts; });
	m_core.on_write(NIC_INT_STS, [this](u32 v) {
		m_sts &= ~v;
		update_irq();
		return m_sts;
	});
	m_core.on_write(NIC_INT_MSK, [this](u32 v) {
		m_core.set_reg(NIC_INT_MSK, v);
		update_irq();
		return v;
	});
}

void cpu_nic::reset()
{
	m_frames.clear();
	m_rx_used = m_rx_tail = m_tx_slot = 0;
	m_sts = 0;
	update_irq();
}

unsigned cpu_nic::rx_slots() const
{
	unsigned last = m_core.reg(NIC_RXBUFF_RX) & 0xfff;
	return last && (last + 1) * 8 <= NIC_MEM ? last + 1 : 0;
}

unsigned cpu_nic::tx_slots() const
{
	unsigned last = m_core.reg(NIC_BUFFSIZE_TX) & 0xfff;
	return last && (last + 1) * 8 <= NIC_MEM ? last + 1 : 0x800;
}

void cpu_nic::update_irq()
{
	bool level = m_sts & m_core.reg(NIC_INT_MSK) & NIC_INT_RXIS;
	if (level != m_irq && irq)
		irq(level);
	m_irq = level;
}

u8 cpu_nic::sfr_r(u8 s)
{
	return s == SFR_CTRL ? 0 : m_sfr[s - SFR_DATA_L];
}

void cpu_nic::sfr_w(u8 s, u8 d)
{
	m_sfr[s - SFR_DATA_L] = d;
	if (s == SFR_CTRL && d)
		dma(d);
}

void cpu_nic::dma(u8 words)
{
	u16 x = m_sfr[0] | (m_sfr[1] << 8);
	u16 ring = m_sfr[2] | (m_sfr[3] << 8);
	unsigned n = words * 8u;
	if (ring & 0x8000) {
		unsigned size = tx_slots() * 8, off = ring & 0x7fff;
		for (unsigned i = 0; i < n; i++)
			m_tx[(off + i) % size] = xdata_r(x + i);
	} else {
		unsigned size = rx_slots() * 8;
		for (unsigned i = 0; i < n; i++)
			xdata_w(x + i, ring < size ? m_rx[(ring + i) % size] : m_rx[(ring + i) % NIC_MEM]);
	}
}

u16 cpu_nic::pvid(unsigned port) const
{
	u32 v = m_core.reg(PVID_BASE + (port >> 1) * 4);
	return (port & 1 ? v >> 12 : v) & 0xfff;
}

void cpu_nic::rx_write(unsigned off, const u8 *p, size_t n)
{
	unsigned size = rx_slots() * 8;
	for (size_t i = 0; i < n; i++)
		m_rx[(off + i) % size] = p[i];
}

bool cpu_nic::receive(unsigned port, const u8 *frame, size_t len)
{
	unsigned const slots = rx_slots();
	if (len < 14 || !slots || !(m_core.reg(RX_CTRL) & 1)) {
		stats.rx_dropped++;
		if (trace)
			fprintf(stderr, "NIC RX p%u %zuB dropped: NIC RX off (RX_CTRL %08x, %u slots)\n", port, len, m_core.reg(RX_CTRL), slots);
		return false;
	}
	// On the wire the frame was padded to the minimum; the ASIC inserts its
	// CPU tag behind the MACs and an 802.1Q tag behind that.
	std::vector<u8> f(frame, frame + len);
	if (f.size() < ETH_MIN)
		f.resize(ETH_MIN, 0);
	bool const tagged = be16(&f[12]) == TPID_8021Q;
	u16 const tci = tagged ? be16(&f[14]) : pvid(port);
	u32 const vlan = m_tables.vlan(tci & 0xfff);
	if (!(vlan & VLAN_VALID) || !(vlan & (1u << CPU_PORT))) {
		stats.rx_dropped++;
		if (trace)
			fprintf(stderr, "NIC RX p%u %zuB dropped: VLAN %u (%08x) doesn't have the CPU\n", port, len, tci & 0xfff, vlan);
		return false;
	}
	u8 hdr[12] = { 0x88, 0x99, 0x04, 0x00, 0x00, 0x00, 0x00, u8(port), 0x81, 0x00, u8(tci >> 8), u8(tci) };
	f.erase(f.begin() + 12, f.begin() + (tagged ? 16 : 12));
	f.insert(f.begin() + 12, hdr, hdr + sizeof hdr);

	unsigned const need = 1 + (f.size() + 7) / 8;
	if (m_rx_used + need > slots) {
		stats.rx_dropped++;
		if (trace)
			fprintf(stderr, "NIC RX p%u %zuB dropped: ring full (%u of %u slots)\n", port, f.size(), m_rx_used, slots);
		return false;
	}
	u8 desc[8] = { m_seq++, 0, 0, u8(port & 0xf), u8(f.size()), u8(f.size() >> 8), 0, 0 };
	rx_write(m_rx_tail * 8, desc, 8);
	rx_write((m_rx_tail + 1) % slots * 8, f.data(), f.size());
	m_frames.push_back({u16(m_rx_tail), u16(need)});
	m_rx_tail = (m_rx_tail + need) % slots;
	m_rx_used += need;
	stats.rx++;
	if (busy)
		m_rx_busy = busy();
	if (trace)
		note("RX", port, f);
	m_sts |= NIC_INT_RXIS;
	update_irq();
	return true;
}

void cpu_nic::rx_release()
{
	if (m_frames.empty())
		return;
	m_rx_used -= m_frames.front().slots;
	m_frames.pop_front();
}

// Checksum offload (descriptor byte 1 = 0x07): uIP leaves the IPv4 header
// and TCP checksums 0 for the ASIC to fill in, and turns an ICMP echo into
// its reply without fixing the ICMP checksum. UDP goes out with checksum 0
// (UIP_CONF_UDP_CHECKSUMS off), which is valid as it is.
static u16 csum(const u8 *p, size_t n, u32 sum = 0)
{
	for (size_t i = 0; i + 1 < n; i += 2)
		sum += be16(p + i);
	if (n & 1)
		sum += p[n - 1] << 8;
	while (sum >> 16)
		sum = (sum & 0xffff) + (sum >> 16);
	return sum;
}

static void offload_checksums(std::vector<u8> &f)
{
	size_t o = 12;
	while (o + 2 <= f.size() && be16(&f[o]) == TPID_8021Q)
		o += 4;
	if (o + 2 > f.size() || be16(&f[o]) != 0x0800)
		return;
	o += 2;
	if (o + 20 > f.size())
		return;
	u8 *ip = &f[o];
	size_t const ihl = (ip[0] & 0xf) * 4, total = be16(ip + 2);
	if (ihl < 20 || o + total > f.size() || total < ihl)
		return;
	put_be16(ip + 10, 0);
	put_be16(ip + 10, ~csum(ip, ihl));
	if (ip[9] == 6 && total >= ihl + 20) {
		u8 *tcp = ip + ihl;
		size_t const l4 = total - ihl;
		u32 pseudo = csum(ip + 12, 8) + 6 + l4;
		put_be16(tcp + 16, 0);
		put_be16(tcp + 16, ~csum(tcp, l4, pseudo));
	} else if (ip[9] == 1 && total >= ihl + 4) {
		u8 *icmp = ip + ihl;
		put_be16(icmp + 2, 0);
		put_be16(icmp + 2, ~csum(icmp, total - ihl));
	}
}

void cpu_nic::tx_send()
{
	unsigned const size = tx_slots() * 8, at = m_tx_slot * 8;
	auto tx = [&](unsigned i) { return m_tx[(at + i) % size]; };
	unsigned const len = tx(4) | (tx(5) << 8);
	bool const offload = tx(1) & 0x07;
	m_tx_slot = (m_tx_slot + 1 + (len + 7) / 8) % tx_slots();
	if (len < 14 || len > 2040 || !(m_core.reg(TX_CTRL) & 1)) {
		stats.tx_dropped++;
		if (trace)
			fprintf(stderr, "NIC TX %uB dropped: NIC TX off or a bad length\n", len);
		return;
	}
	std::vector<u8> f(len);
	for (unsigned i = 0; i < len; i++)
		f[i] = tx(8 + i);
	if (offload)
		offload_checksums(f);
	forward(std::move(f));
}

// Where a frame from the CPU goes; see nic.h for the stand-in lookup.
void cpu_nic::forward(std::vector<u8> f)
{
	u16 flags = 0, pmask = 0;
	bool const cpu_tag = be16(&f[12]) == TAG_CPU && f.size() >= 20;
	if (cpu_tag) {
		flags = be16(&f[16]);
		pmask = be16(&f[18]);
		f.erase(f.begin() + 12, f.begin() + 20);
	}
	bool const tagged = f.size() >= 16 && be16(&f[12]) == TPID_8021Q;
	u16 const vid = (tagged ? be16(&f[14]) : pvid(CPU_PORT)) & 0xfff;
	u32 const vlan = m_tables.vlan(vid);
	u16 dest;
	if (cpu_tag && !(pmask & PMASK_ALLOW))
		dest = pmask & 0x3ff;
	else {
		// the lookup: a unicast MAC the L2 table knows goes to its port,
		// anything else floods the VLAN
		int const at = (f[0] & 1) ? -1 : m_tables.lookup(&f[0], vid);
		if (at >= 0)
			dest = (vlan & VLAN_VALID) ? (1u << at) & ~(1u << CPU_PORT) : 0;
		else
			dest = (vlan & VLAN_VALID) ? vlan & 0x3ff & ~(1u << CPU_PORT) : 0;
		if (cpu_tag)
			dest &= pmask;
	}
	dest &= peers;
	if (!dest) {
		stats.tx_dropped++;
		if (trace)
			fprintf(stderr, "NIC TX %zuB dropped: no port (VLAN %u, pmask %04x)\n", f.size(), vid, pmask);
		return;
	}
	for (unsigned p = 0; p < 10; p++) {
		if (!(dest & (1u << p)))
			continue;
		std::vector<u8> out = f;
		if (!(flags & CPU_TAG_KEEP) && (vlan & VLAN_VALID)) {
			bool const untag = vlan & (1u << (10 + p));
			if (untag && tagged)
				out.erase(out.begin() + 12, out.begin() + 16);
			else if (!untag && !tagged) {
				u8 const t[4] = { 0x81, 0x00, u8(vid >> 8), u8(vid) };
				out.insert(out.begin() + 12, t, t + 4);
			}
		}
		if (out.size() < ETH_MIN)
			out.resize(ETH_MIN, 0);
		stats.tx++;
		if (trace)
			note("TX", p, out);
		if (transmit)
			transmit(p, out);
	}
}

// One line per frame: direction, port, size, what it is; a TX line also has
// the CPU's busy cycles since the last frame arrived.
void cpu_nic::note(const char *dir, unsigned port, const std::vector<u8> &f)
{
	size_t o = 12;
	if (o + 2 <= f.size() && be16(&f[o]) == TAG_CPU)
		o += 8;
	while (o + 2 <= f.size() && be16(&f[o]) == TPID_8021Q)
		o += 4;
	u16 const type = o + 2 <= f.size() ? be16(&f[o]) : 0;
	char what[64];
	snprintf(what, sizeof what, "%04x", type);
	const u8 *ip = &f[o + 2];
	if (type == 0x0806)
		snprintf(what, sizeof what, "ARP");
	else if (type == 0x0800 && o + 22 <= f.size()) {
		size_t const ihl = (ip[0] & 0xf) * 4, total = be16(ip + 2);
		if (ip[9] == 1)
			snprintf(what, sizeof what, "ICMP type %u", ip + ihl < f.data() + f.size() ? ip[ihl] : 0);
		else if (ip[9] == 6 && o + 2 + ihl + 20 <= f.size()) {
			const u8 *t = ip + ihl;
			snprintf(what, sizeof what, "TCP %u>%u flags %02x len %zu", be16(t), be16(t + 2), t[13],
				 total - ihl - (t[12] >> 4) * 4);
		} else if (ip[9] == 17)
			snprintf(what, sizeof what, "UDP");
		else
			snprintf(what, sizeof what, "IP proto %u", ip[9]);
	}
	if (*dir == 'T' && busy)
		fprintf(stderr, "NIC TX p%u %zuB %s  +%llu cycles\n", port, f.size(), what, (unsigned long long)(busy() - m_rx_busy));
	else
		fprintf(stderr, "NIC %s p%u %zuB %s\n", dir, port, f.size(), what);
}
