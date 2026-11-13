// nic.h - the RTL837x CPU port: the NIC between the switch core and the
// 8051, with its packet memory, DMA engine and interrupt.
//
// From RTLPlayground's rtl837x_nic.c, doc/CpuPort.md and rtlplayground.c:
// - DMA (SFRs): 0xb3/0xb4 the xdata address, 0xb5/0xb6 the NIC memory
//   address (bit 15 set: the TX area), 0xb7 the number of 8-byte words;
//   writing 0xb7 starts it, it reads 0 when done (at once, here). An RX
//   transfer that starts inside the RX ring wraps at its end; one starting
//   beyond it reads flat memory.
// - RX ring: NIC_RXBUFF_RX (0x7848) is its last 8-byte slot (0x4ff). A frame
//   is a descriptor (seq, -, -, port in bits 3:0, length LE, -, -) and the
//   frame, padded to 8 bytes. NIC_RX_BUFF_DATA (0x7874) is the slots in use
//   (nonzero while frames wait), CPU_RX_CURR_PKT (0x787c) the slot of the
//   oldest one, NIC_RXCMD (0x784c) = 1 releases it. A frame arrives with the
//   CPU tag after the MACs (0x8899, version 4, reason, flags, the ingress
//   port) and an 802.1Q tag after that (the port's PVID if it came in
//   untagged), so the EtherType is at offset 24.
// - TX area: NIC_BUFFSIZE_TX (0x7844) slots. CPU_TX_CURR_PKT (0x7890) is
//   where the next frame goes, NIC_TX_CURR_PKT (0x7884) the same here. The
//   firmware DMAs a descriptor (seq, checksum offload 0x07, -, -, length LE,
//   -, -) and the frame there and writes NIC_TXCMD (0x7850) = 1. A frame
//   with the CPU tag goes to its pmask (bit 15 ALLOW: looked up, limited to
//   the mask); anything else is switched as if it came in on the CPU port.
// - Interrupt: NIC_INT_STS (0x7854) bit 1 RXIS, set when a frame arrives,
//   write 1 to clear; NIC_INT_MSK (0x7858) bit 1. INT1 is asserted while
//   RXIS is set and unmasked (level).
//
// Until the fabric exists, "switched" is a stand-in for the L2 lookup: the
// frame floods to its VLAN's members (the frame's tag, or the CPU port's
// PVID) that have a peer, with the VLAN's untag mask applied on egress. In
// the same way receive() takes every frame a peer sends, if its VLAN has the
// CPU as a member. The ring and the tags are the chip's; the forwarding
// decisions belong to the fabric and move there with OVS.
#ifndef RTLSIM_NIC_H
#define RTLSIM_NIC_H

#include "sys/switch_core.h"
#include "sys/tables.h"

#include <deque>
#include <functional>
#include <vector>

class cpu_nic
{
public:
	static constexpr unsigned CPU_PORT = 9;

	cpu_nic(switch_core &core, switch_tables &tables);

	static bool owns(u8 sfr) { return sfr >= 0xb3 && sfr <= 0xb7; }
	u8 sfr_r(u8 sfr);
	void sfr_w(u8 sfr, u8 d);
	void reset();

	// A frame (without FCS) that came in on physical port `port`. False if
	// the CPU doesn't get it: NIC RX off, not in a VLAN with the CPU, or no
	// room in the ring.
	bool receive(unsigned port, const u8 *frame, size_t len);

	std::function<u8(u16)> xdata_r;
	std::function<void(u16, u8)> xdata_w;
	std::function<void(bool)> irq;		// INT1 level
	// A frame the CPU sent, leaving physical port `port` (without FCS).
	std::function<void(unsigned port, const std::vector<u8> &frame)> transmit;
	u16 peers = 0;				// physical ports with something on the wire
	std::function<u64()> busy;		// the CPU's busy machine cycles, for traces
	bool trace = false;

	struct counters {
		u64 rx = 0, rx_dropped = 0, tx = 0, tx_dropped = 0;
	} stats;

private:
	struct rx_frame {
		u16 slot, slots;
	};
	unsigned rx_slots() const;
	unsigned tx_slots() const;
	void rx_write(unsigned off, const u8 *p, size_t n);
	void dma(u8 words);
	void rx_release();
	void tx_send();
	void forward(std::vector<u8> f);
	u16 pvid(unsigned port) const;
	void update_irq();
	void note(const char *dir, unsigned port, const std::vector<u8> &f);

	switch_core &m_core;
	switch_tables &m_tables;
	u8 m_sfr[8] = {};			// 0xb3-0xb7
	std::vector<u8> m_rx, m_tx;		// NIC memory: the RX ring side, the TX area
	std::deque<rx_frame> m_frames;
	unsigned m_rx_used = 0, m_rx_tail = 0;
	unsigned m_tx_slot = 0;
	u8 m_seq = 0;
	u32 m_sts = 0;
	bool m_irq = false;
	u64 m_rx_busy = 0;			// busy cycles when the last frame arrived
};

#endif
