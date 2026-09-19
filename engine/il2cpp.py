"""
Port of Il2Cpp.java — parser libil2cpp.so (ELF 32/64) + pencarian
CodeRegistration & MetadataRegistration + apply relocation.
Search order: PlusSearch (SectionHelper) -> search24 (ARM32 pattern) -> symbolSearch.
"""
from .bin_io import Bin

PT_LOAD = 1
PT_DYNAMIC = 2
DT_NULL = 0
DT_PLTGOT = 3
DT_HASH = 4
DT_STRTAB = 5
DT_SYMTAB = 6
DT_RELA = 7
DT_RELASZ = 8
DT_STRSZ = 10
DT_REL = 17
DT_RELSZ = 18
DT_GNU_HASH = 0x6FFFFEF5

RELOC_BATCH = 1_000_000


class Phdr:
    def __init__(self):
        self.type = self.offset = self.vaddr = self.paddr = 0
        self.filesz = self.memsz = self.flags = self.align = 0


class Dyn:
    def __init__(self):
        self.tag = self.val = 0


class Sym:
    def __init__(self):
        self.name = self.value = self.size = self.shndx = 0
        self.info = 0


class Sec:
    def __init__(self):
        self.offset = self.offsetEnd = self.address = self.addressEnd = 0


class CodeRegistration:
    def __init__(self):
        self.methodPointersCount = self.methodPointers = 0
        self.reversePInvokeWrapperCount = self.reversePInvokeWrappers = 0
        self.genericMethodPointersCount = self.genericMethodPointers = 0
        self.genericAdjustorThunks = 0
        self.invokerPointersCount = self.invokerPointers = 0
        self.customAttributeCount = self.customAttributeGenerators = 0
        self.unresolvedVirtualCallCount = self.unresolvedVirtualCallPointers = 0
        self.interopDataCount = self.interopData = 0
        self.windowsRuntimeFactoryCount = self.windowsRuntimeFactoryTable = 0
        self.codeGenModulesCount = self.codeGenModules = 0


class MetadataRegistration:
    def __init__(self):
        self.genericClassesCount = self.genericClasses = 0
        self.genericInstsCount = self.genericInsts = 0
        self.genericMethodTableCount = self.genericMethodTable = 0
        self.typesCount = self.types = 0
        self.methodSpecsCount = self.methodSpecs = 0
        self.fieldOffsetsCount = self.fieldOffsets = 0
        self.typeDefinitionsSizesCount = self.typeDefinitionsSizes = 0
        self.metadataUsagesCount = self.metadataUsages = 0


class CodeGenModule:
    def __init__(self):
        self.moduleName = ""
        self.methodPointerCount = self.methodPointers = 0
        self.invokerIndices = 0
        self.rgctxRangesCount = self.rgctxRanges = self.rgctxsCount = self.rgctxs = 0


class Il2CppType:
    def __init__(self):
        self.data = 0
        self.bits = 0
        self.attrs = self.type = 0
        self.numMods = self.byref = self.pinned = self.valuetype = 0

    def init(self, v):
        bits = self.bits & 0xFFFFFFFF
        self.attrs = bits & 0xFFFF
        self.type = (bits >> 16) & 0xFF
        if v >= 27.2:
            self.numMods = (bits >> 24) & 0x1F
            self.byref = (bits >> 29) & 1
            self.pinned = (bits >> 30) & 1
            self.valuetype = (bits >> 31) & 1
        else:
            self.numMods = (bits >> 24) & 0x3F
            self.byref = (bits >> 30) & 1
            self.pinned = (bits >> 31) & 1


class GenericMethodTableEntry:
    def __init__(self):
        self.genericMethodIndex = self.methodIndex = self.invokerIndex = self.adjustorThunk = 0


class MethodSpec:
    def __init__(self):
        self.methodDefinitionIndex = self.classIndexIndex = self.methodIndexIndex = 0

    def key(self):
        return (self.methodDefinitionIndex, self.classIndexIndex, self.methodIndexIndex)


class GenericInst:
    def __init__(self):
        self.typeArgc = 0
        self.typeArgv = 0


def fmt_ver(v):
    return str(int(v)) if v == int(v) else str(v)


class Il2Cpp:
    """log: objek dengan method info(s)/warn(s)/error(s) (boleh None)."""

    def __init__(self, path, log=None):
        self.log = log
        self.bin = Bin(path, "r+b")
        self.is64 = False
        self.ptr = 4
        self.version = 0.0
        self.eMachine = 0

        self.phdrs = []
        self.dynamic = []
        self.execSections = []
        self.dataSections = []

        self.codeReg = None
        self.metaReg = None
        self.types = []
        self.fieldOffsets = []
        self.fieldOffsetsArePointers = False
        self.codeGenModuleMethodPointers = {}
        self.methodPointers = []
        self.genericMethodPointers = []
        self.genericMethodTable = []
        self.methodSpecs = []
        self.genericInsts = []
        self.methodDefMethodSpecs = {}
        self.methodSpecGenericMethodPointers = {}

        self.globalOffsetTable = 0
        self._symbols = []
        self.imageBase = 0
        self.pointerInExec = False

        self._load()

    # ================= ELF loading =================

    def _load(self):
        b = self.bin
        b.seek(0)
        magic = b.read_u32()
        if magic != 0x464C457F:
            raise ValueError("Bukan file ELF (libil2cpp.so tidak valid)")
        b.seek(4)
        ei_class = b.read_u8()
        self.is64 = ei_class == 2
        self.ptr = 8 if self.is64 else 4
        b.seek(18)
        self.eMachine = b.read_u16()

        if self.is64:
            b.seek(32); e_phoff = b.read_u64()
            b.seek(54); e_phnum = b.read_u16()
            b.seek(e_phoff)
            for _ in range(e_phnum):
                p = Phdr()
                p.type = b.read_u32()
                p.flags = b.read_u32()
                p.offset = b.read_u64()
                p.vaddr = b.read_u64()
                p.paddr = b.read_u64()
                p.filesz = b.read_u64()
                p.memsz = b.read_u64()
                p.align = b.read_u64()
                self.phdrs.append(p)
        else:
            b.seek(28); e_phoff = b.read_u32()
            b.seek(44); e_phnum = b.read_u16()
            b.seek(e_phoff)
            for _ in range(e_phnum):
                p = Phdr()
                p.type = b.read_u32()
                p.offset = b.read_u32()
                p.vaddr = b.read_u32()
                p.paddr = b.read_u32()
                p.filesz = b.read_u32()
                p.memsz = b.read_u32()
                p.flags = b.read_u32()
                p.align = b.read_u32()
                self.phdrs.append(p)

        pt_dynamic = next((p for p in self.phdrs if p.type == PT_DYNAMIC), None)
        if pt_dynamic is None:
            raise ValueError("PT_DYNAMIC tidak ditemukan — libil2cpp.so rusak/protected")
        b.seek(pt_dynamic.offset)
        dyn_end = pt_dynamic.offset + pt_dynamic.filesz
        while b.pos < dyn_end:
            tag = self._read_ptr()
            val = self._read_ptr()
            if tag == DT_NULL:
                break
            d = Dyn(); d.tag = tag; d.val = val
            self.dynamic.append(d)
            if tag == DT_PLTGOT:
                self.globalOffsetTable = val

        self._read_symbols()
        if self.log:
            self.log.info("Applying relocations...")
        self._apply_relocations()

        for p in self.phdrs:
            if p.type != PT_LOAD or p.memsz == 0:
                continue
            s = Sec()
            s.offset = p.offset
            s.offsetEnd = p.offset + p.filesz
            s.address = p.vaddr
            s.addressEnd = p.vaddr + p.memsz
            is_x = (p.flags & 1) != 0
            (self.execSections if is_x else self.dataSections).append(s)

    def _read_ptr(self):
        return self.bin.read_u64() if self.is64 else self.bin.read_u32()

    def map_va_to_offset(self, va):
        for p in self.phdrs:
            if p.vaddr <= va < p.vaddr + p.memsz:
                off = va - p.vaddr + p.offset
                if off < 0 or off >= self.bin.length():
                    raise ValueError("VA 0x%x di luar file" % va)
                return off
        raise ValueError("VA 0x%x tidak ada di program headers" % va)

    def _mapv(self, va):
        return self.map_va_to_offset(va)

    def map_rt_va(self, ra):
        for p in self.phdrs:
            if p.offset <= ra < p.offset + p.filesz:
                return ra - p.offset + p.vaddr
        return 0

    def _find_dyn(self, tag):
        for d in self.dynamic:
            if d.tag == tag:
                return d
        return None

    # ================= symbols =================

    def _read_symbols(self):
        b = self.bin
        try:
            symbol_count = 0
            h = self._find_dyn(DT_HASH)
            if h is not None:
                addr = self._mapv(h.val)
                b.seek(addr)
                b.read_u32()  # nbucket
                symbol_count = b.read_u32()
            else:
                h = self._find_dyn(DT_GNU_HASH)
                if h is None:
                    return
                addr = self._mapv(h.val)
                b.seek(addr)
                nbuckets = b.read_u32()
                symoffset = b.read_u32()
                bloom_size = b.read_u32()
                b.read_u32()  # bloom_shift
                buckets_addr = addr + 16 + self.ptr * bloom_size
                last_symbol = 0
                b.seek(buckets_addr)
                for _ in range(nbuckets):
                    v = self._read_ptr()
                    if v > last_symbol:
                        last_symbol = v
                if last_symbol < symoffset:
                    symbol_count = symoffset
                else:
                    chains_base = buckets_addr + self.ptr * nbuckets
                    b.seek(chains_base + (last_symbol - symoffset) * self.ptr)
                    while True:
                        chain = self._read_ptr()
                        last_symbol += 1
                        if chain & 1:
                            break
                    symbol_count = last_symbol

            symtab = self._find_dyn(DT_SYMTAB)
            dynsym_offset = self._mapv(symtab.val)
            b.seek(dynsym_offset)
            for _ in range(symbol_count):
                s = Sym()
                s.name = self._read_ptr()
                if self.is64:
                    s.info = b.read_u8()
                    b.read_u8()  # other
                    s.shndx = b.read_u16()
                    s.value = b.read_u64()
                    s.size = b.read_u64()
                else:
                    s.value = b.read_u32()
                    s.size = b.read_u32()
                    s.info = b.read_u8()
                    b.read_u8()
                    s.shndx = b.read_u16()
                self._symbols.append(s)
        except Exception as e:
            if self.log:
                self.log.warn("Gagal baca symbol table: %s" % e)

    # ================= relocation (batched, FIX OOM) =================

    def _apply_relocations(self):
        b = self.bin
        try:
            if self.is64:
                rela = self._find_dyn(DT_RELA)
                if rela is None:
                    return
                rela_offset = self._mapv(rela.val)
                rela_size = self._find_dyn(DT_RELASZ).val
                n = int(rela_size // 24)
                if self.log:
                    self.log.info("Relocation entries: %d" % n)
                b.seek(rela_offset)
                done = 0
                while done < n:
                    count = min(RELOC_BATCH, n - done)
                    r_offsets = [0] * count
                    r_types = [0] * count
                    r_syms = [0] * count
                    r_addends = [0] * count
                    for i in range(count):
                        r_offsets[i] = b.read_u64()
                        info = b.read_u64()
                        r_addends[i] = b.read_u64()
                        r_types[i] = info & 0xFFFFFFFF
                        r_syms[i] = info >> 32
                    for i in range(count):
                        recognized = True
                        if r_types[i] == 1027 and self.eMachine == 183:      # R_AARCH64_RELATIVE
                            value = r_addends[i]
                        elif r_types[i] == 257 and self.eMachine == 183:     # R_AARCH64_ABS64
                            value = self._sym_value(r_syms[i]) + r_addends[i]
                        elif r_types[i] == 8 and self.eMachine == 62:        # R_X86_64_RELATIVE
                            value = r_addends[i]
                        elif r_types[i] == 1 and self.eMachine == 62:        # R_X86_64_64
                            value = self._sym_value(r_syms[i]) + r_addends[i]
                        else:
                            value = 0
                            recognized = False
                        if recognized:
                            b.write_u64(self._mapv(r_offsets[i]), value)
                    done += count
            else:
                rel = self._find_dyn(DT_REL)
                if rel is None:
                    return
                rel_offset = self._mapv(rel.val)
                rel_size = self._find_dyn(DT_RELSZ).val
                is_x86 = self.eMachine == 3
                n = int(rel_size // 8)
                if self.log:
                    self.log.info("Relocation entries: %d" % n)
                b.seek(rel_offset)
                done = 0
                while done < n:
                    count = min(RELOC_BATCH, n - done)
                    r_offsets = [0] * count
                    r_values = [0] * count
                    r_ok = [False] * count
                    for i in range(count):
                        r_offsets[i] = b.read_u32()
                        info = b.read_u32()
                        typ = info & 0xFF
                        sym = info >> 8
                        if is_x86 and typ == 1:      # R_386_32
                            r_values[i] = self._sym_value(sym); r_ok[i] = True
                        elif not is_x86 and typ == 2:  # R_ARM_ABS32
                            r_values[i] = self._sym_value(sym); r_ok[i] = True
                    for i in range(count):
                        if r_ok[i]:
                            b.write_u32(self._mapv(r_offsets[i]), r_values[i])
                    done += count
        except Exception as e:
            if self.log:
                self.log.warn("Relocation sebagian gagal: %s" % e)

    def _sym_value(self, idx):
        if 0 <= idx < len(self._symbols):
            return self._symbols[idx].value
        return 0

    # ================= Registration search =================

    def find_registrations(self, meta):
        """urutan: PlusSearch -> search24 (ARM32) -> symbolSearch."""
        from .section_helper import SectionHelper
        method_count = self._count_valid_methods(meta)
        if self.log:
            self.log.info("PlusSearch: mencari CodeRegistration & MetadataRegistration via analisa data...")
        sh = SectionHelper(self, method_count, len(meta.typeDefs), meta.metadataUsagesCount, len(meta.imageDefs))
        try:
            code_reg_va = sh.find_code_registration()
            meta_reg_va = sh.find_metadata_registration()
        finally:
            sh.close()
        if self.auto_plus_init(code_reg_va, meta_reg_va):
            return True

        if meta.version >= 24:
            if self.log:
                self.log.info("PlusSearch gagal, coba pattern search (il2cpp init)...")
            if self.search24():
                return True

        if self.log:
            self.log.info("Cari symbol export g_CodeRegistration / g_MetadataRegistration...")
        if self.symbol_search():
            return True

        if self.log:
            self.log.error("Registration TIDAK ditemukan — kemungkinan libil2cpp.so di-protect/strip aneh")
        return False

    def _count_valid_methods(self, meta):
        if meta.version >= 24.2:
            return len(meta.methodDefs)
        return sum(1 for m in meta.methodDefs if m.methodIndex >= 0)

    def auto_plus_init(self, code_reg_va, meta_reg_va):
        limit = 0x50000
        if code_reg_va != 0 and self.version >= 24.2:
            self.codeReg = self._read_code_registration(code_reg_va)
            if self.version == 31:
                if self.codeReg.genericMethodPointersCount > limit:
                    code_reg_va -= self.ptr * 2
                else:
                    self.version = 29
                    if self.log:
                        self.log.info("il2cpp version dikoreksi ke: %s" % fmt_ver(self.version))
            if self.version == 29:
                self.codeReg = self._read_code_registration(code_reg_va)
                if self.codeReg.genericMethodPointersCount > limit:
                    self.version = 29.1
                    code_reg_va -= self.ptr * 2
                    if self.log:
                        self.log.info("il2cpp version dikoreksi ke: %s" % fmt_ver(self.version))
            if self.version == 27:
                self.codeReg = self._read_code_registration(code_reg_va)
                if self.codeReg.reversePInvokeWrapperCount > limit:
                    self.version = 27.1
                    code_reg_va -= self.ptr
                    if self.log:
                        self.log.info("il2cpp version dikoreksi ke: %s" % fmt_ver(self.version))
            if self.version == 24.4:
                code_reg_va -= self.ptr * 2
                self.codeReg = self._read_code_registration(code_reg_va)
                if self.codeReg.reversePInvokeWrapperCount > limit:
                    self.version = 24.5
                    code_reg_va -= self.ptr
                    if self.log:
                        self.log.info("il2cpp version dikoreksi ke: %s" % fmt_ver(self.version))
            if self.version == 24.2:
                self.codeReg = self._read_code_registration(code_reg_va)
                if self.codeReg.interopDataCount == 0:
                    self.version = 24.3
                    code_reg_va -= self.ptr * 2
                    if self.log:
                        self.log.info("il2cpp version dikoreksi ke: %s" % fmt_ver(self.version))
        if self.log:
            self.log.info("CodeRegistration : 0x%x" % code_reg_va)
            self.log.info("MetadataRegistration : 0x%x" % meta_reg_va)
        if code_reg_va != 0 and meta_reg_va != 0:
            self.init(code_reg_va, meta_reg_va)
            return True
        return False

    def init(self, code_reg_va, meta_reg_va):
        self.codeReg = self._read_code_registration(code_reg_va)
        limit = 0x50000
        if self.version == 27 and self.codeReg.invokerPointersCount > limit:
            self.version = 27.1
            if self.log:
                self.log.info("il2cpp version dikoreksi ke: %s" % fmt_ver(self.version))
            self.codeReg = self._read_code_registration(code_reg_va)
        self.metaReg = self._read_metadata_registration(meta_reg_va)

        self.genericMethodPointers = self.read_ptr_arr(self.codeReg.genericMethodPointers, int(self.codeReg.genericMethodPointersCount))
        if self.version < 24.2:
            self.methodPointers = self.read_ptr_arr(self.codeReg.methodPointers, int(self.codeReg.methodPointersCount))

        self.genericMethodTable = self._read_generic_method_table(self.metaReg.genericMethodTable, int(self.metaReg.genericMethodTableCount))
        self.methodSpecs = self._read_method_specs(self.metaReg.methodSpecs, int(self.metaReg.methodSpecsCount))
        self.genericInsts = self._read_generic_insts(self.metaReg.genericInsts, int(self.metaReg.genericInstsCount))
        for t in self.genericMethodTable:
            if t.genericMethodIndex < 0 or t.genericMethodIndex >= len(self.methodSpecs):
                continue
            spec = self.methodSpecs[t.genericMethodIndex]
            self.methodDefMethodSpecs.setdefault(spec.methodDefinitionIndex, []).append(spec)
            if 0 <= t.methodIndex < len(self.genericMethodPointers):
                self.methodSpecGenericMethodPointers[spec.key()] = self.genericMethodPointers[t.methodIndex]

        self.fieldOffsetsArePointers = self.version > 21
        if self.fieldOffsetsArePointers:
            self.fieldOffsets = self.read_ptr_arr(self.metaReg.fieldOffsets, int(self.metaReg.fieldOffsetsCount))
        else:
            off = self._mapv(self.metaReg.fieldOffsets)
            self.bin.seek(off)
            self.fieldOffsets = [self.bin.read_u32() for _ in range(int(self.metaReg.fieldOffsetsCount))]

        type_ptrs = self.read_ptr_arr(self.metaReg.types, int(self.metaReg.typesCount))
        self.types = []
        for tp in type_ptrs:
            t = Il2CppType()
            off = self._mapv(tp)
            self.bin.seek(off)
            t.data = self._read_ptr()
            t.bits = self.bin.read_u32()
            t.init(self.version)
            self.types.append(t)

        if self.version >= 24.2:
            module_ptrs = self.read_ptr_arr(self.codeReg.codeGenModules, int(self.codeReg.codeGenModulesCount))
            for mp in module_ptrs:
                off = self._mapv(mp)
                self.bin.seek(off)
                m = CodeGenModule()
                module_name_ptr = self._read_ptr()
                m.methodPointerCount = self._read_ptr()
                m.methodPointers = self._read_ptr()
                if self.version == 24.5 or self.version >= 27.1:
                    self._read_ptr(); self._read_ptr()  # adjustor
                m.invokerIndices = self._read_ptr()
                self._read_ptr(); self._read_ptr()  # reversePInvoke
                m.rgctxRangesCount = self._read_ptr(); m.rgctxRanges = self._read_ptr()
                m.rgctxsCount = self._read_ptr(); m.rgctxs = self._read_ptr()
                self._read_ptr()  # debuggerMetadata
                if 27 <= self.version <= 27.2:
                    self._read_ptr()  # customAttributeCacheGenerator
                if self.version >= 27:
                    self._read_ptr()  # moduleInitializer
                    self._read_ptr()  # staticConstructorTypeIndices
                    self._read_ptr()  # metadataRegistration
                    self._read_ptr()  # codeRegistration
                m.moduleName = self.bin.read_string_to_null(self._mapv(module_name_ptr))
                try:
                    ptrs = self.read_ptr_arr(m.methodPointers, int(m.methodPointerCount))
                except Exception:
                    ptrs = [0] * int(m.methodPointerCount)
                self.codeGenModuleMethodPointers[m.moduleName] = ptrs

    def read_ptr_arr(self, va, count):
        if va == 0 or count <= 0 or count > 5_000_000:
            return []
        off = self._mapv(va)
        self.bin.seek(off)
        return [self._read_ptr() for _ in range(count)]

    def check_ptr_arr_in_range(self, va, count, ranges):
        """FIX OOM (.so gede): cek jangkauan pointer SATU-SATU langsung dari file,
        berhenti begitu ketemu 1 yang di luar jangkauan — hindari alokasi array
        penuh buat kandidat false-positive (lihat catatan versi Java)."""
        if va == 0 or count <= 0 or count > 5_000_000:
            return False
        off = self._mapv(va)
        self.bin.seek(off)
        for _ in range(count):
            p = self._read_ptr()
            ok = any(s.address <= p <= s.addressEnd for s in ranges)
            if not ok:
                return False
        return True

    def _read_generic_method_table(self, va, count):
        arr = [GenericMethodTableEntry() for _ in range(max(count, 0))]
        if va == 0 or count <= 0:
            return arr
        off = self._mapv(va)
        self.bin.seek(off)
        for e in arr:
            e.genericMethodIndex = self.bin.read_i32()
            e.methodIndex = self.bin.read_i32()
            e.invokerIndex = self.bin.read_i32()
            if self.version == 24.5 or self.version >= 27.1:
                e.adjustorThunk = self.bin.read_i32()
        return arr

    def _read_method_specs(self, va, count):
        arr = [MethodSpec() for _ in range(max(count, 0))]
        if va == 0 or count <= 0:
            return arr
        off = self._mapv(va)
        self.bin.seek(off)
        for m in arr:
            m.methodDefinitionIndex = self.bin.read_i32()
            m.classIndexIndex = self.bin.read_i32()
            m.methodIndexIndex = self.bin.read_i32()
        return arr

    def _read_generic_insts(self, va, count):
        if count > 0x100000:
            count = 0
        arr = [GenericInst() for _ in range(max(count, 0))]
        if va == 0 or count <= 0:
            return arr
        inst_pointers = self.read_ptr_arr(va, count)
        for i in range(count):
            g = arr[i]
            p = inst_pointers[i]
            if p != 0:
                off = self._mapv(p)
                self.bin.seek(off)
                g.typeArgc = self.bin.read_i32()
                if self.is64:
                    self.bin.read_u32()  # padding alignment ke 8 byte
                g.typeArgv = self._read_ptr()
        return arr

    def _read_code_registration(self, va):
        c = CodeRegistration()
        off = self._mapv(va)
        self.bin.seek(off)
        v = self.version
        if v <= 24.1:
            c.methodPointersCount = self._read_ptr(); c.methodPointers = self._read_ptr()
        if v <= 21:
            self._read_ptr(); self._read_ptr()
        if v >= 22:
            c.reversePInvokeWrapperCount = self._read_ptr(); c.reversePInvokeWrappers = self._read_ptr()
        if v <= 22:
            self._read_ptr(); self._read_ptr(); self._read_ptr(); self._read_ptr()
        if 21 <= v <= 22:
            self._read_ptr(); self._read_ptr()
        c.genericMethodPointersCount = self._read_ptr(); c.genericMethodPointers = self._read_ptr()
        if v == 24.5 or v >= 27.1:
            c.genericAdjustorThunks = self._read_ptr()
        c.invokerPointersCount = self._read_ptr(); c.invokerPointers = self._read_ptr()
        if v <= 24.5:
            c.customAttributeCount = self._read_ptr(); c.customAttributeGenerators = self._read_ptr()
        if 21 <= v <= 22:
            self._read_ptr(); self._read_ptr()
        if v >= 22:
            c.unresolvedVirtualCallCount = self._read_ptr(); c.unresolvedVirtualCallPointers = self._read_ptr()
        if v >= 29.1:
            self._read_ptr(); self._read_ptr()
        if v >= 23:
            c.interopDataCount = self._read_ptr(); c.interopData = self._read_ptr()
        if v >= 24.3:
            c.windowsRuntimeFactoryCount = self._read_ptr(); c.windowsRuntimeFactoryTable = self._read_ptr()
        if v >= 24.2:
            c.codeGenModulesCount = self._read_ptr(); c.codeGenModules = self._read_ptr()
        return c

    def _read_metadata_registration(self, va):
        m = MetadataRegistration()
        off = self._mapv(va)
        self.bin.seek(off)
        m.genericClassesCount = self._read_ptr(); m.genericClasses = self._read_ptr()
        m.genericInstsCount = self._read_ptr(); m.genericInsts = self._read_ptr()
        m.genericMethodTableCount = self._read_ptr(); m.genericMethodTable = self._read_ptr()
        m.typesCount = self._read_ptr(); m.types = self._read_ptr()
        m.methodSpecsCount = self._read_ptr(); m.methodSpecs = self._read_ptr()
        if self.version <= 16:
            self._read_ptr(); self._read_ptr()
        m.fieldOffsetsCount = self._read_ptr(); m.fieldOffsets = self._read_ptr()
        m.typeDefinitionsSizesCount = self._read_ptr(); m.typeDefinitionsSizes = self._read_ptr()
        if self.version >= 19:
            m.metadataUsagesCount = self._read_ptr(); m.metadataUsages = self._read_ptr()
        return m

    # ================= pattern search ARM32 + symbol search =================

    def search24(self):
        """pola LDR R1 / ADD R0 / ADD R2 (Thumb, il2cpp_init) — hanya ELF32."""
        if self.is64:
            return False
        for sec in self.execSections:
            size = sec.offsetEnd - sec.offset
            buff = self.read_chunk(sec.offset, size)
            if buff is None:
                continue
            hits = []
            limit = len(buff) - 12
            for i in range(limit):
                if (buff[i + 1] == 0x10 and buff[i + 3] == 0xE7
                        and buff[i + 5] == 0x00 and buff[i + 7] == 0xE0
                        and buff[i + 9] == 0x20 and buff[i + 11] == 0xE0):
                    hits.append(i)
            if len(hits) == 1:
                result = hits[0]
                b = self.bin
                b.seek(sec.offset + result + 0x14)
                code_reg = (b.read_u32() + result + 0xC + self.imageBase) & 0xFFFFFFFF
                b.seek(sec.offset + result + 0x10)
                p = (b.read_u32() + result + 0x8) & 0xFFFFFFFF
                b.seek(self._mapv(p + self.imageBase))
                meta_reg = b.read_u32()
                if self.log:
                    self.log.info("CodeRegistration : 0x%x" % code_reg)
                    self.log.info("MetadataRegistration : 0x%x" % meta_reg)
                self.init(code_reg, meta_reg)
                return True
        return False

    def symbol_search(self):
        code_reg = meta_reg = 0
        if not self._symbols:
            return False
        strtab = self._find_dyn(DT_STRTAB)
        dynstr_offset = self._mapv(strtab.val)
        for s in self._symbols:
            name = self.bin.read_string_to_null(dynstr_offset + s.name)
            if name == "g_CodeRegistration":
                code_reg = s.value
            elif name == "g_MetadataRegistration":
                meta_reg = s.value
        if code_reg > 0 and meta_reg > 0:
            if self.log:
                self.log.info("Symbol terdeteksi!")
                self.log.info("CodeRegistration : 0x%x" % code_reg)
                self.log.info("MetadataRegistration : 0x%x" % meta_reg)
            self.init(code_reg, meta_reg)
            return True
        return False

    def read_chunk(self, offset, size):
        if size <= 0 or offset < 0 or offset + size > self.bin.length():
            return None
        self.bin.seek(offset)
        return self.bin.read_bytes(int(size))

    # ================= helper buat dump =================

    def get_method_pointer(self, image_name, method_def):
        if self.version >= 24.2:
            ptrs = self.codeGenModuleMethodPointers.get(image_name)
            if ptrs is None:
                return 0
            idx = method_def.token & 0x00FFFFFF
            if idx - 1 < 0 or idx - 1 >= len(ptrs):
                return 0
            return ptrs[idx - 1]
        else:
            if 0 <= method_def.methodIndex < len(self.methodPointers):
                return self.methodPointers[method_def.methodIndex]
        return 0

    def get_field_offset_from_index(self, type_index, field_index_in_type, field_index, is_value_type, is_static):
        offset = -1
        if self.fieldOffsetsArePointers:
            ptrv = self.fieldOffsets[type_index]
            if ptrv > 0:
                self.bin.seek(self._mapv(ptrv) + 4 * field_index_in_type)
                offset = self.bin.read_i32()
        else:
            offset = int(self.fieldOffsets[field_index])
        if offset > 0:
            if is_value_type and not is_static:
                offset -= 8 if self.ptr == 4 else 16
        return offset

    def get_rva(self, pointer):
        return pointer

    def close(self):
        self.bin.close()
