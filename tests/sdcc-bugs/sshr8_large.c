/* SDCC 4.6.0 #16555 (RTLPlayground toolchain, 4 patches), -mmcs51 --model-large:
 * an 8-bit signed right shift by a variable count loses the operand.
 *
 *   sdcc -mmcs51 --model-large -c sshr8_large.c
 *
 * The shift sequence loads A with "mov acc,r6", then "rlc a; mov ov,c" to take
 * the sign, and does not reload A before the "rrc a" loop (the small model
 * emits "mov a,r4" there). The result is one shift short, with a stray bit:
 * -24 >> 2 gives -12, not -6. It happens whenever the operand is in A first
 * (a volatile xdata variable loaded with movx does it too). Found by tests/isa/t_arith.c.
 * Also in stock SDCC 4.6.0 #16555 (no patches): an upstream bug.
 */
#include <stdint.h>
void f(uint8_t v);
uint8_t g(void);
void t(void)
{
	uint8_t i;
	for (i = 0; i < 10; i++) {
		uint8_t a = g(), s = g() & 7;
		int8_t sa = a;
		f(a);
		f((uint8_t)(int8_t)(sa >> s));
	}
}
