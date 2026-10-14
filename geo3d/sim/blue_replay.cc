// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
//
// blue_replay: runs a geo3d stimulus file (the format of tb_engine.v) through
// the Geo3DCore of blueMSX+ (Hesoten's port of the openMSX device's core,
// which adds the engine's time) and writes
//   - the log in the testbench's format (L / M / K / G / F lines), to compare
//     with the *_expect.txt files of the reference model;
//   - with a third argument, the producer's clock of every command and of the
//     end of every RUN ("C <clock>", "E <clock>", "F <n>"), to compare with
//     what tb_engine.v writes with +ec=<file>. The two differ by a constant
//     (where each one starts counting): check_ec.py compares them.
//
// Build, with <blue> = blueMSX-plus/blueMSX/Src/VideoChips:
//   g++ -std=c++20 -O2 -I<blue> blue_replay.cc <blue>/Geo3DCore.cpp -o blue_replay

#include "Geo3DCore.h"

#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

int main(int argc, char** argv)
{
	if (argc != 3 && argc != 4) {
		std::cerr << "usage: blue_replay <stim.txt> <got.txt> [<ec.txt>]\n";
		return 2;
	}
	std::ifstream in(argv[1]);
	if (!in) { std::cerr << "cannot open " << argv[1] << '\n'; return 1; }
	FILE* out = std::fopen(argv[2], "w");
	if (!out) { std::cerr << "cannot write " << argv[2] << '\n'; return 1; }
	FILE* ec = (argc == 4) ? std::fopen(argv[3], "w") : nullptr;

	Geo3DCore g;
	unsigned nCmd = 0, frames = 0;
	bool wasRunning = false;
	std::string line;
	// a RUN: every command with the clock at which the producer has it
	// ready, then the clock at which it reaches R_DRAIN
	auto drain = [&] {
		while (g.hasCommand()) {
			const auto& c = g.nextCommand();
			if (ec) std::fprintf(ec, "C %u\n", unsigned(c.ready));
			std::fputs(c.isLrmm() ? "M" : "L", out);
			for (unsigned k = 0; k < c.size; ++k) std::fprintf(out, " %02x", c.bytes[k]);
			std::fputc('\n', out);
			++nCmd;
		}
		if (ec) std::fprintf(ec, "E %u\n", unsigned(g.endClock()));
		g.finishRun();
	};
	while (std::getline(in, line)) {
		std::istringstream is(line);
		std::string op;
		if (!(is >> op)) continue;
		if (op == "W") {
			unsigned sel, b;
			is >> sel >> std::hex >> b;
			if (sel & 1) g.writeData(uint8_t(b)); else g.writeIndex(uint8_t(b));
			wasRunning = g.isRunning();
		} else if (op == "R") {
			if (wasRunning) drain();
			wasRunning = false;
		} else if (op == "K") {
			g.writeIndex(0x4A);
			uint8_t k[6];
			for (auto& x : k) x = g.readData();
			std::fprintf(out, "K %02x%02x %02x%02x %02x%02x\n", k[1], k[0], k[3], k[2], k[5], k[4]);
		} else if (op == "G") {
			unsigned idx, n;
			is >> std::hex >> idx >> std::dec >> n;
			g.writeIndex(uint8_t(idx));
			std::fprintf(out, "G %02x", idx);
			for (unsigned k = 0; k < n; ++k) std::fprintf(out, " %02x", g.readData());
			std::fputc('\n', out);
		} else if (op == "F") {
			unsigned n;
			is >> n;
			std::fprintf(out, "F %u\n", n);
			if (ec) std::fprintf(ec, "F %u\n", n);
			++frames;
		}
	}
	std::fclose(out);
	if (ec) std::fclose(ec);
	std::printf("frames: %u, VDP commands: %u\n", frames, nCmd);
	return 0;
}
