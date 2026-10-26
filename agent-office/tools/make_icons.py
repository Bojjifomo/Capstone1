#!/usr/bin/env python3
"""Draw the home-screen icons (a pixel guild crest) as PNGs, standard library only."""
import struct
import sys
import zlib
from pathlib import Path

CREST = [
    "................",
    "................",
    "...GGGGGGGGGG...",
    "...GRRRRRRRRG...",
    "...GRRRRYRRRG...",
    "...GRRRYYYRRG...",
    "...GRRRYOYRRG...",
    "...GRRRRWRRRG...",
    "...GRRRRWRRRG...",
    "...GRRRWWWRRG...",
    "....GRRRRRRG....",
    "....GRRRRRRG....",
    ".....GRRRRG.....",
    "......GRRG......",
    ".......GG.......",
    "................",
]
COLORS = {".": (27, 20, 48), "G": (224, 176, 76), "R": (122, 34, 48), "Y": (255, 214, 102),
          "O": (255, 138, 61), "W": (243, 228, 195)}


def png(size, rows):
    scale = size / len(rows)
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        row = rows[min(int(y / scale), len(rows) - 1)]
        for x in range(size):
            raw.extend(COLORS[row[min(int(x / scale), len(row) - 1)]])
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "static" / "icons")
    out.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        (out / name).write_bytes(png(size, CREST))
    print("icons written to", out)


if __name__ == "__main__":
    main()
