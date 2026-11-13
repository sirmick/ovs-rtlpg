// rtl8238b.h - the PoE controller of the POE-2G040210GSM (a stub), on the
// I2C bus the firmware bit-bangs over two GPIO pins.
//
// The bus side is a plain I2C slave: it watches the two lines as the
// master leaves them (open drain: a pin is low while its direction is
// output with the latch 0, else pulled up), finds START and STOP, takes
// bits on SCL rising, and drives SDA low for its ACKs and the 0 bits it
// sends, changing SDA only while SCL is low. No clock stretching.
//
// The chip side, from RTLPlayground's machine/POE_2G040210GSM_init.c
// (pse_load()): 4-byte registers, written as [addr W][reg][data...] and read
// as [addr W][reg], repeated START, [addr R][4 bytes].
//   0x1a  write 0x20: reset (disarms, forgets the image)
//   0xec  write 03 00 00 00: arm; with alt_arm it arms only after 0x18 was
//         written 00 00 20 39. Read: byte 0 bit 2 armed, bit 5 the image was
//         taken (a whole 0xf4 transfer while armed)
//   0xe8  read: the chip type, e8 (LE; 2 selects the firmware's image 1)
//   0xf4  write: the image, one transfer; kept in image()
// Nothing is known of what a real chip answers before it's armed; these are
// the firmware's expectations, as options.
#ifndef RTLSIM_RTL8238B_H
#define RTLSIM_RTL8238B_H

#include "emu.h"

#include <functional>
#include <vector>

class rtl8238b
{
public:
	explicit rtl8238b(u8 addr) : m_addr(addr) {}

	// The lines as the master leaves them (true: released, high). Returns
	// what this device does to SDA (true: leaves it released).
	bool lines(bool scl, bool sda);

	u32 e8 = 0;			// what 0xe8 reads (chip type)
	bool alt_arm = false;		// arm only after 0x18 = 00 00 20 39
	bool trace = false;

	const std::vector<u8> &image() const { return m_image; }	// the last image taken
	std::function<void(const std::vector<u8> &)> on_image;		// called with each one
	unsigned resets = 0, images = 0;
	bool armed() const { return m_armed; }

private:
	enum state_t { IDLE, ADDR, ACK_ADDR, RX, ACK_RX, TX, ACK_TX };
	void start();
	void stop();
	void byte_in(u8 b);
	u8 byte_out();

	u8 m_addr;
	state_t m_state = IDLE;
	bool m_scl = true, m_sda = true;	// the lines as last seen
	bool m_drive = true;			// our SDA: true = released
	u8 m_byte = 0;
	int m_bits = 0;
	bool m_read = false, m_master_ack = false;
	// the transfer
	int m_reg = -1;
	std::vector<u8> m_data;
	unsigned m_out = 0;
	// the chip
	bool m_armed = false, m_taken = false, m_alt_ready = false;
	std::vector<u8> m_image;
};

#endif
