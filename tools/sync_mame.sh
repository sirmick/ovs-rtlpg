#!/bin/sh
# Re-copy MAME's mcs51 core (unchanged) into third_party/mame/mcs51.
set -e
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
git clone -q --depth 1 --filter=blob:none --sparse https://github.com/mamedev/mame.git "$tmp"
git -C "$tmp" sparse-checkout set src/devices/cpu/mcs51
for f in i8051.h i8051.cpp mcs51ops.cpp i8052.h i8052.cpp dw8051.h dw8051.cpp mcs51dasm.h mcs51dasm.cpp; do
	cp "$tmp/src/devices/cpu/mcs51/$f" third_party/mame/mcs51/
done
sed -i "s/^Commit: .*/Commit: $(git -C "$tmp" rev-parse HEAD)/" third_party/mame/UPSTREAM
rm -rf "$tmp"
