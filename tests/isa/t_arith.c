/* t_arith.c - integer arithmetic in every width, signed and unsigned. */
#include "common.h"

#define N 300

/* 8-bit results go in through a uint8_t parameter: SDCC 4.6.0 (small model)
 * drops the cast in mix(h, (uint8_t)(a + b)) and passes the 16-bit sum; see
 * tests/sdcc-bugs/cast_u8_to_u32.c */
static uint32_t mix8(uint32_t h, uint8_t v) { return mix(h, v); }

int main(void)
{
	uint16_t i;
	uint32_t h;

	h = 0;
	for (i = 0; i < N; i++) {	/* 8-bit */
		uint8_t a = rnd(), b = rnd(), s = rnd() & 7;
		int8_t sa = a, sb = b;
		h = mix8(h, (uint8_t)(a + b));
		h = mix8(h, (uint8_t)(a - b));
		h = mix8(h, MUL8(a, b));
		h = mix8(h, b ? a / b : 0);
		h = mix8(h, b ? a % b : 0);
		h = mix8(h, (uint8_t)(a << s) | (uint8_t)(a >> s));
		h = mix8(h, (a & b) ^ (a | b));
		h = mix8(h, (a < b) | (a == b) << 1 | (sa < sb) << 2 | (sa >= sb) << 3);
		if (sb && !(sa == -128 && sb == -1)) {
			h = mix8(h, (uint8_t)(int8_t)(sa / sb));
			h = mix8(h, (uint8_t)(int8_t)(sa % sb));
		}
		/* in 16 bits: SDCC --model-large miscompiles the 8-bit signed
		 * shift by a variable, see tests/sdcc-bugs/sshr8_large.c */
		h = mix8(h, (uint8_t)(int8_t)((int16_t)sa >> s));
	}
	out_kv("u8", h);

	h = 0;
	for (i = 0; i < N; i++) {	/* 16-bit */
		uint16_t a = rnd(), b = rnd(), s = rnd() & 15;
		int16_t sa = a, sb = b;
		if (i & 1)
			b >>= rnd() & 15;	/* small divisors too */
		h = mix(h, (uint16_t)(a + b));
		h = mix(h, (uint16_t)(a - b));
		h = mix(h, MUL16(a, b));
		h = mix(h, (uint16_t)SMUL16(sa, sb));
		h = mix(h, b ? a / b : 0);
		h = mix(h, b ? a % b : 0);
		h = mix(h, (uint16_t)(a << s) | (uint16_t)(a >> s));
		h = mix(h, (uint16_t)~a ^ (uint16_t)-b);
		h = mix(h, (a < b) | (a == b) << 1 | (sa < sb) << 2 | (sa >= sb) << 3);
		sb = b;
		if (sb && !(sa == -32768 && sb == -1)) {
			h = mix(h, (uint16_t)(int16_t)(sa / sb));
			h = mix(h, (uint16_t)(int16_t)(sa % sb));
		}
		h = mix(h, (uint16_t)(int16_t)(sa >> s));
	}
	out_kv("u16", h);

	h = 0;
	for (i = 0; i < N; i++) {	/* 32-bit */
		uint32_t a = rnd(), b = rnd();
		uint8_t s = rnd() & 31;
		int32_t sa, sb;
		if (i & 1)
			b >>= rnd() & 31;
		sa = a;
		sb = b;
		h = mix(h, a + b);
		h = mix(h, a - b);
		h = mix(h, a * b);
		h = mix(h, b ? a / b : 0);
		h = mix(h, b ? a % b : 0);
		h = mix(h, (a << s) ^ (a >> s));
		h = mix(h, (a < b) | (a == b) << 1 | (sa < sb) << 2 | (sa >= sb) << 3);
		if (sb && !(sa == INT32_MIN && sb == -1)) {
			h = mix(h, (uint32_t)(sa / sb));
			h = mix(h, (uint32_t)(sa % sb));
		}
		h = mix(h, (uint32_t)(sa >> s));
	}
	out_kv("u32", h);

	h = 0;
	for (i = 0; i < N; i++) {	/* conversions between widths */
		uint32_t r = rnd();
		int8_t c = r;
		int16_t w = r;
		h = mix(h, (uint32_t)(int32_t)c);
		h = mix(h, (uint32_t)(int32_t)w);
		h = mix(h, (uint16_t)(int16_t)c);
		h = mix(h, (uint8_t)(r >> 3));
	}
	out_kv("conv", h);

	out_s("done\n");
	halt();
	return 0;
}
