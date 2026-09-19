"""
Bot Telegram — IL2CPP / COCOS2D / UNREAL Dumper.
Alur: user pilih mode -> kirim APK (auto-extract) ATAU kirim libil2cpp.so +
global-metadata.dat manual -> /dump -> bot balikin hasil dump (dump.cs atau
laporan native) + log progres real-time (mirip console di app Android).

Jalanin: python bot.py   (baca token dari env var BOT_TOKEN, atau isi langsung
di bawah / lewat file .env — lihat README.md)
"""
import asyncio
import logging
import os
import shutil
import time
import traceback

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

from engine.pipeline import run_il2cpp_dump, run_native_dump, DumpFailed
from engine.apk_extract import extract_from_apk, candidate_lib_names_for_mode

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("il2cpp-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ISI_TOKEN_BOT_DI_SINI")

# Semua file kerja (upload + hasil dump) disimpan per-user di sini, mirip folder
# "Dumper" di app Android (tapi dipisah per user_id biar gak nyampur punya orang lain).
DATA_DIR = os.environ.get("DUMPER_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

MODES = ["AUTO", "IL2CPP", "COCOS2D", "UNREAL"]
MAX_TELEGRAM_FILE_MB = 2000  # limit upload bot API (bot lokal/self-hosted bisa lebih; default cloud API ~2GB utk bot biasa, 50MB utk download via getFile pada beberapa alur — lihat README

# ================= session per user =================

class Session:
    def __init__(self):
        self.mode = "IL2CPP"
        self.lib_path = None
        self.lib_name = None
        self.meta_path = None
        self.output_name = "dump.cs"

    def work_dir(self, user_id):
        d = os.path.join(DATA_DIR, str(user_id), "work")
        os.makedirs(d, exist_ok=True)
        return d

    def output_dir(self, user_id):
        d = os.path.join(DATA_DIR, str(user_id), "Dumper")
        os.makedirs(d, exist_ok=True)
        return d

    def reset_files(self):
        self.lib_path = None
        self.lib_name = None
        self.meta_path = None


_sessions = {}


def get_session(user_id):
    s = _sessions.get(user_id)
    if s is None:
        s = Session()
        _sessions[user_id] = s
    return s


# ================= helper UI =================

def mode_keyboard(current):
    row = []
    for m in MODES:
        label = ("✅ " if m == current else "") + m
        row.append(InlineKeyboardButton(label, callback_data="mode:%s" % m))
    return InlineKeyboardMarkup([row])


def status_text(session):
    lib = session.lib_name or (os.path.basename(session.lib_path) if session.lib_path else "belum dipilih")
    meta = "sudah dipilih" if session.meta_path else "belum dipilih"
    need_meta = session.mode == "IL2CPP" or session.mode == "AUTO"
    lines = [
        "*Mode:* `%s`" % session.mode,
        "*Library (.so):* `%s`" % lib,
    ]
    if need_meta:
        lines.append("*global-metadata.dat:* `%s`" % meta)
    lines.append("")
    lines.append("Kirim file `.apk` (auto-extract) atau `.so`/`.dat` manual, terus /dump.")
    return "\n".join(lines)


# ================= command handlers =================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 *IL2CPP / COCOS2D / UNREAL Dumper Bot*\n\n"
        "Perintah:\n"
        "/mode — pilih engine (AUTO/IL2CPP/COCOS2D/UNREAL)\n"
        "/dump — mulai dump (setelah file lengkap)\n"
        "/status — lihat file yang udah kepilih\n"
        "/reset — hapus pilihan file, mulai dari awal\n"
        "/setname nama.cs — ganti nama file output (default dump.cs)\n\n"
        "Kirim file `.apk` (bot auto-extract libnya) atau kirim `.so` + `.dat` manual.",
        parse_mode=ParseMode.MARKDOWN)
    session = get_session(update.effective_user.id)
    await update.message.reply_text(status_text(session), parse_mode=ParseMode.MARKDOWN,
                                     reply_markup=mode_keyboard(session.mode))


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    await update.message.reply_text("Pilih mode engine:", reply_markup=mode_keyboard(session.mode))


async def on_mode_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session = get_session(update.effective_user.id)
    new_mode = query.data.split(":", 1)[1]
    if new_mode != session.mode:
        session.mode = new_mode
        session.reset_files()  # ganti tab -> mulai fresh, sama kayak konsep row "Pilih APK" ke-reset di app
    await query.edit_message_text(status_text(session), parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=mode_keyboard(session.mode))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    await update.message.reply_text(status_text(session), parse_mode=ParseMode.MARKDOWN,
                                     reply_markup=mode_keyboard(session.mode))


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    session.reset_files()
    await update.message.reply_text("Oke, file dikosongin lagi. Kirim APK atau .so/.dat baru.")


async def cmd_setname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Contoh: `/setname MyGameDump.cs`", parse_mode=ParseMode.MARKDOWN)
        return
    session.output_name = " ".join(context.args).strip()
    await update.message.reply_text("Nama output diganti jadi: `%s`" % session.output_name,
                                     parse_mode=ParseMode.MARKDOWN)


# ================= file upload handler =================

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    doc = update.message.document
    fname = doc.file_name or "file"
    lower = fname.lower()

    status_msg = await update.message.reply_text("⏳ Ngunduh `%s`..." % fname, parse_mode=ParseMode.MARKDOWN)
    tg_file = await doc.get_file()
    work_dir = session.work_dir(update.effective_user.id)

    if lower.endswith(".apk"):
        apk_path = os.path.join(work_dir, "input.apk")
        await tg_file.download_to_drive(apk_path)
        await status_msg.edit_text("🔎 Nyari library di dalam APK...")
        try:
            res = extract_from_apk(apk_path, session.mode, work_dir)
        except Exception as e:
            await status_msg.edit_text("❌ Gagal buka APK: %s" % e)
            return
        msg_lines = []
        if res.lib_path:
            session.lib_path = res.lib_path
            session.lib_name = res.lib_name
            msg_lines.append("✅ Library: `%s`" % res.lib_name)
        else:
            wanted = " / ".join(candidate_lib_names_for_mode(session.mode))
            msg_lines.append("⚠️ Gak ketemu library (%s) di dalam APK ini." % wanted)
        if session.mode in ("IL2CPP", "AUTO"):
            if res.meta_path:
                session.meta_path = res.meta_path
                msg_lines.append("✅ global-metadata.dat ketemu")
            elif session.mode == "IL2CPP":
                msg_lines.append("⚠️ global-metadata.dat gak ketemu di dalam APK ini.")
        msg_lines.append("")
        msg_lines.append(status_text(session))
        await status_msg.edit_text("\n".join(msg_lines), parse_mode=ParseMode.MARKDOWN)

    elif lower.endswith(".so"):
        dst = os.path.join(work_dir, "lib_%d.so" % int(time.time()))
        await tg_file.download_to_drive(dst)
        session.lib_path = dst
        session.lib_name = fname
        await status_msg.edit_text("✅ Library `%s` disimpan.\n\n%s" % (fname, status_text(session)),
                                    parse_mode=ParseMode.MARKDOWN)

    elif lower.endswith(".dat"):
        dst = os.path.join(work_dir, "global-metadata.dat")
        await tg_file.download_to_drive(dst)
        session.meta_path = dst
        await status_msg.edit_text("✅ `global-metadata.dat` disimpan.\n\n%s" % status_text(session),
                                    parse_mode=ParseMode.MARKDOWN)
    else:
        await status_msg.edit_text("File `%s` gak dikenali. Kirim `.apk`, `.so`, atau `.dat` (global-metadata.dat)." % fname,
                                    parse_mode=ParseMode.MARKDOWN)


# ================= dump =================

class _LiveLog:
    """Nampung baris log & throttle edit_message_text biar gak kena rate-limit Telegram
    (mirip printLine() + consoleScroll di app, tapi di-batch tiap ~1.2 detik)."""

    def __init__(self, message):
        self.message = message
        self.lines = []
        self._last_edit = 0.0
        self._pending = False
        self._loop = asyncio.get_event_loop()

    def add(self, line):
        self.lines.append(line)
        log.info(line)
        now = time.time()
        if now - self._last_edit >= 1.2 and not self._pending:
            self._pending = True
            asyncio.run_coroutine_threadsafe(self._flush(), self._loop)

    async def _flush(self):
        try:
            text = self._render()
            await self.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
            self._last_edit = time.time()
        except Exception:
            pass
        finally:
            self._pending = False

    async def final_flush(self):
        try:
            await self.message.edit_text(self._render(), parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    def _render(self):
        tail = self.lines[-25:]  # batasin biar gak kena limit panjang pesan Telegram (4096 char)
        body = "\n".join(tail)
        text = "```\n%s\n```" % body
        if len(text) > 3900:
            text = text[-3900:]
        return text


async def cmd_dump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    user_id = update.effective_user.id

    if session.mode == "IL2CPP" and (not session.lib_path or not session.meta_path):
        await update.message.reply_text("Untuk mode IL2CPP, `libil2cpp.so` DAN `global-metadata.dat` harus ada dulu. /status buat cek.",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    if session.mode != "IL2CPP" and not session.lib_path:
        await update.message.reply_text("Library `.so` belum ada. Kirim APK atau `.so` dulu.", parse_mode=ParseMode.MARKDOWN)
        return

    status_msg = await update.message.reply_text("```\n[..] Memulai dump...\n```", parse_mode=ParseMode.MARKDOWN)
    live = _LiveLog(status_msg)
    live.add("[..] Memulai dump...")

    loop = asyncio.get_event_loop()
    use_il2cpp = session.mode == "IL2CPP" or (session.mode == "AUTO" and session.meta_path)

    def work():
        out_dir = session.output_dir(user_id)
        if use_il2cpp:
            return run_il2cpp_dump(session.lib_path, session.meta_path, out_dir, session.output_name, live.add)
        return run_native_dump(session.lib_path, session.mode, out_dir, session.output_name, live.add)

    try:
        out_path = await loop.run_in_executor(None, work)
    except DumpFailed as e:
        live.add("[GAGAL] %s" % e)
        await live.final_flush()
        return
    except Exception as e:
        live.add("[ERROR] %s: %s" % (type(e).__name__, e))
        log.error("dump error: %s", traceback.format_exc())
        await live.final_flush()
        return

    await live.final_flush()
    try:
        with open(out_path, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(out_path),
                                                  caption="✅ Hasil dump: `%s`" % os.path.basename(out_path),
                                                  parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text("Dump selesai tapi gagal kirim file (%s). File ada di server: `%s`" % (e, out_path),
                                         parse_mode=ParseMode.MARKDOWN)

    # UPGRADE (samain kayak app Android kita): abis dump SUKSES, reset pilihan file biar
    # siap dump APK/app berikutnya tanpa nyangkut pilihan lama.
    session.reset_files()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled exception", exc_info=context.error)


def main():
    if not BOT_TOKEN or BOT_TOKEN == "ISI_TOKEN_BOT_DI_SINI":
        raise SystemExit("Set BOT_TOKEN dulu (env var BOT_TOKEN atau edit langsung di bot.py).")
    os.makedirs(DATA_DIR, exist_ok=True)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("setname", cmd_setname))
    app.add_handler(CommandHandler("dump", cmd_dump))
    app.add_handler(CallbackQueryHandler(on_mode_button, pattern=r"^mode:"))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_error_handler(on_error)

    log.info("Bot jalan...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
