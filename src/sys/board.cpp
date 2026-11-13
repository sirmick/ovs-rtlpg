// board.cpp - see board.h.
#include "sys/board.h"
#include "sys/json.h"

#include <fstream>
#include <iterator>
#include <stdexcept>
#include <strings.h>

board board_swtg024as()
{
	board b;
	b.name = "swgt024_v2_0_unmanaged";
	b.ports = { {4, "1"}, {5, "2"}, {6, "3"}, {7, "4"}, {8, "5"}, {3, "6"} };
	b.sfp = { {1, 8, 30, 37, 4, ""}, {2, 3, 50, 51, 3, ""} };
	return b;
}

// SDA pin -> I2C bus (machine_defs.h I2C_BUS_FROM_SDA_PIN)
static unsigned sda_bus(long pin)
{
	switch (pin) {
	case 47: return 0;
	case 49: return 1;
	case 51: return 2;
	case 41: return 3;
	case 39: return 4;
	}
	throw std::runtime_error("GPIO " + std::to_string(pin) + " is no I2C SDA pin (47, 49, 51, 41, 39)");
}

static u32 size_of(const json &v, u32 def)
{
	if (v.kind != json::STR)
		return v.num(def);
	char *end;
	unsigned long n = strtoul(v.s.c_str(), &end, 0);
	if (*end == 'K' || *end == 'k')
		n <<= 10;
	else if (*end == 'M' || *end == 'm')
		n <<= 20;
	return n;
}

board load_board(const std::string &path)
{
	std::ifstream f(path);
	if (!f)
		throw std::runtime_error("cannot read " + path);
	json j;
	try {
		j = json::parse(std::string(std::istreambuf_iterator<char>(f), {}));
	} catch (const std::runtime_error &e) {
		throw std::runtime_error(path + ": " + e.what());
	}
	std::string const dir = path.find('/') == std::string::npos ? "" : path.substr(0, path.rfind('/') + 1);
	board b;
	b.name = j["name"].s;
	if (b.name.empty())
		throw std::runtime_error(path + ": no \"name\"");
	std::string const chip = j["chip"].s;
	if (!strncasecmp(chip.c_str(), "RTL8373", 7))
		b.id.rtl8373 = true;
	else if (!chip.empty() && strncasecmp(chip.c_str(), "RTL8372", 7))
		throw std::runtime_error(path + ": \"chip\" is RTL8372 or RTL8373");
	b.id.n_variant = chip.size() > 7 && toupper(chip[7]) == 'N';
	b.id.revision = j["revision"].num(b.id.revision);
	b.id.uuid = j["uuid"].num(b.id.uuid);
	b.id.lot_no = j["lot"].num(b.id.lot_no);
	b.flash_size = size_of(j["flash"]["size"], b.flash_size);
	if (b.flash_size & (b.flash_size - 1))
		throw std::runtime_error(path + ": the flash size must be a power of two");
	if (j["flash"]["jedec"].kind == json::STR) {
		unsigned long id = strtoul(j["flash"]["jedec"].s.c_str(), nullptr, 16);
		b.jedec_mfr = id >> 8;
		b.jedec_type = id;
	}
	for (auto &p : j["ports"].a) {
		long const phys = p["phys"].num(-1);
		if (phys < 0 || phys > 9)
			throw std::runtime_error(path + ": a port needs \"phys\" 0-9");
		b.ports.push_back({unsigned(phys), p["front"].s.empty() ? std::to_string(phys) : p["front"].s});
		const json &s = p["sfp"];
		if (s.is_null())
			continue;
		const json &i2c = s["i2c"];
		if (i2c.kind != json::ARR || i2c.a.empty())
			throw std::runtime_error(path + ": SFP on port " + std::to_string(phys) + " needs \"i2c\": [SDA, SCL]");
		board_sfp c;
		c.slot = s["slot"].num(b.sfp.size() + 1);
		c.phys = phys;
		c.detect_gpio = s["detect_gpio"].num(board::NO_GPIO);
		c.los_gpio = s["los_gpio"].num(board::NO_GPIO);
		c.i2c_bus = sda_bus(i2c.a[0].num());
		if (!s["module"].s.empty())
			c.module = s["module"].s[0] == '/' ? s["module"].s : dir + s["module"].s;
		b.sfp.push_back(c);
	}
	return b;
}
