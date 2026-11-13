/* constbug.c - the RTLPlayground repack bug on purpose: the code moved to
 * BANK3 but its constants stayed in BANK2, so they are read with bank 3
 * mapped. The bank checker must report it (see the Makefile). */
#pragma codeseg BANK3
#pragma constseg BANK2
#include "bank.h"

static const __code char msg[] = "IGMP-Entry FOUND";

uint8_t constbug_sum(void) __banked
{
	uint8_t i, s = 0;
	for (i = 0; i < sizeof msg - 1; i++)
		s += msg[i];
	return s;
}
