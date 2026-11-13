// rtl8238b.cpp - see rtl8238b.h.
#include "sys/rtl8238b.h"

#include <cstdio>

bool rtl8238b::lines(bool scl, bool sda_master)
{
	bool const sda = sda_master && m_drive;
	if (scl && m_scl && m_sda && !sda)
		start();
	else if (scl && m_scl && !m_sda && sda)
		stop();
	else if (scl && !m_scl) {				// SCL rising: a bit
		if (m_state == ADDR || m_state == RX) {
			m_byte = u8(m_byte << 1) | sda;
			m_bits++;
		} else if (m_state == ACK_TX)
			m_master_ack = !sda;
	} else if (!scl && m_scl) {				// SCL falling: our turn on SDA
		switch (m_state) {
		case ADDR:
		case RX:
			if (m_bits < 8)
				break;
			if (m_state == ADDR) {
				if ((m_byte >> 1) != m_addr) {
					m_state = IDLE;			// not us: wait for a START
					break;
				}
				m_read = m_byte & 1;
				if (m_read)
					m_out = 0;		// reads the register the write before set
				else {
					m_reg = -1;		// a write starts with the register
					m_data.clear();
				}
				m_state = ACK_ADDR;
			} else {
				byte_in(m_byte);
				m_state = ACK_RX;
			}
			m_drive = false;			// ACK
			break;
		case ACK_ADDR:
			if (m_read) {
				m_byte = byte_out();
				m_bits = 0;
				m_drive = m_byte & 0x80;
				m_state = TX;
			} else {
				m_drive = true;
				m_bits = 0;
				m_byte = 0;
				m_state = RX;
			}
			break;
		case ACK_RX:
			m_drive = true;
			m_bits = 0;
			m_byte = 0;
			m_state = RX;
			break;
		case TX:
			if (++m_bits < 8)
				m_drive = (m_byte << m_bits) & 0x80;
			else {
				m_drive = true;				// the master's ACK bit
				m_state = ACK_TX;
			}
			break;
		case ACK_TX:
			if (m_master_ack) {
				m_byte = byte_out();
				m_bits = 0;
				m_drive = m_byte & 0x80;
				m_state = TX;
			} else
				m_state = IDLE;
			break;
		case IDLE:
			break;
		}
	}
	m_scl = scl;
	m_sda = sda_master && m_drive;
	return m_drive;
}

// A START, or a repeated one: a write before it is done (a read keeps its
// register).
void rtl8238b::start()
{
	if (m_state == RX || m_state == ACK_RX)
		stop();
	m_state = ADDR;
	m_bits = 0;
	m_byte = 0;
	m_drive = true;
}

void rtl8238b::stop()
{
	if (!m_read && m_reg >= 0 && !m_data.empty()) {
		auto is = [this](std::initializer_list<u8> v) {
			return m_data.size() >= v.size() && std::equal(v.begin(), v.end(), m_data.begin());
		};
		switch (m_reg) {
		case 0x1a:
			if (m_data[0] == 0x20) {
				m_armed = m_taken = m_alt_ready = false;
				m_image.clear();
				resets++;
				if (trace)
					fprintf(stderr, "RTL8238B: reset\n");
			}
			break;
		case 0x18:
			if (is({0x00, 0x00, 0x20, 0x39}))
				m_alt_ready = true;
			break;
		case 0xec:
			if (is({0x03, 0x00, 0x00, 0x00}) && (!alt_arm || m_alt_ready)) {
				m_armed = true;
				if (trace)
					fprintf(stderr, "RTL8238B: armed\n");
			}
			break;
		case 0xf4:
			if (m_armed) {
				m_image = m_data;
				m_taken = true;
				images++;
				if (on_image)
					on_image(m_image);
			}
			if (trace)
				fprintf(stderr, "RTL8238B: image of %zu bytes %s\n", m_data.size(), m_armed ? "taken" : "ignored (not armed)");
			break;
		}
	}
	m_state = IDLE;
	m_drive = true;
	m_data.clear();
	m_read = false;
}

void rtl8238b::byte_in(u8 b)
{
	if (m_reg < 0)
		m_reg = b;
	else
		m_data.push_back(b);
}

u8 rtl8238b::byte_out()
{
	u32 v = 0;
	if (m_reg == 0xec)
		v = (m_armed ? 0x04 : 0) | (m_taken ? 0x20 : 0);
	else if (m_reg == 0xe8)
		v = e8;
	u8 const b = (m_out < 4) ? u8(v >> (8 * m_out)) : 0;
	m_out++;
	return b;
}
