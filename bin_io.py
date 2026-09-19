"""
Port of Bin.java — baca/tulis file biner little-endian.

Di Java versi Android, class ini punya buffer manual 64KB karena I/O langsung
byte-per-byte lambat banget di Android. Di Python, `open(..., "r+b")` bawaan
sudah buffered dengan baik, jadi kita pakai itu langsung + `mmap` (dipakai
SectionHelper) tanpa perlu reimplementasi buffering manual — perilaku dan
urutan baca/tulisnya tetap setara dengan versi Java.
"""
import struct


class Bin:
    def __init__(self, path, mode="r+b"):
        self.path = path
        self.f = open(path, mode)
        self.pos = 0

    def length(self):
        cur = self.f.tell()
        self.f.seek(0, 2)
        n = self.f.tell()
        self.f.seek(cur)
        return n

    def seek(self, p):
        self.pos = p

    def _read(self, n):
        self.f.seek(self.pos)
        data = self.f.read(n)
        if len(data) < n:
            raise EOFError("EOF saat baca %d byte di offset %d" % (n, self.pos))
        self.pos += n
        return data

    def read_u8(self):
        return self._read(1)[0]

    def read_u16(self):
        return struct.unpack("<H", self._read(2))[0]

    def read_u32(self):
        return struct.unpack("<I", self._read(4))[0]

    def read_i32(self):
        return struct.unpack("<i", self._read(4))[0]

    def read_u64(self):
        return struct.unpack("<Q", self._read(8))[0]

    def read_bytes(self, n):
        if n <= 0:
            return b""
        return self._read(n)

    def read_string_to_null(self, offset):
        save = self.pos
        self.f.seek(offset)
        out = bytearray()
        try:
            while len(out) < 1_000_000:
                b = self.f.read(1)
                if not b or b[0] == 0:
                    break
                out.append(b[0])
        except Exception:
            pass
        self.pos = save
        return out.decode("utf-8", errors="replace")

    def write_at(self, offset, data: bytes):
        self.f.seek(offset)
        self.f.write(data)

    def write_u32(self, offset, value):
        self.write_at(offset, struct.pack("<i", value & 0xFFFFFFFF if value >= 0 else value))

    def write_u64(self, offset, value):
        self.write_at(offset, struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))

    def flush(self):
        self.f.flush()

    def get_channel_path(self):
        """Dipakai SectionHelper buat mmap — di Python cukup path + flush dulu."""
        self.flush()
        return self.path

    def close(self):
        self.f.flush()
        self.f.close()
