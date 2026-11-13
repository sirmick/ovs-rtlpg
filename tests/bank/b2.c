/* b2.c - bank 2: its own code and constants, and a call into bank 3. */
#ifndef HOST
#pragma codeseg BANK2
#pragma constseg BANK2
#endif
#include "bank.h"

static const __code uint8_t tab[16] = {
	28, 46, 43, 184, 86, 157, 128, 108, 18, 81, 220, 201, 190, 227, 137, 18
};
static const __code char name[] = "bank2";

uint8_t b2_table(uint8_t i) __banked
{
	return tab[i & 15] ^ name[i % 5];
}

uint32_t b2_work(uint8_t depth, uint32_t h) __banked
{
	uint8_t i;
	for (i = 0; i < 4; i++)
		h = mix(h, b2_table(i + depth));
	if (depth)
		h = b3_work(depth - 1, h);	/* into bank 3 and back */
	return mix(h, tab[depth & 15]);
}
