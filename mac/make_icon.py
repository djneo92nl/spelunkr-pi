#!/usr/bin/env python3
"""Draw the app icon (a little pixel-art TV) and write an .iconset folder. No imaging library needed.

    python3 mac/make_icon.py OUT.iconset && iconutil -c icns OUT.iconset
"""
import os
import struct
import sys
import zlib

N = 32                                     # pixel grid
PAL = {"_": None, "t": (21, 28, 51), "w": (255, 111, 0), "d": (178, 70, 0), "s": (0, 200, 83), "S": (105, 240, 174),
       "k": (8, 40, 24), "g": (200, 205, 220), "b": (40, 40, 48)}


def design():
    g = [["_"] * N for _ in range(N)]

    def rect(x0, y0, x1, y1, c):
        for y in range(y0, y1):
            for x in range(x0, x1):
                g[y][x] = c

    rect(0, 0, N, N, "t")                                   # tile
    for (x, y) in ((0, 0), (N - 1, 0), (0, N - 1), (N - 1, N - 1), (1, 0), (0, 1), (N - 2, 0), (N - 1, 1),
                   (1, N - 1), (0, N - 2), (N - 2, N - 1), (N - 1, N - 2)):
        g[y][x] = "_"                                       # rounded corners
    for i in range(7):                                      # antenna
        g[9 - i][16 - i - 1] = "g"
        g[9 - i][16 + i] = "g"
    rect(4, 10, 28, 26, "w")                                # body
    rect(4, 24, 28, 26, "d")
    rect(6, 12, 22, 24, "k")                                # screen bezel
    rect(7, 13, 21, 23, "s")                                # phosphor
    for y in range(13, 23, 2):
        rect(7, y, 21, y + 1, "S")                          # scan lines
    rect(23, 13, 26, 16, "b")                               # knobs
    rect(23, 18, 26, 21, "b")
    rect(7, 26, 10, 28, "b")                                # feet
    rect(22, 26, 25, 28, "b")
    return g


def png(pixels, size):
    """pixels: size x size list of (r,g,b,a)."""
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in pixels)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def render(size):
    g = design()
    out = []
    for y in range(size):
        row = []
        for x in range(size):
            c = PAL[g[y * N // size][x * N // size]]
            row.append((0, 0, 0, 0) if c is None else (*c, 255))
        out.append(row)
    return png(out, size)


def main(dest):
    os.makedirs(dest, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{base}x{base}" + ("@2x" if scale == 2 else "") + ".png"
            with open(os.path.join(dest, name), "wb") as f:
                f.write(render(base * scale))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
