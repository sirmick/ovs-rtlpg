/* b3.c - bank 3: its own code and constants, and a call into bank 1. */
#ifndef HOST
#pragma codeseg BANK3
#pragma constseg BANK3
#endif
#include "bank.h"

static const __code uint8_t tab[16] = {
	121, 66, 189, 242, 33, 6, 240, 132, 119, 98, 240, 243, 203, 77, 118, 77
};
static const __code char name[] = "bank3";

uint8_t b3_table(uint8_t i) __banked
{
	return tab[i & 15] ^ name[i % 5];
}

uint32_t b3_work(uint8_t depth, uint32_t h) __banked
{
	uint8_t i;
	for (i = 0; i < 4; i++)
		h = mix(h, b3_table(i + depth));
	if (depth)
		h = b1_work(depth - 1, h);	/* into bank 1 and back */
	return mix(h, tab[depth & 15]);
}
