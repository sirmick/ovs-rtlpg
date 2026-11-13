/* bank.h - the banking test: one function and one constant table per bank,
 * calling across banks. Built like the firmware (crtbank.asm trampoline,
 * BANKn linked at n*0x10000 + 0x4000) and for the host. */
#ifndef BANK_H
#define BANK_H
#include "../isa/common.h"
#ifdef HOST
#define __banked
#endif
uint32_t b1_work(uint8_t depth, uint32_t h) __banked;
uint32_t b2_work(uint8_t depth, uint32_t h) __banked;
uint32_t b3_work(uint8_t depth, uint32_t h) __banked;
uint8_t b1_table(uint8_t i) __banked;
uint8_t b2_table(uint8_t i) __banked;
uint8_t b3_table(uint8_t i) __banked;
#endif
