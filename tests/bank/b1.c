/* b1.c - bank 1: its own code and constants, and a call into bank 2. */
#ifndef HOST
#pragma codeseg BANK1
#pragma constseg BANK1
#endif
#include "bank.h"

static const __code uint8_t tab[16] = {
	68, 32, 130, 60, 253, 230, 241, 194, 107, 48, 249, 14, 199, 221, 1, 228
};
static const __code char name[] = "bank1";

uint8_t b1_table(uint8_t i) __banked
{
	return tab[i & 15] ^ name[i % 5];
}

uint32_t b1_work(uint8_t depth, uint32_t h) __banked
{
	uint8_t i;
	for (i = 0; i < 4; i++)
		h = mix(h, b1_table(i + depth));
	if (depth)
		h = b2_work(depth - 1, h);	/* into bank 2 and back */
	return mix(h, tab[depth & 15]);
}
