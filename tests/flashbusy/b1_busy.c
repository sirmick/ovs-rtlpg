/* b1_busy.c - BANK1: code that must not run while the flash is busy. */
#pragma codeseg BANK1
#pragma constseg BANK1
#include <stdint.h>

static const __code uint8_t tab[8] = { 3, 1, 4, 1, 5, 9, 2, 6 };

uint8_t b1_value(uint8_t x) __banked
{
	return tab[x & 7] * 7 + x;
}
