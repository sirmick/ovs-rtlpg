// flashctl.cpp - see flashctl.h.
#include "sys/flashctl.h"

#include <cstdio>
#include <cstring>

enum : u8 {
	EXEC = 0x80, MODEB = 0x9a, CONF_DIV = 0x9b, CONF_RCMD = 0x9c, DUMMY = 0x9d, TCONF = 0x9e,
	ADDR0 = 0xa9, ADDR8 = 0xaa, ADDR16 = 0xab, DATA0 = 0xac, DATA8 = 0xad, DATA16 = 0xae, DATA24 = 0xaf,
	CMD_R = 0xb1, CMD = 0xb2, CONFIG = 0xbc,
};
enum : u8 { EXEC_BUSY = 0x01, EXEC_GO = 0x04 };

flash_controller::flash_controller(const std::vector<u8> &image, u32 chip_size, u8 jedec_mfr, u8 jedec_type)
	: m_mfr(jedec_mfr), m_type(jedec_type)
{
	fd_init(&m_dev, chip_size);
	fd_load(&m_dev, 0, image.data(), std::min<u32>(image.size(), chip_size));
	m_cap = 0;
	while ((1u << m_cap) < chip_size)
		m_cap++;
}

flash_controller::~flash_controller()
{
	fd_free(&m_dev);
}

bool flash_controller::owns(u8 s)
{
	return s == EXEC || (s >= MODEB && s <= TCONF) || (s >= ADDR0 && s <= DATA24) || s == CMD_R || s == CMD || s == CONFIG;
}

u8 flash_controller::sfr_r(u8 s)
{
	if (s == EXEC)
		return m_sfr[EXEC] & ~(EXEC_BUSY | EXEC_GO);	// every transaction is done at once
	return m_sfr[s];
}

void flash_controller::sfr_w(u8 s, u8 d)
{
	m_sfr[s] = d;
	if (s == EXEC && (d & EXEC_GO))
		execute();
}

void flash_controller::execute()
{
	u8 const tconf = m_sfr[TCONF];
	unsigned const len = std::min(tconf & 7, 4);
	bool const write = tconf & 0x08;
	u32 const addr = (m_sfr[ADDR16] << 16) | (m_sfr[ADDR8] << 8) | m_sfr[ADDR0];
	u8 *data = &m_sfr[DATA0];	// DATA0, DATA8, DATA16, DATA24: consecutive SFRs
	u8 const cmd = write ? m_sfr[CMD] : m_sfr[CMD_R];

	if (trace)
		fprintf(stderr, "FLASH %s cmd=%02x tconf=%02x addr=%06x\n", write ? "W" : "R", cmd, tconf, addr);

	if (write) {
		switch (cmd) {
		case 0x06: m_wel = true; break;				// write enable
		case 0x04: m_wel = false; break;			// write disable
		case 0x01: m_status = data[0] & 0xfc; m_wel = false; break;	// write status
		case 0x02:						// page program
			if (m_wel)
				fd_write(&m_dev, addr, data, len);
			m_wel = false;
			break;
		case 0x20:						// sector erase
			if (m_wel)
				fd_erase_sector(&m_dev, addr);
			m_wel = false;
			break;
		case 0xd8:						// 64K block erase
			if (m_wel)
				for (u32 a = addr & ~0xffffu; a < (addr & ~0xffffu) + 0x10000; a += FD_SECTOR_SIZE)
					fd_erase_sector(&m_dev, a);
			m_wel = false;
			break;
		default:
			fprintf(stderr, "flash: unmodelled write command %02x (tconf %02x)\n", cmd, tconf);
		}
		return;
	}

	switch (cmd) {
	case 0x05:						// read status
		data[0] = m_status | (m_wel ? 0x02 : 0);
		break;
	case 0x9f:						// JEDEC id
		data[0] = m_mfr;
		data[1] = m_type;
		data[2] = m_cap;
		break;
	case 0x03: case 0x0b: case 0x3b: case 0xbb:		// reads, any width
		fd_read(&m_dev, addr, data, len);
		break;
	case 0x4b:						// unique id
		for (unsigned i = 0; i < len; i++)
			data[i] = 0x50 + i;
		break;
	case 0x48:						// security registers
		memset(data, 0xff, len);
		break;
	default:
		fprintf(stderr, "flash: unmodelled read command %02x (tconf %02x)\n", cmd, tconf);
	}
}
