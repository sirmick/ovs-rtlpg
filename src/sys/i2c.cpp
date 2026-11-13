// i2c.cpp - see i2c.h.
#include "sys/i2c.h"

#include <cstdio>

enum : u16 { I2C_CTRL = 0x0418, I2C_IN = 0x0420, I2C_OUT = 0x0424 };
enum : u32 { I2C_GO = 0x01, I2C_FAIL = 0x02, I2C_ADDR_1BYTE = 1u << 20 };

i2c_controller::i2c_controller(switch_core &core) : m_core(core)
{
	m_core.on_write(I2C_CTRL, [this](u32 v) { return v & I2C_GO ? execute(v) : v; });
}

void i2c_controller::attach(unsigned bus, u8 dev, std::vector<u8> mem)
{
	if (mem.empty())
		m_dev.erase({bus, dev});
	else
		m_dev[{bus, dev}] = std::move(mem);
}

u32 i2c_controller::execute(u32 ctrl)
{
	unsigned const bus = (ctrl >> 10) & 7, n = ((ctrl >> 16) & 0xf) + 1;
	u8 const dev = (ctrl >> 3) & 0x7f, reg = m_core.reg(I2C_IN) & 0xff;
	auto it = m_dev.find({bus, dev});
	bool const ok = it != m_dev.end() && (ctrl & I2C_ADDR_1BYTE);
	if (trace)
		fprintf(stderr, "I2C R bus %u dev %02x reg %02x len %u%s\n", bus, dev, reg, n, ok ? "" : ": no ack");
	if (!ok)
		return (ctrl & ~I2C_GO) | I2C_FAIL;
	const std::vector<u8> &mem = it->second;
	u32 out[4] = {};
	for (unsigned i = 0; i < n; i++)
		out[i / 4] |= u32(mem[(reg + i) % mem.size()]) << (8 * (i % 4));
	for (unsigned w = 0; w < 4; w++)
		m_core.set_reg(I2C_OUT + 4 * w, out[w]);
	return ctrl & ~(I2C_GO | I2C_FAIL);
}
