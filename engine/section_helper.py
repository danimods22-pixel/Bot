"""
Port of SectionHelper.java — pencarian CodeRegistration & MetadataRegistration
berbasis analisa data (PlusSearch).

Beda kecil dari versi Java: di Java tiap section di-mmap TERPISAH (MappedByteBuffer
per segmen) karena constraint alignment RandomAccessFile-channel Android. Di Python
kita mmap SATU KALI seluruh file (mmap.mmap 0-based, dibackup page cache OS persis
kayak MappedByteBuffer -- nol copy penuh ke RAM proses), terus tiap section cuma
nyimpen (offset, capacity) sebagai slice ke mmap itu. Hasil & algoritmanya sama
persis, cuma implementasi mmap-nya disederhanakan.
"""
import mmap

FEATURE = b"mscorlib.dll\x00"


class MemSec:
    __slots__ = ("offset", "offsetEnd", "address", "addressEnd", "capacity")

    def __init__(self, sec, whole_mmap, file_len):
        self.offset = sec.offset
        self.offsetEnd = sec.offsetEnd
        self.address = sec.address
        self.addressEnd = sec.addressEnd
        end = min(sec.offsetEnd, file_len)
        self.capacity = max(0, end - sec.offset)
        self._mm = whole_mmap

    def byte_at(self, local_pos):
        return self._mm[self.offset + local_pos]

    def slice(self, local_start, local_end):
        return self._mm[self.offset + local_start:self.offset + local_end]


class SectionHelper:
    DBG = False

    def __init__(self, il2cpp, method_count, type_definitions_count, metadata_usages_count, image_count):
        self.il2cpp = il2cpp
        self.methodCount = method_count
        self.typeDefinitionsCount = type_definitions_count
        self.metadataUsagesCount = metadata_usages_count
        self.imageCount = image_count
        self.pointerInExec = False
        self.ptr = il2cpp.ptr

        # FIX (bug relocation ilang): pastikan semua tulisan (hasil apply_relocations)
        # sudah nyampe ke disk sebelum di-mmap, karena mmap baca langsung dari file di
        # disk, lewatin buffer Python file object.
        il2cpp.bin.flush()
        file_len = il2cpp.bin.length()
        self._mm = mmap.mmap(il2cpp.bin.f.fileno(), 0, access=mmap.ACCESS_READ)

        self.memData = [MemSec(s, self._mm, file_len) for s in il2cpp.dataSections]
        self.memExec = [MemSec(s, self._mm, file_len) for s in il2cpp.execSections]

    def close(self):
        try:
            self._mm.close()
        except Exception:
            pass

    def _ptr_at(self, sec: MemSec, off):
        if self.ptr == 8:
            b = sec.slice(off, off + 8)
            return int.from_bytes(b, "little", signed=False)
        else:
            b = sec.slice(off, off + 4)
            return int.from_bytes(b, "little", signed=False)

    def find_code_registration(self):
        if self.il2cpp.version >= 24.2:
            code_reg = self._find_code_registration_2019(self.memExec)
            if code_reg == 0:
                code_reg = self._find_code_registration_2019(self.memData)
            else:
                self.pointerInExec = True
            return code_reg
        return self._find_code_registration_old()

    def find_metadata_registration(self):
        if self.il2cpp.version < 19:
            return 0
        if self.il2cpp.version >= 27:
            return self._find_metadata_registration_v21()
        return self._find_metadata_registration_old()

    def _find_code_registration_old(self):
        for sec in self.memData:
            end = sec.capacity - self.ptr
            pos = 0
            while pos < end:
                if self._ptr_at(sec, pos) == self.methodCount:
                    try:
                        ptrva = self._ptr_at(sec, pos + self.ptr)
                        pointer = self.il2cpp.map_va_to_offset(ptrva)
                        if self._check_pointer_range_data_ra(pointer):
                            if self.il2cpp.check_ptr_arr_in_range(ptrva, self.methodCount, self.il2cpp.execSections):
                                return pos + sec.address
                    except Exception:
                        pass
                pos += self.ptr
        return 0

    def _find_metadata_registration_old(self):
        for sec in self.memData:
            end = sec.capacity - self.ptr
            pos = 0
            while pos < end:
                if self._ptr_at(sec, pos) == self.typeDefinitionsCount:
                    try:
                        ptrva = self._ptr_at(sec, pos + self.ptr * 3)
                        pointer = self.il2cpp.map_va_to_offset(ptrva)
                        if self._check_pointer_range_data_ra(pointer):
                            if self.il2cpp.check_ptr_arr_in_range(ptrva, int(self.metadataUsagesCount), self.il2cpp.dataSections):
                                return pos - self.ptr * 12 + sec.address
                    except Exception:
                        pass
                pos += self.ptr
        return 0

    def _find_metadata_registration_v21(self):
        for sec in self.memData:
            end = sec.capacity - self.ptr
            pos = 0
            while pos < end:
                if self._ptr_at(sec, pos) == self.typeDefinitionsCount:
                    if self._ptr_at(sec, pos + self.ptr * 2) == self.typeDefinitionsCount:
                        try:
                            ptrva = self._ptr_at(sec, pos + self.ptr * 3)
                            pointer = self.il2cpp.map_va_to_offset(ptrva)
                            if self._check_pointer_range_data_ra(pointer):
                                ranges = self.il2cpp.execSections if self.pointerInExec else self.il2cpp.dataSections
                                if self.il2cpp.check_ptr_arr_in_range(ptrva, self.typeDefinitionsCount, ranges):
                                    return pos - self.ptr * 10 + sec.address
                        except Exception:
                            pass
                pos += self.ptr
        return 0

    def _find_code_registration_2019(self, secs):
        for sec in secs:
            hits = self._search_bytes(sec, FEATURE)
            for index in hits:
                dllva = index + sec.address
                refs = self._find_reference(dllva)
                for refva in refs:
                    refs2 = self._find_reference(refva)
                    for refva2 in refs2:
                        if self.il2cpp.version >= 27:
                            for i in range(self.imageCount - 1, -1, -1):
                                refs3 = self._find_reference(refva2 - i * self.ptr)
                                if not refs3:
                                    continue
                                for refva3 in refs3:
                                    try:
                                        val = self._read_ptr_at_va(refva3 - self.ptr)
                                    except Exception:
                                        continue
                                    if val == self.imageCount:
                                        if self.il2cpp.version >= 35:
                                            return refva3 - self.ptr * 16
                                        if self.il2cpp.version >= 29:
                                            return refva3 - self.ptr * 14
                                        return refva3 - self.ptr * 13
                        else:
                            for i in range(self.imageCount):
                                refs3 = self._find_reference(refva2 - i * self.ptr)
                                if refs3:
                                    return refs3[0] - self.ptr * 13
        return 0

    def _read_ptr_at_va(self, va):
        off = self.il2cpp.map_va_to_offset(va)
        self.il2cpp.bin.seek(off)
        return self.il2cpp._read_ptr()

    def _find_reference(self, addr):
        result = []
        for sec in self.memData:
            end = sec.capacity - self.ptr
            pos = 0
            while pos < end:
                if self._ptr_at(sec, pos) == addr:
                    result.append(pos + sec.address)
                pos += self.ptr
        return result

    def _search_bytes(self, sec: MemSec, pattern: bytes):
        """cari semua kemunculan pattern di section (via mmap slice -> bytes.find berulang,
        jauh lebih cepat daripada loop byte-per-byte manual di Python)."""
        result = []
        data = sec.slice(0, sec.capacity)
        start = 0
        while True:
            idx = data.find(pattern, start)
            if idx < 0:
                break
            result.append(idx)
            start = idx + 1
        return result

    def _check_pointer_range_data_ra(self, pointer):
        for s in self.il2cpp.dataSections:
            if s.offset <= pointer <= s.offsetEnd:
                return True
        return False
