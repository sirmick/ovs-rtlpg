// json.h - just enough JSON for the board files: objects, arrays, strings
// (\" \\ \/ \n \t escapes), numbers, true, false, null. Throws
// std::runtime_error with the offset on bad input.
#ifndef RTLSIM_JSON_H
#define RTLSIM_JSON_H

#include <cctype>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

struct json {
	enum kind_t { NUL, BOOL, NUM, STR, ARR, OBJ } kind = NUL;
	bool b = false;
	double n = 0;
	std::string s;
	std::vector<json> a;
	std::map<std::string, json> o;

	bool is_null() const { return kind == NUL; }
	const json &operator[](const std::string &k) const
	{
		static const json none;
		auto it = o.find(k);
		return kind == OBJ && it != o.end() ? it->second : none;
	}
	// A number, or a string in C notation ("0x68"); `def` when absent.
	long num(long def = 0) const
	{
		if (kind == NUM)
			return long(n);
		if (kind == STR)
			return strtol(s.c_str(), nullptr, 0);
		return def;
	}

	static json parse(const std::string &text)
	{
		size_t at = 0;
		json v = value(text, at);
		skip(text, at);
		if (at != text.size())
			fail(at, "trailing characters");
		return v;
	}

private:
	[[noreturn]] static void fail(size_t at, const char *what)
	{
		throw std::runtime_error("JSON at offset " + std::to_string(at) + ": " + what);
	}
	static void skip(const std::string &t, size_t &at)
	{
		while (at < t.size() && isspace((unsigned char)t[at]))
			at++;
	}
	static bool word(const std::string &t, size_t &at, const char *w)
	{
		size_t n = strlen(w);
		if (t.compare(at, n, w))
			return false;
		at += n;
		return true;
	}
	static std::string str(const std::string &t, size_t &at)
	{
		std::string r;
		at++;	// "
		while (at < t.size() && t[at] != '"') {
			char c = t[at++];
			if (c == '\\' && at < t.size()) {
				c = t[at++];
				c = c == 'n' ? '\n' : c == 't' ? '\t' : c;
			}
			r += c;
		}
		if (at >= t.size())
			fail(at, "unterminated string");
		at++;
		return r;
	}
	static json value(const std::string &t, size_t &at)
	{
		json v;
		skip(t, at);
		if (at >= t.size())
			fail(at, "unexpected end");
		char c = t[at];
		if (c == '{') {
			v.kind = OBJ;
			at++;
			skip(t, at);
			if (at < t.size() && t[at] == '}') {
				at++;
				return v;
			}
			for (;;) {
				skip(t, at);
				if (at >= t.size() || t[at] != '"')
					fail(at, "expected a key");
				std::string k = str(t, at);
				skip(t, at);
				if (at >= t.size() || t[at++] != ':')
					fail(at, "expected ':'");
				v.o[k] = value(t, at);
				skip(t, at);
				if (at < t.size() && t[at] == ',') {
					at++;
					continue;
				}
				if (at < t.size() && t[at] == '}') {
					at++;
					return v;
				}
				fail(at, "expected ',' or '}'");
			}
		}
		if (c == '[') {
			v.kind = ARR;
			at++;
			skip(t, at);
			if (at < t.size() && t[at] == ']') {
				at++;
				return v;
			}
			for (;;) {
				v.a.push_back(value(t, at));
				skip(t, at);
				if (at < t.size() && t[at] == ',') {
					at++;
					continue;
				}
				if (at < t.size() && t[at] == ']') {
					at++;
					return v;
				}
				fail(at, "expected ',' or ']'");
			}
		}
		if (c == '"') {
			v.kind = STR;
			v.s = str(t, at);
			return v;
		}
		if (word(t, at, "true") || word(t, at, "false")) {
			v.kind = BOOL;
			v.b = t[at - 1] == 'e' && t[at - 2] == 'u';
			return v;
		}
		if (word(t, at, "null"))
			return v;
		char *end;
		v.n = strtod(t.c_str() + at, &end);
		if (end == t.c_str() + at)
			fail(at, "unexpected character");
		v.kind = NUM;
		at = end - t.c_str();
		return v;
	}
};

#endif
