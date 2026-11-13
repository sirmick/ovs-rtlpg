/* t_constbug.c - calls the misplaced-constant code in constbug.c. */
#include "bank.h"
__sfr __at(0x96) PSBANK;	/* crtbank.asm switches banks through it */
uint8_t constbug_sum(void) __banked;

int main(void)
{
	out_kv("tables", b1_table(1) + b2_table(2) + b3_table(3));
	out_kv("sum", constbug_sum());
	halt();
	return 0;
}
