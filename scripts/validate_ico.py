"""Validate build/icon.ico: confirm multi-size entries exist and each PNG is not solid/black.

Pure-stdlib ICO reader (PNG-in-ICO container). Decodes each embedded PNG's IHDR
for dimensions, and for the small sizes (<=48px) fully decodes to check pixel
variance (guards against a broken render that produced a solid-color square).
"""
import struct
import zlib
import sys

ICO = r"D:\MoRay\electron-app\build\icon.ico"


def decode_png(data: bytes):
    """Return (w, h, unique_rgb_count, all_solid) for a PNG. Minimal stdlib decoder."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos = 8
    width = height = None
    idat = b""
    bit_depth = color_type = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if width is None:
        raise ValueError("no IHDR")
    if bit_depth != 8 or color_type not in (2, 6):
        return width, height, None, None  # paletted/gray: skip pixel check
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * channels
    # Undo PNG filtering (Sub/Up/Average/Paeth) row by row.
    prev = bytearray(stride)
    unfiltered = bytearray()
    p = 0
    for _y in range(height):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ftype == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        unfiltered += line
        prev = line
    # Collect unique opaque RGB values.
    uniq = set()
    for i in range(0, len(unfiltered), channels):
        r, g, b = unfiltered[i], unfiltered[i + 1], unfiltered[i + 2]
        alpha = unfiltered[i + 3] if channels == 4 else 255
        if alpha > 16:
            uniq.add((r, g, b))
    return width, height, len(uniq), len(uniq) <= 2


def main():
    raw = open(ICO, "rb").read()
    reserved, itype, count = struct.unpack("<HHH", raw[:6])
    print(f"ICO header: reserved={reserved} type={itype} entries={count} bytes={len(raw)}")
    if itype != 1:
        print("  !! type is not 1 (cursor)")
    ok = True
    for i in range(count):
        w, h, _, _, planes, bpp, size, off = struct.unpack("<BBBBHHII", raw[6 + 16 * i:22 + 16 * i])
        blob = raw[off:off + size]
        magic = blob[:4]
        dim = f"{w or 256}x{h or 256}"
        if magic == b"\x89PNG":
            try:
                pw, ph, uniq, solid = decode_png(blob)
                flag = ""
                if uniq is not None:
                    if solid:
                        flag = "  !! SUSPECT: near-solid color"
                        ok = False
                    else:
                        flag = f"  ({uniq} distinct colors - OK)"
                print(f"  [{i}] {dim} {bpp}bpp PNG {size}B  decoded {pw}x{ph}{flag}")
            except Exception as e:  # noqa: BLE001
                print(f"  [{i}] {dim} PNG {size}B  !! decode failed: {e}")
                ok = False
        else:
            print(f"  [{i}] {dim} {bpp}bpp {size}B  (raw/bmp entry, magic={magic!r})")
    print("VERDICT:", "ICO looks healthy" if ok else "ICO has problems - repack needed")


if __name__ == "__main__":
    sys.exit(0 if main() is None else 0)
