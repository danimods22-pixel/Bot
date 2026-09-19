"""
Orkestrasi jalur dump IL2CPP — mirror persis alur runIl2CppDump() di
MainActivity.java: parse metadata -> load .so -> cari registration -> tulis dump.cs.
Juga nyediain next_dump_subdir() yang mirror getNewDumpSubDir() (folder
Dump/Dump1/Dump2/dst, gak pernah ketimpa punya run sebelumnya).
"""
import os
import time

from .metadata import Metadata
from .il2cpp import Il2Cpp, fmt_ver
from .dumper import Dumper


class DumpFailed(Exception):
    pass


class _Il2CppLogAdapter:
    """Samain gaya log MainActivity: prefix [I]/[W]/[E]."""

    def __init__(self, emit):
        self._emit = emit

    def info(self, s):
        self._emit("[I] %s" % s)

    def warn(self, s):
        self._emit("[W] %s" % s)

    def error(self, s):
        self._emit("[E] %s" % s)


def next_dump_subdir(base_dir):
    """Mirror getNewDumpSubDir(): "Dump" buat yang pertama, "Dump1"/"Dump2"/dst
    buat berikutnya, dicek dari isi folder di disk (bukan counter di memori)."""
    os.makedirs(base_dir, exist_ok=True)
    name = "Dump"
    i = 1
    path = os.path.join(base_dir, name)
    while os.path.exists(path):
        name = "Dump%d" % i
        path = os.path.join(base_dir, name)
        i += 1
    os.makedirs(path, exist_ok=True)
    return path


def safe_output_name(name, default_ext=".cs"):
    name = (name or ("dump" + default_ext)).strip()
    if not name:
        name = "dump" + default_ext
    if "." not in os.path.basename(name):
        name += default_ext
    bad = '\\/:*?"<>|'
    return "".join(c for c in name if c not in bad)


def fmt_size(num_bytes):
    if num_bytes < 1024:
        return "%d B" % num_bytes
    units = ["KB", "MB", "GB"]
    val = float(num_bytes)
    for u in units:
        val /= 1024.0
        if val < 1024.0:
            return "%.1f %s" % (val, u)
    return "%.1f TB" % (val / 1024.0)


class _NativeLogAdapter:
    def __init__(self, emit):
        self._emit = emit

    def info(self, s):
        self._emit(s)


def run_native_dump(lib_path, mode, out_base_dir, output_name, log_line):
    """mode: "COCOS2D"/"UNREAL"/"AUTO". Return path hasil dump kalau sukses."""
    from .native_dumper import NativeEngineDumper
    t0 = time.time()
    dumper = None
    try:
        log_line("[..] Memuat library...")
        nlog = _NativeLogAdapter(log_line)
        forced = "Cocos2d-x" if mode == "COCOS2D" else ("Unreal Engine" if mode == "UNREAL" else None)
        dumper = NativeEngineDumper(lib_path, forced, nlog)
        log_line("[OK] %s | machine=%s" % ("ELF64" if dumper.is64bit() else "ELF32", dumper.machine_name()))

        out_dir = next_dump_subdir(out_base_dir)
        out_path = os.path.join(out_dir, safe_output_name(output_name, default_ext=".txt"))
        dumper.dump(out_path)

        engine_label = dumper.engine_name + ((" (%s)" % dumper.detected_version) if dumper.detected_version else "")
        log_line("[OK] Engine terdeteksi: %s" % engine_label)
        log_line("[OK] Exported: %d | Strings: %d | Markers: %d" % (
            dumper.get_exported_count(), dumper.get_string_count(), dumper.get_marker_count()))
        log_line("[OK] Classes: %d | Others: %d" % (dumper.reconstructed_class_count, dumper.other_symbol_count))

        ms = int((time.time() - t0) * 1000)
        log_line("------------------------------------------------------")
        log_line("[SELESAI] %d detik | %s" % (ms // 1000, fmt_size(os.path.getsize(out_path))))
        log_line("[SELESAI] Lokasi: %s" % out_path)
        log_line("[READY] Hasil dump siap di folder output.")
        return out_path
    finally:
        if dumper is not None:
            dumper.close()


def run_il2cpp_dump(lib_path, meta_path, out_base_dir, output_name, log_line):
    """log_line(str) dipanggil tiap ada 1 baris log baru (kayak printLine()).
    Return: path dump.cs kalau sukses. Raise DumpFailed kalau gagal."""
    t0 = time.time()
    metadata = None
    il2cpp = None
    try:
        log_line("[..] Membaca global-metadata.dat...")
        metadata = Metadata(meta_path)
        log_line("[OK] Versi metadata: %s | Image: %d | Type: %d | Method: %d | Field: %d" % (
            fmt_ver(metadata.version), len(metadata.imageDefs), len(metadata.typeDefs),
            len(metadata.methodDefs), len(metadata.fieldDefs)))

        log_line("[..] Memuat libil2cpp.so + relocation...")
        elog = _Il2CppLogAdapter(log_line)
        il2cpp = Il2Cpp(lib_path, elog)
        il2cpp.version = metadata.version
        log_line("[OK] %s | ptr=%d | machine=%d" % (
            "ELF64" if il2cpp.is64 else "ELF32", il2cpp.ptr, il2cpp.eMachine))

        log_line("[..] PlusSearch: mencari CodeRegistration & MetadataRegistration...")
        if not il2cpp.find_registrations(metadata):
            raise DumpFailed("Registration TIDAK ditemukan — kemungkinan libil2cpp.so di-protect/strip aneh")
        log_line("[OK] Registration ditemukan! | Types: %d | codeGenModules: %d" % (
            len(il2cpp.types), len(il2cpp.codeGenModuleMethodPointers)))

        log_line("[..] Membuat dump.cs...")
        dumper = Dumper(metadata, il2cpp)
        out_dir = next_dump_subdir(out_base_dir)
        out_path = os.path.join(out_dir, safe_output_name(output_name))
        dumper.dump(out_path)
        log_line("[OK] Class terdump: %d/%d | Gagal: %d" % (
            dumper.dumpedOk, dumper.totalExpected, dumper.failedCount))

        ms = int((time.time() - t0) * 1000)
        log_line("------------------------------------------------------")
        log_line("[SELESAI] %d detik | %s" % (ms // 1000, fmt_size(os.path.getsize(out_path))))
        log_line("[SELESAI] Lokasi: %s" % out_path)
        log_line("[READY] Hasil dump siap di folder output.")
        return out_path
    finally:
        if metadata is not None:
            try:
                metadata.close()
            except Exception:
                pass
        if il2cpp is not None:
            try:
                il2cpp.close()
            except Exception:
                pass
