/* t_spaces.c - data in every 8051 memory space, through direct and generic
 * pointers. __pdata goes through movx @Ri, so MPAGE. */
#include "common.h"

static __data uint8_t d[16];
static __idata uint8_t id[32];
static __xdata uint8_t xd[300];
static __pdata uint8_t pd[64];
static const __code char text[] = "the quick brown fox";

static uint32_t sum(const uint8_t *p, uint16_t n)
{
	uint32_t h = 0;
	while (n--)
		h = mix(h, *p++);
	return h;
}

static void fill(uint8_t *p, uint16_t n)
{
	while (n--)
		*p++ = rnd();
}

int main(void)
{
	uint16_t i;
	uint32_t h;

	fill(d, sizeof d);
	fill(id, sizeof id);
	fill(xd, sizeof xd);
	fill(pd, sizeof pd);

	h = 0;
	for (i = 0; i < sizeof d; i++)
		h = mix(h, d[i]);
	out_kv("data", h);
	h = 0;
	for (i = 0; i < sizeof id; i++)
		h = mix(h, id[i]);
	out_kv("idata", h);
	h = 0;
	for (i = 0; i < sizeof xd; i++)
		h = mix(h, xd[i]);
	out_kv("xdata", h);
	h = 0;
	for (i = 0; i < sizeof pd; i++)
		h = mix(h, pd[i]);
	out_kv("pdata", h);

	/* the same through generic pointers */
	h = mix(sum(d, sizeof d), sum(id, sizeof id));
	h = mix(h, sum(xd, sizeof xd));
	h = mix(h, sum(pd, sizeof pd));
	h = mix(h, sum((const uint8_t *)text, sizeof text));
	out_kv("generic", h);

	/* copies between spaces */
	memcpy(xd, text, sizeof text);
	memcpy(pd, xd, 20);
	memcpy(id, pd, 20);
	memcpy(d, id + 4, 16);
	out_kv("copy", sum(d, 16));

	out_s("done\n");
	halt();
	return 0;
}
