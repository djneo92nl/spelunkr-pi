#!/usr/bin/env python3
"""Convert WOFF2 fonts to plain TrueType (libass, which draws mpv's overlays, cannot read WOFF2).

    python3 tools/woff2_to_ttf.py star4000.woff2 [more.woff2 ...] --out fonts/

Standard library only, plus `node` for the Brotli decompression (Node has it built in; Python has not). Handles what the
WOFF2 spec calls the glyf/loca and hmtx transforms, so it works on ordinary WOFF2 files, not just the pixel fonts it was
written for. Outlines come out uncompressed (every coordinate as a 16-bit delta): bigger, but trivially valid.
"""
import argparse
import os
import struct
import subprocess
import sys

KNOWN_TAGS = ["cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "cvt ", "fpgm", "glyf", "loca", "prep", "CFF ",
              "VORG", "EBDT", "EBLC", "gasp", "hdmx", "kern", "LTSH", "PCLT", "VDMX", "vhea", "vmtx", "BASE", "GDEF", "GPOS",
              "GSUB", "EBSC", "JSTF", "MATH", "CBDT", "CBLC", "COLR", "CPAL", "SVG ", "sbix", "acnt", "avar", "bdat", "bloc",
              "bsln", "cvar", "fdsc", "feat", "fmtx", "fvar", "gvar", "hsty", "just", "lcar", "mort", "morx", "opbd", "prop",
              "trak", "Zapf", "Silf", "Glat", "Gloc", "Feat", "Sill"]


def brotli_decompress(data):
    js = "const z=require('zlib');const c=[];process.stdin.on('data',d=>c.push(d)).on('end',()=>process.stdout.write(z.brotliDecompressSync(Buffer.concat(c))))"
    p = subprocess.run(["node", "-e", js], input=data, capture_output=True)
    if p.returncode:
        raise SystemExit("brotli decompression failed: " + p.stderr.decode(errors="replace")[:300])
    return p.stdout


class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self):
        v = struct.unpack_from(">H", self.d, self.p)[0]
        self.p += 2
        return v

    def i16(self):
        v = struct.unpack_from(">h", self.d, self.p)[0]
        self.p += 2
        return v

    def u32(self):
        v = struct.unpack_from(">I", self.d, self.p)[0]
        self.p += 4
        return v

    def raw(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def base128(self):
        v = 0
        for _ in range(5):
            b = self.u8()
            v = (v << 7) | (b & 0x7F)
            if not b & 0x80:
                return v
        raise ValueError("bad UIntBase128")

    def u255(self):
        b = self.u8()
        if b == 253:
            return self.u16()
        if b == 255:
            return 253 + self.u8()
        if b == 254:
            return 506 + self.u8()
        return b


def _sign(flag, v):
    return v if flag & 1 else -v


def _triplet(f, r):
    """(dx, dy, on_curve) for one point from the glyph stream, per the WOFF2 triplet encoding."""
    on = not (f >> 7)
    f &= 0x7F
    if f < 10:
        return 0, _sign(f, ((f & 14) << 7) + r.u8()), on
    if f < 20:
        return _sign(f, (((f - 10) & 14) << 7) + r.u8()), 0, on
    if f < 84:
        b = r.u8()
        return (_sign(f, 1 + ((f - 20) & 0x30) + (b >> 4)), _sign(f >> 1, 1 + (((f - 20) & 0x0C) << 2) + (b & 15)), on)
    if f < 120:
        b0, b1 = r.u8(), r.u8()
        return (_sign(f, 1 + (((f - 84) // 12) << 8) + b0), _sign(f >> 1, 1 + ((((f - 84) % 12) >> 2) << 8) + b1), on)
    if f < 124:
        b0, b1, b2 = r.u8(), r.u8(), r.u8()
        return _sign(f, (b0 << 4) + (b1 >> 4)), _sign(f >> 1, ((b1 & 15) << 8) + b2), on
    b0, b1, b2, b3 = r.u8(), r.u8(), r.u8(), r.u8()
    return _sign(f, (b0 << 8) + b1), _sign(f >> 1, (b2 << 8) + b3), on


def rebuild_glyf(blob):
    """(glyf table, loca table in long format, [xMin per glyph]) from a transformed glyf table."""
    r = Reader(blob)
    r.u16()                                              # reserved
    option_flags = r.u16()
    num_glyphs = r.u16()
    r.u16()                                              # index format of the original loca: we always write the long one
    sizes = [r.u32() for _ in range(7)]
    streams, pos = [], r.p
    for s in sizes:
        streams.append(Reader(blob, pos))
        pos += s
    n_contour, n_points, flags, glyph, composite, bbox, instr = streams
    bbox_bitmap = bbox.raw(((num_glyphs + 31) >> 5) << 2)
    glyf, loca, xmins = bytearray(), [], []
    for g in range(num_glyphs):
        loca.append(len(glyf))
        nc = n_contour.i16()
        has_bbox = bool(bbox_bitmap[g >> 3] & (0x80 >> (g & 7)))
        if nc == 0:
            xmins.append(0)
            continue
        if nc > 0:
            ends, total = [], 0
            for _ in range(nc):
                total += n_points.u255()
                ends.append(total - 1)
            pts, x, y = [], 0, 0
            fl = flags.raw(total)
            for f in fl:
                dx, dy, on = _triplet(f, glyph)
                x, y = x + dx, y + dy
                pts.append((x, y, on))
            ilen = glyph.u255()
            code = instr.raw(ilen)
            if has_bbox:
                box = struct.unpack(">4h", bbox.raw(8))
            else:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                box = (min(xs), min(ys), max(xs), max(ys)) if pts else (0, 0, 0, 0)
            out = bytearray(struct.pack(">h4h", nc, *box))
            out += struct.pack(f">{nc}H", *ends) + struct.pack(">H", ilen) + code
            out += bytes(1 if p[2] else 0 for p in pts)                 # flags: on-curve bit only, coordinates are 16-bit
            px = py = 0
            for p in pts:
                out += struct.pack(">h", p[0] - px)
                px = p[0]
            for p in pts:
                out += struct.pack(">h", p[1] - py)
                py = p[1]
            xmins.append(box[0])
        else:                                                   # composite: copy the component records, bbox is explicit
            start = composite.p
            more = True
            have_instr = False
            while more:
                fl = composite.u16()
                composite.u16()
                composite.raw(4 if fl & 0x0001 else 2)
                composite.raw(8 if fl & 0x0080 else 4 if fl & 0x0040 else 2 if fl & 0x0008 else 0)
                more = bool(fl & 0x0020)
                have_instr = have_instr or bool(fl & 0x0100)
            comp = composite.d[start:composite.p]
            box = struct.unpack(">4h", bbox.raw(8))
            out = bytearray(struct.pack(">h4h", -1, *box)) + comp
            if have_instr:
                ilen = glyph.u255()
                out += struct.pack(">H", ilen) + instr.raw(ilen)
            xmins.append(box[0])
        out += b"\0" * (-len(out) % 4)
        glyf += out
    loca.append(len(glyf))
    return bytes(glyf), struct.pack(f">{len(loca)}I", *loca), xmins


def rebuild_hmtx(blob, num_hmetrics, num_glyphs, xmins):
    r = Reader(blob)
    flags = r.u8()
    adv = [r.u16() for _ in range(num_hmetrics)]
    lsb = [r.i16() for _ in range(num_hmetrics)] if not flags & 1 else xmins[:num_hmetrics]
    extra = [r.i16() for _ in range(num_glyphs - num_hmetrics)] if not flags & 2 else xmins[num_hmetrics:]
    out = b"".join(struct.pack(">Hh", a, l) for a, l in zip(adv, lsb)) + b"".join(struct.pack(">h", e) for e in extra)
    return out


def checksum(data):
    data = data + b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(data) // 4}I", data)) & 0xFFFFFFFF


def convert(woff2):
    r = Reader(woff2)
    if r.raw(4) != b"wOF2":
        raise SystemExit("not a WOFF2 file")
    flavor = r.u32()
    r.u32()
    num_tables = r.u16()
    r.u16()
    r.u32()
    comp_size = r.u32()
    r.raw(24)
    entries = []
    for _ in range(num_tables):
        fl = r.u8()
        tag = KNOWN_TAGS[fl & 0x3F] if (fl & 0x3F) != 63 else r.raw(4).decode("latin1")
        version = fl >> 6
        orig = r.base128()
        transformed = version != 3 if tag in ("glyf", "loca") else version != 0
        entries.append((tag, orig, r.base128() if transformed else orig, transformed))
    data = brotli_decompress(r.raw(comp_size))
    tables, pos = {}, 0
    for tag, orig, tlen, transformed in entries:
        tables[tag] = (data[pos:pos + tlen], transformed)
        pos += tlen
    out = {t: b for t, (b, tr) in tables.items() if not tr}
    xmins = None
    if "glyf" in tables and tables["glyf"][1]:
        out["glyf"], out["loca"], xmins = rebuild_glyf(tables["glyf"][0])
        head = bytearray(out["head"])
        struct.pack_into(">h", head, 50, 1)                                  # indexToLocFormat: long offsets
        out["head"] = bytes(head)
    if "hmtx" in tables and tables["hmtx"][1]:
        num_hm = struct.unpack_from(">H", out["hhea"], 34)[0]
        num_gl = struct.unpack_from(">H", out["maxp"], 4)[0]
        out["hmtx"] = rebuild_hmtx(tables["hmtx"][0], num_hm, num_gl, xmins or [0] * num_gl)
    tags = sorted(out)
    n = len(tags)
    entry_sel = n.bit_length() - 1
    search = (1 << entry_sel) * 16
    sfnt = bytearray(struct.pack(">IHHHH", flavor, n, search, entry_sel, n * 16 - search))
    offset = 12 + 16 * n
    body = bytearray()
    for t in tags:
        tbl = out[t]
        if t == "head":
            tbl = tbl[:8] + b"\0\0\0\0" + tbl[12:]
        sfnt += struct.pack(">4sIII", t.encode("latin1"), checksum(tbl), offset + len(body), len(tbl))
        body += tbl + b"\0" * (-len(tbl) % 4)
    font = bytearray(sfnt + body)
    if "head" in out:                                                        # whole-file checksum adjustment
        adj = (0xB1B0AFBA - checksum(bytes(font))) & 0xFFFFFFFF
        head_off = next(struct.unpack_from(">I", font, 12 + 16 * i + 8)[0] for i, t in enumerate(tags) if t == "head")
        struct.pack_into(">I", font, head_off + 8, adj)
    return bytes(font)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for f in a.files:
        with open(f, "rb") as fh:
            ttf = convert(fh.read())
        dest = os.path.join(a.out, os.path.splitext(os.path.basename(f))[0] + ".ttf")
        with open(dest, "wb") as fh:
            fh.write(ttf)
        print(f"{f} -> {dest} ({len(ttf)} bytes)")


if __name__ == "__main__":
    sys.exit(main())
