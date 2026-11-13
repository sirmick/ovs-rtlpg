/* t_control.c - calls, recursion, function pointers, jump tables, structs,
 * the string library, constants in code memory. */
#include "common.h"

static uint16_t fib(uint8_t n) __reentrant
{
	return n < 2 ? n : fib(n - 1) + fib(n - 2);
}

static uint16_t ack(uint8_t m, uint16_t n) __reentrant
{
	if (!m)
		return n + 1;
	if (!n)
		return ack(m - 1, 1);
	return ack(m - 1, ack(m, n - 1));
}

static uint16_t op_add(uint16_t a, uint16_t b) __reentrant { return a + b; }
static uint16_t op_xor(uint16_t a, uint16_t b) __reentrant { return a ^ b; }
static uint16_t op_mul(uint16_t a, uint16_t b) __reentrant { return MUL16(a, b); }
static uint16_t op_sub(uint16_t a, uint16_t b) __reentrant { return a - b; }
static uint16_t (*const ops[])(uint16_t, uint16_t) __reentrant = { op_add, op_xor, op_mul, op_sub };

static uint8_t dense(uint8_t v)
{
	switch (v) {
	case 0: return 11;
	case 1: return 22;
	case 2: return 33;
	case 3: return 44;
	case 4: return 55;
	case 5: return 66;
	case 6: return 77;
	case 7: return 88;
	case 8: return 99;
	case 9: return 111;
	default: return 1;
	}
}

static uint16_t sparse(uint16_t v)
{
	switch (v) {
	case 3: return 1;
	case 100: return 2;
	case 1000: return 3;
	case 0x7fff: return 4;
	case 0xfffe: return 5;
	default: return v & 7;
	}
}

struct rec {
	uint8_t tag;
	uint16_t len;
	uint32_t val;
	char name[6];
};

static struct rec mkrec(uint8_t t)
{
	struct rec r;
	r.tag = t;
	r.len = t * 3u;
	r.val = 0x01010101ul * t;
	memcpy(r.name, "abcde", 6);
	r.name[t % 5] = 'A' + t % 26;
	return r;
}

static const __code uint8_t table[32] = {
	3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3,
	2, 3, 8, 4, 6, 2, 6, 4, 3, 3, 8, 3, 2, 7, 9, 5,
};

static __xdata uint8_t buf[64];

int main(void)
{
	uint16_t i, j;
	uint32_t h;
	struct rec r, s;

	h = 0;
	for (i = 0; i < 20; i++)
		h = mix(h, fib(i));
	h = mix(h, ack(2, 3));
	out_kv("recursion", h);

	h = 0;
	for (i = 0; i < 200; i++)
		h = mix(h, ops[rnd() & 3](rnd(), rnd()));
	out_kv("fnptr", h);

	h = 0;
	for (i = 0; i < 200; i++) {
		h = mix(h, dense(rnd() % 12));
		h = mix(h, sparse(i & 1 ? rnd() : (uint16_t[]){ 3, 100, 1000, 0x7fff, 0xfffe, 9 }[i % 6]));
	}
	out_kv("switch", h);

	h = 0;
	for (i = 0; i < 30; i++) {
		r = mkrec(i);
		s = r;
		s.name[0] ^= 1;
		h = mix(h, r.tag + r.len + r.val + (uint8_t)r.name[i % 5]);
		h = mix(h, memcmp(&r, &s, sizeof r) != 0);
		h = mix(h, strcmp(r.name, s.name) < 0);
	}
	out_kv("struct", h);

	h = 0;
	for (i = 0; i < sizeof buf; i++)
		buf[i] = rnd();
	for (i = 1; i < sizeof buf; i++)	/* insertion sort */
		for (j = i; j > 0 && buf[j - 1] > buf[j]; j--) {
			uint8_t t = buf[j];
			buf[j] = buf[j - 1];
			buf[j - 1] = t;
		}
	for (i = 0; i < sizeof buf; i++)
		h = mix(h, buf[i]);
	memset(buf, 0x5a, 10);
	memmove(buf + 3, buf, 20);
	for (i = 0; i < 24; i++)
		h = mix(h, buf[i]);
	h = mix(h, strlen("hello, rtlsim"));
	out_kv("memory", h);

	h = 0;
	for (i = 0; i < 100; i++)
		h = mix(h, table[rnd() & 31]);
	out_kv("code", h);

	out_s("done\n");
	halt();
	return 0;
}
