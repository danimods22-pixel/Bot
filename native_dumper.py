"""
Port of NativeEngineDumper.java — dumper untuk native library NON-Unity
(Cocos2d-x, Unreal Engine, atau native lain). Read-only, murni analisa statis
(exported dynamic symbols + readable strings + deteksi engine), sama seperti
`readelf`/`nm`/`strings`.
"""
import os
import re
from .bin_io import Bin
from .demangle_util import demangle

PT_DYNAMIC = 2
DT_NULL = 0
DT_HASH = 4
DT_STRTAB = 5
DT_SYMTAB = 6
DT_GNU_HASH = 0x6FFFFEF5
STT_FUNC = 2
STT_GNU_IFUNC = 10

MIN_STRING_LEN = 8
MAX_STRINGS = 4000
MAX_EXPORTED = 300000

COCOS_MARKERS = ["cocos2d", "CCDirector", "CCLabel", "CCSprite", "CCLayer", "cocos2dcpp",
                 "AppDelegate::applicationDidFinishLaunching", "CCFileUtils"]
UE4_MARKERS = ["UE4Game", "UnrealEngine", "FUnrealEngine", "UE4Editor", "GEngineLoop",
               "FEngineLoop", "UnrealBuildTool", "libUE4", "UE4+Release", "UE5+Release",
               "FGenericPlatformMisc"]
UNITY_MARKERS = ["UnityPlayer", "il2cpp", "libunity", "UnityEngine"]

COCOS_VERSION_RE = re.compile(r"cocos2d-x\s*[0-9]+\.[0-9]+(?:\.[0-9]+)?", re.IGNORECASE)
UE_VERSION_RE = re.compile(r"UE[45][+-][A-Za-z]*[- ]?[0-9]+\.[0-9]+(?:\.[0-9]+)?", re.IGNORECASE)


def _is_cpp_mangled(name):
    return bool(name) and name.startswith("_Z")


def _is_runtime_symbol(name):
    if not _is_cpp_mangled(name):
        return False
    return (name.startswith("_ZSt") or name.startswith("_ZNSt") or name.startswith("_ZNKSt")
            or name.startswith("_ZN9__gnu_cxx") or name.startswith("_ZN10__cxxabiv1"))


def _is_runtime_class(class_name):
    if class_name.startswith("std::__ndk1::"):
        rest = class_name[len("std::__ndk1::"):]
        return rest.startswith("__") or rest.startswith("allocator") or rest.startswith("char_traits")
    return (class_name.startswith("std::") or class_name.startswith("__cxxabiv1::")
            or class_name.startswith("__gnu_cxx::"))


def _is_linker_noise(name):
    return (name.startswith("__do_global_") or name == "__TMC_END__" or name == "_init"
            or name == "_fini" or name == "frame_dummy" or name == "deregister_tm_clones"
            or name == "register_tm_clones")


def _is_standalone_method(demangled):
    params = demangled.get_parameters()
    return "::" in params or "std__" in params or "basic_string" in params


def _is_bridge_or_library_artifact(name):
    if not name:
        return False
    if "objc_" in name.lower():
        return True
    if name.startswith("Java_"):
        return True
    if (name.startswith("_ZTV") or name.startswith("_ZTI") or name.startswith("_ZTS")
            or name.startswith("_ZTT") or name.startswith("_ZTh") or name.startswith("_ZTc")
            or name.startswith("_ZTv")):
        return True
    if name.startswith("_ZZ") or name.startswith("_ZGV"):
        return True
    if "__ndk1" in name or "google8protobuf" in name:
        return True
    if name.startswith("scc_info_"):
        return True
    return False


class ExportedSymbol:
    __slots__ = ("name", "address", "offset")

    def __init__(self, name, address, offset):
        self.name = name
        self.address = address
        self.offset = offset


def _offset_or_address(s):
    return s.offset if s.offset is not None and s.offset >= 0 else s.address


def _hex_sym(s):
    return "0x%x" % _offset_or_address(s)


class _Phdr:
    __slots__ = ("type", "offset", "vaddr", "filesz", "memsz", "flags")


class _Dyn:
    __slots__ = ("tag", "val")


class _ClassBlock:
    def __init__(self, name):
        self.name = name
        self.methods = []
        self._keys = set()

    def add(self, method):
        key = method.key()
        if key not in self._keys:
            self._keys.add(key)
            self.methods.append(method)


class _MethodEntry:
    __slots__ = ("name", "parameters", "standalone", "symbol")

    def __init__(self, name, parameters, standalone, symbol):
        self.name = name
        self.parameters = parameters
        self.standalone = standalone
        self.symbol = symbol

    def key(self):
        return "%s\x00%s\x00%s" % (_hex_sym(self.symbol), self.name, self.parameters)


def _fmt_size(n):
    if n > 1048576:
        return "%.1f MB" % (n / 1048576.0)
    if n > 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%d B" % n


class NativeEngineDumper:
    """forced_engine_name: None untuk auto-detect (mode AUTO), atau "Cocos2d-x"/"Unreal Engine"."""

    def __init__(self, path, forced_engine_name=None, log=None):
        self.path = path
        self.forced_engine_name = forced_engine_name
        self.log = log
        self.bin = Bin(path, "rb")

        self.is64 = False
        self.ptr = 4
        self.e_machine = 0
        self.entry_point = 0
        self.phdrs = []
        self.dynamic = []
        self._found_strings = []  # list[(offset, text)]

        self.exported_symbols = []
        self.marker_hits = []
        self.engine_name = "Unknown Native Engine"
        self.detected_version = None
        self.truncated_exports = False
        self.truncated_strings = False
        self.reconstructed_class_count = 0
        self.other_symbol_count = 0

        self._parse_elf()
        self._read_exported_symbols()

    def _log(self, s):
        if self.log:
            self.log.info(s)

    def is64bit(self):
        return self.is64

    def machine_name(self):
        return {40: "ARM (32-bit)", 183: "AArch64 (ARM64)", 3: "x86", 62: "x86-64"}.get(
            self.e_machine, "Unknown (0x%x)" % self.e_machine)

    def get_exported_count(self):
        return len(self.exported_symbols)

    def get_string_count(self):
        return len(self._found_strings)

    def get_marker_count(self):
        return len(self.marker_hits)

    def _read_ptr(self):
        return self.bin.read_u64() if self.is64 else self.bin.read_u32()

    def _parse_elf(self):
        b = self.bin
        b.seek(0)
        magic = b.read_u32()
        if magic != 0x464C457F:
            raise ValueError("Bukan file ELF yang valid (.so rusak?).")
        b.seek(4)
        self.is64 = b.read_u8() == 2
        self.ptr = 8 if self.is64 else 4
        b.seek(18)
        self.e_machine = b.read_u16()

        if self.is64:
            b.seek(24); self.entry_point = b.read_u64()
            b.seek(32); phoff = b.read_u64()
            b.seek(54); phnum = b.read_u16()
            b.seek(phoff)
            for _ in range(phnum):
                p = _Phdr()
                p.type = b.read_u32(); p.flags = b.read_u32()
                p.offset = b.read_u64(); p.vaddr = b.read_u64()
                b.read_u64()  # paddr
                p.filesz = b.read_u64(); p.memsz = b.read_u64()
                b.read_u64()  # align
                self.phdrs.append(p)
        else:
            b.seek(24); self.entry_point = b.read_u32()
            b.seek(28); phoff = b.read_u32()
            b.seek(44); phnum = b.read_u16()
            b.seek(phoff)
            for _ in range(phnum):
                p = _Phdr()
                p.type = b.read_u32(); p.offset = b.read_u32(); p.vaddr = b.read_u32()
                b.read_u32()  # paddr
                p.filesz = b.read_u32(); p.memsz = b.read_u32(); p.flags = b.read_u32()
                b.read_u32()  # align
                self.phdrs.append(p)

        dyn_phdr = next((p for p in self.phdrs if p.type == PT_DYNAMIC), None)
        if dyn_phdr is None:
            self._log("[W] PT_DYNAMIC tidak ditemukan — export symbol tidak bisa dibaca, lanjut ke string scan saja.")
            return
        b.seek(dyn_phdr.offset)
        dyn_end = dyn_phdr.offset + dyn_phdr.filesz
        while b.pos < dyn_end:
            tag = self._read_ptr()
            val = self._read_ptr()
            if tag == DT_NULL:
                break
            d = _Dyn(); d.tag = tag; d.val = val
            self.dynamic.append(d)

    def _map_vatr(self, va):
        for p in self.phdrs:
            if p.vaddr <= va < p.vaddr + p.memsz:
                return va - p.vaddr + p.offset
        return -1

    def _find_dyn(self, tag):
        for d in self.dynamic:
            if d.tag == tag:
                return d
        return None

    def _count_symbols(self):
        b = self.bin
        h = self._find_dyn(DT_HASH)
        if h is not None:
            addr = self._map_vatr(h.val)
            if addr < 0:
                return 0
            b.seek(addr)
            b.read_u32()  # nbucket
            return b.read_u32()
        h = self._find_dyn(DT_GNU_HASH)
        if h is None:
            return 0
        addr = self._map_vatr(h.val)
        if addr < 0:
            return 0
        b.seek(addr)
        nbuckets = b.read_u32()
        symoffset = b.read_u32()
        bloom_size = b.read_u32()
        b.read_u32()  # bloom_shift
        buckets_addr = addr + 16 + self.ptr * bloom_size
        last_symbol = 0
        b.seek(buckets_addr)
        for _ in range(nbuckets):
            v = b.read_u32()
            if v > last_symbol:
                last_symbol = v
        if last_symbol < symoffset:
            return symoffset
        chains_base = buckets_addr + 4 * nbuckets
        b.seek(chains_base + (last_symbol - symoffset) * 4)
        safety = 0
        while safety < 5_000_000:
            chain = b.read_u32()
            last_symbol += 1
            safety += 1
            if chain & 1:
                break
        return last_symbol

    def _read_exported_symbols(self):
        b = self.bin
        try:
            symtab = self._find_dyn(DT_SYMTAB)
            strtab = self._find_dyn(DT_STRTAB)
            if symtab is None or strtab is None:
                return
            dynstr_offset = self._map_vatr(strtab.val)
            if dynstr_offset < 0:
                return
            symbol_count = self._count_symbols()
            dynsym_offset = self._map_vatr(symtab.val)
            if dynsym_offset < 0 or symbol_count <= 0:
                return
            b.seek(dynsym_offset)
            for _ in range(symbol_count):
                if self.is64:
                    name_off = b.read_u32()
                    info = b.read_u8()
                    b.read_u8()  # other
                    shndx = b.read_u16()
                    value = b.read_u64()
                    b.read_u64()  # size
                else:
                    name_off = b.read_u32()
                    value = b.read_u32()
                    b.read_u32()  # size
                    info = b.read_u8()
                    b.read_u8()
                    shndx = b.read_u16()
                if value == 0 or shndx == 0:
                    continue
                st_type = info & 0xF
                if st_type != STT_FUNC and st_type != STT_GNU_IFUNC:
                    continue
                name = b.read_string_to_null(dynstr_offset + name_off)
                if not name:
                    continue
                if len(self.exported_symbols) >= MAX_EXPORTED:
                    self.truncated_exports = True
                    break
                self.exported_symbols.append(ExportedSymbol(name, value, self._map_vatr(value)))
        except Exception as e:
            self._log("[W] Gagal membaca dynamic symbol table: %s" % e)

    # ================= string scan =================

    def _scan_strings(self):
        file_len = self.bin.length()
        chunk_size = 1 << 20
        base = 0
        cur = []
        cur_start = -1
        f = self.bin.f
        while base < file_len:
            size = min(chunk_size, file_len - base)
            f.seek(base)
            chunk = f.read(size)
            for i, c in enumerate(chunk):
                if 32 <= c < 127:
                    if cur_start < 0:
                        cur_start = base + i
                    cur.append(chr(c))
                else:
                    self._commit_string(cur, cur_start)
                    cur = []
                    cur_start = -1
            base += size
        self._commit_string(cur, cur_start)

    def _commit_string(self, buf, start):
        if len(buf) < MIN_STRING_LEN:
            return
        s = "".join(buf)
        if len(s) > 160:
            s = s[:160] + "..."
        if not self._looks_like_real_text(s):
            return
        if _is_bridge_or_library_artifact(s) or s.startswith("_Z"):
            return
        if self._contains_any_marker(s):
            self.marker_hits.append(s)
        if len(self._found_strings) < MAX_STRINGS:
            self._found_strings.append((start, s))
        else:
            self.truncated_strings = True

    @staticmethod
    def _looks_like_real_text(s):
        if not s:
            return False
        clean = sum(1 for c in s if c.isalnum() or c in " _.:/-,")
        return (clean / len(s)) >= 0.85

    @staticmethod
    def _contains_any_marker(s):
        for m in COCOS_MARKERS:
            if m in s:
                return True
        for m in UE4_MARKERS:
            if m in s:
                return True
        for m in UNITY_MARKERS:
            if m in s:
                return True
        return False

    def _count_matches(self, markers):
        count = 0
        for _, text in self._found_strings:
            if any(m in text for m in markers):
                count += 1
        return count

    def _detect_engine(self):
        cocos = self._count_matches(COCOS_MARKERS)
        ue4 = self._count_matches(UE4_MARKERS)
        unity = self._count_matches(UNITY_MARKERS)

        if self.forced_engine_name is not None:
            self.engine_name = self.forced_engine_name
        elif cocos == 0 and ue4 == 0 and unity == 0:
            self.engine_name = "Unknown Native Engine"
        elif cocos >= ue4 and cocos >= unity:
            self.engine_name = "Cocos2d-x"
        elif ue4 >= unity:
            self.engine_name = "Unreal Engine"
        else:
            self.engine_name = "Unity (native)"

        for _, text in self._found_strings:
            m1 = COCOS_VERSION_RE.search(text)
            if m1:
                self.detected_version = m1.group()
                return
            m2 = UE_VERSION_RE.search(text)
            if m2:
                self.detected_version = m2.group()
                return

    # ================= dump =================

    def dump(self, out_path):
        self._log("[..] Scanning readable strings...")
        self._scan_strings()
        self._log("[..] Detecting engine + version...")
        self._detect_engine()

        with open(out_path, "w", encoding="utf-8", newline="\n") as w:
            w.write("========================================================\n")
            w.write(" UNIVERSAL DUMPER — NATIVE ENGINE REPORT\n")
            w.write("========================================================\n")
            w.write("File        : %s\n" % os.path.basename(self.path))
            w.write("Engine      : %s%s\n" % (
                self.engine_name, ("  (%s)" % self.detected_version) if self.detected_version else ""))
            w.write("ELF class   : %s\n" % ("ELF64 (64-bit)" if self.is64 else "ELF32 (32-bit)"))
            w.write("Machine     : %s\n" % self.machine_name())
            w.write("Entry point : 0x%X\n" % self.entry_point)
            w.write("File size   : %s\n\n" % _fmt_size(os.path.getsize(self.path)))

            w.write("Engine markers matched : %d (dipakai untuk deteksi engine/versi di atas)\n" % len(self.marker_hits))
            w.write("Readable strings found  : %d%s\n\n" % (
                len(self._found_strings), "+ (truncated)" if self.truncated_strings else ""))

            classes = []
            class_index = {}
            fallback_symbols = []
            others = []

            for sym in self.exported_symbols:
                if not _is_runtime_symbol(sym.name):
                    fallback_symbols.append(sym)
                demangled = demangle(sym.name)
                class_name = demangled.get_class_name()
                if _is_runtime_symbol(sym.name) or (class_name and _is_runtime_class(class_name)):
                    continue
                if not class_name or class_name == "Global":
                    if not _is_linker_noise(sym.name) and not _is_bridge_or_library_artifact(sym.name):
                        others.append(sym)
                    continue
                cb = class_index.get(class_name)
                if cb is None:
                    cb = _ClassBlock(class_name)
                    classes.append(cb)
                    class_index[class_name] = cb
                method = _MethodEntry(demangled.get_method_name(), demangled.get_parameters(),
                                       _is_standalone_method(demangled), sym)
                cb.add(method)

            if not classes:
                native_block = _ClassBlock("Native")
                for sym in fallback_symbols:
                    if _is_linker_noise(sym.name):
                        continue
                    native_block.add(_MethodEntry(sym.name, "", True, sym))
                if native_block.methods:
                    classes.append(native_block)

            classes.sort(key=lambda c: c.name)
            self.reconstructed_class_count = len(classes)

            w.write("---- CLASSES (%d reconstructed from exported C++ symbols) ----\n" % len(classes))
            if not classes:
                w.write("(no demangled C++ classes found — symbols may be stripped or C-style exports only)\n\n")
            else:
                for cb in classes:
                    cb.methods.sort(key=lambda m: (m.name, _offset_or_address(m.symbol), m.parameters))
                    has_inline = any(not m.standalone for m in cb.methods)
                    if has_inline:
                        w.write("class %s {\n" % cb.name)
                        for m in cb.methods:
                            if m.standalone:
                                continue
                            w.write("\t%s(%s); //%s\n" % (m.name, m.parameters, _hex_sym(m.symbol)))
                        w.write("};\n\n")
                    for m in cb.methods:
                        if not m.standalone:
                            continue
                        w.write("class %s::%s(%s); //%s\n" % (cb.name, m.name, m.parameters, _hex_sym(m.symbol)))
                        w.write("};\n\n")

            self.other_symbol_count = len(others)

    def close(self):
        try:
            self.bin.close()
        except Exception:
            pass
