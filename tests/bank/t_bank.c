/* t_bank.c - home bank: drives the banked code and reads each bank's
 * constants through the trampoline. */
#include "bank.h"
#ifndef HOST
__sfr __at(0x96) PSBANK;
#endif

int main(void)
{
	uint8_t i;
	uint32_t h = 0;

	for (i = 0; i < 16; i++)
		h = mix(h, b1_table(i) + b2_table(i) + b3_table(i));
	out_kv("tables", h);
	out_kv("b1", b1_work(7, 1));
	out_kv("b2", b2_work(8, 2));
	out_kv("b3", b3_work(9, 3));
	h = 0;
	for (i = 0; i < 20; i++)
		h = mix(h, i % 3 == 0 ? b1_work(i, h) : i % 3 == 1 ? b2_work(i, h) : b3_work(i, h));
	out_kv("mixed", h);
#ifndef HOST
	out_kv("psbank", PSBANK);	/* back home: 0 */
#else
	out_kv("psbank", 0);
#endif
	out_s("done\n");
	halt();
	return 0;
}
