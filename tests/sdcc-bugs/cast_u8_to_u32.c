/* SDCC 4.6.0 #16555 (RTLPlayground toolchain, 4 patches), -mmcs51 --model-small:
 * the (uint8_t) cast is dropped when its result is passed as a uint32_t.
 *
 *   sdcc -mmcs51 --model-small -c cast_u8_to_u32.c
 *
 * t1 passes the 16-bit sum, sign-extended (dph = high byte of a + b), where
 * C requires (a + b) & 0xff. t2, through a uint8_t variable, is right.
 * Found by rtlsim's differential ISA test (tests/isa/t_arith.c).
 * Also in stock SDCC 4.6.0 #16555 (no patches): an upstream bug.
 */
#include <stdint.h>

volatile uint32_t out;
void f(uint32_t v) { out = v; }
uint8_t a, b;

void t1(void) { f((uint8_t)(a + b)); }		/* wrong */
void t2(void) { uint8_t s = a + b; f(s); }	/* right */
