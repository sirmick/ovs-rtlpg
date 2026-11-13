// logmacro.h - MAME's verbosity-masked logging, for the vendored core.
#ifndef VERBOSE
#define VERBOSE 0
#endif
#ifndef LOG_OUTPUT_FUNC
#define LOG_OUTPUT_FUNC logerror
#endif
#define LOG_GENERAL (1U << 0)
#define LOGMASKED(mask, ...) do { if (VERBOSE & (mask)) LOG_OUTPUT_FUNC(__VA_ARGS__); } while (false)
#define LOG(...) LOGMASKED(LOG_GENERAL, __VA_ARGS__)
