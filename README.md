# IL2CPP / COCOS2D / UNREAL Dumper — Bot Telegram

Port Python dari mesin dump aplikasi Android "IL2CPP Dumper" (Dani Modder) —
sama-sama bisa dump IL2CPP (dump.cs) maupun analisa native Cocos2d-x/Unreal
Engine, tapi lewat chat bot Telegram.

## Cara pasang di hosting bot (bot.py + requirements.txt)

1. Upload SEMUA isi folder ini (`bot.py`, `requirements.txt`, folder `engine/`)
   ke hosting bot-nya (bukan cuma `bot.py` doang — folder `engine/` WAJIB ikut
   ke-upload, itu isinya mesin dump-nya).
2. Set environment variable `BOT_TOKEN` = token bot dari @BotFather.
   Kalau hosting-nya gak bisa set env var, boleh juga langsung edit baris
   `BOT_TOKEN = os.environ.get("BOT_TOKEN", "ISI_TOKEN_BOT_DI_SINI")` di
   `bot.py`, ganti `"ISI_TOKEN_BOT_DI_SINI"` jadi token bot kamu.
3. Jalanin `bot.py` (biasanya otomatis kalau hosting-nya model "upload bot.py +
   requirements.txt terus auto-run", kayak yang di screenshot kamu).

## Cara pakai bot-nya

- `/start` — mulai, lihat menu
- `/mode` — pilih AUTO / IL2CPP / COCOS2D / UNREAL (tombol)
- Kirim file:
  - **APK langsung** → bot otomatis nyari `libil2cpp.so` / `libcocos2dcpp.so` /
    `libUE4.so` + `global-metadata.dat` di dalamnya (kayak "Pilih APK" di app)
  - **`.so` + `.dat` manual** → kirim 2 file terpisah kalau APK-nya gak punya
    library yang dicari, atau mau pilih manual
- `/status` — lihat file yang udah kepilih
- `/setname nama.cs` — ganti nama file hasil (default `dump.cs`)
- `/dump` — mulai proses dump, bot bakal update progress live (mirip console
  di app), terus kirim balik file hasilnya
- `/reset` — kosongin pilihan file

## Struktur folder

```
bot.py                  <- entry point bot
requirements.txt
engine/
  bin_io.py             <- baca/tulis file biner
  metadata.py           <- parser global-metadata.dat (v16-31 + v38/39)
  il2cpp.py              <- parser ELF + relocation + cari registration
  section_helper.py      <- PlusSearch (mmap)
  custom_attr.py          <- decoder custom attribute v29+
  dumper.py               <- penulis dump.cs
  native_dumper.py         <- dumper Cocos2d-x/Unreal/native lain
  demangle_util.py          <- demangle C++ (pakai cxxfilt / libstdc++)
  apk_extract.py             <- extract lib & metadata dari APK
  pipeline.py                <- orkestrasi + folder Dump/Dump1/dst
```

Hasil dump per user disimpan di `data/<user_id>/Dumper/Dump`, `Dump1`, dst —
sama persis konsepnya kayak folder `/storage/emulated/0/Dumper/Dump...` di app
Android (gak pernah ketimpa punya run sebelumnya).

## Catatan penting — baca ini

Kode ini udah aku tes jalan (bukan cuma cek syntax): parsing ELF beneran,
baca exported symbol, demangle C++ pakai symbol hasil compile g++ asli, extract
dari APK beneran, sampai generate dump.cs dari data contoh — semuanya lolos.

**TAPI** aku nulis ini tanpa bisa nyoba pakai APK/game Unity **asli** (gak ada
akses ke file kalian). Jadi kemungkinan ada game/versi il2cpp tertentu yang
hasilnya beda dikit dari app Android kita — sama persis kayak waktu kita
nemuin & benerin bug OOM/relocation di app Android, mungkin ada 1-2 ronde
perbaikan lagi yang perlu dilakuin begitu ketemu kasus nyata. Kalau ada hasil
yang aneh/beda, kirim aja pesan errornya atau contoh perbedaannya, nanti
dibenerin.
