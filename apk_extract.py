"""
Extract libil2cpp.so / libcocos2dcpp.so / libUE4.so / global-metadata.dat dari
APK — mirror bagian "scan base APK" di MainActivity.java (lib/<arch>/<name>.so).
Catatan: APK split Play Store (via PackageManager, SplitApkHelper) sengaja TIDAK
diporting — itu khusus app yang sudah TERINSTAL di Android, konsepnya gak ada di
konteks bot Telegram (di sini user upload file APK langsung).
"""
import os
import zipfile

ARCH_ORDER = ["arm64-v8a", "armeabi-v7a", "x86_64", "x86"]


def candidate_lib_names_for_mode(mode):
    if mode == "IL2CPP":
        return ["libil2cpp.so"]
    if mode == "COCOS2D":
        return ["libcocos2dcpp.so"]
    if mode == "UNREAL":
        return ["libUE4.so"]
    return ["libil2cpp.so", "libcocos2dcpp.so", "libUE4.so"]  # AUTO


def _find_entry_ending_with(zf, filename):
    for name in zf.namelist():
        if not name.endswith("/") and name.endswith("/" + filename):
            return name
    return None


class ApkExtractResult:
    def __init__(self):
        self.lib_path = None
        self.lib_name = None
        self.meta_path = None


def extract_from_apk(apk_path, mode, work_dir, log_line=None):
    """mode: "AUTO"/"IL2CPP"/"COCOS2D"/"UNREAL". Return ApkExtractResult."""
    os.makedirs(work_dir, exist_ok=True)
    result = ApkExtractResult()
    candidates = candidate_lib_names_for_mode(mode)

    with zipfile.ZipFile(apk_path, "r") as zf:
        names = set(zf.namelist())
        found_lib = False
        for arch in ARCH_ORDER:
            for lib_name in candidates:
                entry = "lib/%s/%s" % (arch, lib_name)
                if entry not in names:
                    continue
                dest_name = "libil2cpp.so" if (mode == "IL2CPP" or lib_name == "libil2cpp.so") else "native_lib.so"
                dst = os.path.join(work_dir, dest_name)
                with zf.open(entry) as src, open(dst, "wb") as out:
                    _copy(src, out)
                result.lib_path = dst
                result.lib_name = lib_name
                if log_line:
                    log_line("[OK] Ditemukan di dalam APK: %s (%s)" % (lib_name, _fmt_size(os.path.getsize(dst))))
                found_lib = True
                break
            if found_lib:
                break

        if mode not in ("COCOS2D", "UNREAL"):
            meta_entry = _find_entry_ending_with(zf, "global-metadata.dat")
            if meta_entry:
                dst = os.path.join(work_dir, "global-metadata.dat")
                with zf.open(meta_entry) as src, open(dst, "wb") as out:
                    _copy(src, out)
                result.meta_path = dst
                if log_line:
                    log_line("[OK] Ditemukan di dalam APK: global-metadata.dat (%s)" % _fmt_size(os.path.getsize(dst)))

    return result


def _copy(src, dst, chunk_size=1 << 20):
    while True:
        chunk = src.read(chunk_size)
        if not chunk:
            break
        dst.write(chunk)


def _fmt_size(n):
    if n > 1048576:
        return "%.1f MB" % (n / 1048576.0)
    if n > 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%d B" % n
