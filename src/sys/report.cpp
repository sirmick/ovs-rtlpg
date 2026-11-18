// report.cpp - see report.h.
#include "sys/report.h"

#include <cstdarg>
#include <cstdio>

std::function<void(const char *kind, const std::string &text)> report_sink;

void report(const char *kind, const char *fmt, ...)
{
	char buf[512];
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(buf, sizeof buf, fmt, ap);
	va_end(ap);
	if (report_sink)
		report_sink(kind, buf);
	else
		fprintf(stderr, "%s\n", buf);
}
