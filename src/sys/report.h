// report.h - the model's findings about the firmware: what it did that the
// model doesn't cover (an unmodelled register or SFR, a flash command) or
// shouldn't do. They go to stderr, as they always have; with a sink set (the
// control socket's `checks`), to the sink instead.
#ifndef RTLSIM_REPORT_H
#define RTLSIM_REPORT_H

#include <functional>
#include <string>

// kind: "unmodelled", "bank-ret", ...; text: the message, without a newline.
extern std::function<void(const char *kind, const std::string &text)> report_sink;

void report(const char *kind, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

#endif
