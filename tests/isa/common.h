/* common.h - output and portability for the differential ISA tests.
 *
 * Each test is plain C that prints what it computes. It is built twice: with
 * SDCC for rtlsim, and with the host compiler (-DHOST). The outputs must be
 * identical. Arithmetic is written so that C gives the same answer with a
 * 16-bit int (SDCC) and a 32-bit int (host): the MUL/SHL macros below spell
 * out where the promotions differ.
 */
#ifndef ISA_COMMON_H
#define ISA_COMMON_H

#include <stdint.h>
#include <string.h>

#ifdef HOST
#include <stdio.h>
#define __xdata
#define __pdata
#define __idata
#define __data
#define __code
#define __reentrant
static void out_c(char c) { putchar(c); }
static void halt(void) { }
#else
#include <8051.h>
__sfr __at(0x92) _XPAGE;	/* pdata page in MPAGE, as the firmware has it */
static void out_c(char c) { SBUF = c; while (!TI); TI = 0; }
static void halt(void) { __asm sjmp . __endasm; }
#endif

/* products and left shifts in the operand's own width */
#define MUL8(a, b)  ((uint8_t)((uint16_t)(a) * (uint16_t)(b)))
#define MUL16(a, b) ((uint16_t)((uint32_t)(uint16_t)(a) * (uint16_t)(b)))
#define SMUL16(a, b) ((int16_t)(uint16_t)((uint32_t)(uint16_t)(a) * (uint16_t)(b)))

static void out_s(const char *s) { while (*s) out_c(*s++); }
static void out_h4(uint8_t v) { out_c("0123456789abcdef"[v & 15]); }
static void out_h8(uint8_t v) { out_h4(v >> 4); out_h4(v); }
static void out_h16(uint16_t v) { out_h8(v >> 8); out_h8(v); }
static void out_h32(uint32_t v) { out_h16(v >> 16); out_h16(v); }
static void out_kv(const char *k, uint32_t v) { out_s(k); out_c('='); out_h32(v); out_c('\n'); }

/* the PRNG every test draws its operands from */
static uint32_t rng = 0x12345678;
static uint32_t rnd(void)
{
	rng ^= rng << 13;
	rng ^= rng >> 17;
	rng ^= rng << 5;
	return rng;
}

/* fold a value into a running check value */
static uint32_t mix(uint32_t h, uint32_t v)
{
	h ^= v;
	h = (h << 7) | (h >> 25);
	return h + 0x9e3779b9;
}

#endif
