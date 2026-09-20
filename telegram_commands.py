"""
Listener command Telegram (getUpdates long-polling) untuk kelola wallet yang
dipantau tanpa perlu restart bot / edit config.py manual.

Bot ini MULTI-USER & TERBUKA UNTUK SEMUA ORANG: siapa pun yang chat ke bot
bisa /addwallet wallet mereka sendiri. Wallet yang ditambahkan satu chat_id
HANYA kelihatan/dikelola oleh chat_id itu sendiri, dan notifikasi cuma
dikirim ke chat_id yang benar-benar menambahkan wallet tsb — tidak nyampur
ke daftar/wallet punya orang lain.

Command:
  /start, /help                        - info & daftar command
  /addwallet <address> [nama]          - tambah wallet ke daftar pantau kamu
  /removewallet <address|nama>         - hapus wallet dari daftar pantau kamu
  /renamewallet <address|nama> <baru>  - ganti nama wallet yang sudah ada
  /wallets                             - lihat wallet + nama punya kamu

Dijalankan di thread terpisah dari loop scan blockchain utama (lihat
monitor.py: poll_commands_forever() dipanggil lewat threading.Thread), supaya
nambah/hapus/rename wallet langsung kepakai di scan berikutnya tanpa restart
proses bot, dan tidak memblok/menunda scan yang sedang berjalan.
"""
import re
import time

import requests

import config
from state import BotState
from telegram_notifier import send_message

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

HELP_TEXT = (
    "<b>🤖 LP Wallet Tracker Bot</b>\n"
    "──────────────────────\n"
    "Bot ini bisa dipakai siapa saja. Wallet yang kamu tambahkan cuma "
    "kelihatan &amp; dikelola oleh kamu sendiri lewat chat ini — nggak "
    "nyampur ke daftar pantau orang lain.\n\n"
    "<b>Perintah:</b>\n"
    "/addwallet 0x... [nama] — tambah wallet ke daftar pantau kamu\n"
    "/removewallet 0x... atau nama — hapus wallet dari daftar kamu\n"
    "/renamewallet 0x... atau nama &lt;nama baru&gt; — ganti nama wallet\n"
    "/wallets — lihat wallet yang kamu pantau\n"
    "/help — tampilkan pesan ini lagi"
)


def _extract_command(text: str):
    """Pisahkan '/addwallet@BotName 0x123... Nama' jadi ('addwallet', '0x123... Nama')."""
    text = text.strip()
    if not text.startswith("/"):
        return None, None
    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].split("@")[0].lower()  # buang '/' dan '@BotUsername'
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


def _split_address_and_name(arg: str):
    """'0x123...abcd Nama Wallet Ku' -> ('0x123...abcd', 'Nama Wallet Ku')."""
    parts = arg.split(maxsplit=1)
    address = parts[0] if parts else ""
    name = parts[1].strip() if len(parts) > 1 else None
    return address, name


def handle_command(state: BotState, chat_id: str, cmd: str, arg: str, on_wallet_added=None):
    if cmd in ("start", "help"):
        send_message(chat_id, HELP_TEXT)

    elif cmd == "wallets":
        wallets = state.list_wallets(chat_id)
        if not wallets:
            send_message(chat_id, "📭 Kamu belum menambahkan wallet apa pun.\nTambah dengan /addwallet 0x... [nama]")
            return
        lines = ["<b>👛 Wallet yang kamu pantau</b>", "──────────────────────"]
        for i, (addr, name) in enumerate(wallets.items(), start=1):
            lines.append(f"{i}. <b>{name}</b>\n   <code>{addr}</code>")
        send_message(chat_id, "\n".join(lines))

    elif cmd == "addwallet":
        address, name = _split_address_and_name(arg)
        if not ADDRESS_RE.match(address):
            send_message(
                chat_id,
                "⚠️ Format salah. Contoh:\n<code>/addwallet 0x1234...abcd NamaWallet</code>\n"
                "(alamat harus 42 karakter, diawali 0x; nama boleh dikosongkan)",
            )
            return
        added = state.add_wallet(chat_id, address, name)
        final_name = state.get_wallet_name(chat_id, address)
        if added:
            send_message(
                chat_id,
                f"✅ Wallet ditambahkan ke daftar pantau kamu:\n<b>{final_name}</b>\n"
                f"<code>{address.lower()}</code>\n\n"
                "🔍 Lagi cek posisi LP yang sudah ada di wallet ini (kalau ada), tunggu "
                "sebentar ya — hasilnya dikirim otomatis kalau ketemu.",
            )
            if on_wallet_added:
                try:
                    on_wallet_added(address.lower(), chat_id)
                except Exception as e:
                    print(f"[telegram_commands] on_wallet_added gagal: {e}")
        else:
            send_message(chat_id, f"ℹ️ Wallet ini sudah ada di daftar pantau kamu:\n<b>{final_name}</b>\n<code>{address.lower()}</code>")

    elif cmd == "removewallet":
        if not arg:
            send_message(chat_id, "⚠️ Contoh: <code>/removewallet 0x1234...abcd</code> atau <code>/removewallet NamaWallet</code>")
            return
        removed = state.remove_wallet(chat_id, arg)
        if removed:
            send_message(
                chat_id,
                f"🗑️ Wallet dihapus dari daftar pantau kamu:\n<code>{removed}</code>\n\n"
                "Catatan: histori posisi LP wallet ini tetap tersimpan — kalau nanti wallet "
                "yang sama di-/addwallet lagi, histori lamanya otomatis kepakai lagi.",
            )
        else:
            send_message(chat_id, f"ℹ️ Wallet/nama <b>{arg}</b> tidak ketemu di daftar pantau kamu.")

    elif cmd == "renamewallet":
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            send_message(chat_id, "⚠️ Contoh: <code>/renamewallet 0x1234...abcd NamaBaru</code> atau <code>/renamewallet NamaLama NamaBaru</code>")
            return
        target, new_name = parts[0], parts[1].strip()
        renamed = state.rename_wallet(chat_id, target, new_name)
        if renamed:
            send_message(chat_id, f"✏️ Wallet <code>{renamed}</code> sekarang bernama <b>{new_name}</b>.")
        else:
            send_message(chat_id, f"ℹ️ Wallet/nama <b>{target}</b> tidak ketemu di daftar pantau kamu.")

    # command tidak dikenal -> diamkan saja (jangan spam balasan tiap pesan random)


def poll_commands_forever(state: BotState, on_wallet_added=None):
    """Loop long-polling getUpdates. Dipanggil di thread terpisah oleh
    monitor.py. Menerima command dari SIAPA PUN yang chat ke bot (bot ini
    memang didesain multi-user/publik) — tidak ada lagi pembatasan satu
    TELEGRAM_CHAT_ID seperti versi sebelumnya.

    `on_wallet_added(address, chat_id)` — kalau diisi, dipanggil tiap kali
    /addwallet berhasil, supaya monitor.py bisa langsung backfill posisi LP
    existing wallet itu (lihat monitor.py: backfill_wallet_positions)."""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:
        try:
            offset = state.get_telegram_offset()
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset + 1
            resp = requests.get(url, params=params, timeout=35)
            resp.raise_for_status()
            updates = resp.json().get("result", [])

            for upd in updates:
                state.set_telegram_offset(upd["update_id"])
                msg = upd.get("message") or upd.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                chat_id = str(msg["chat"]["id"])
                cmd, arg = _extract_command(msg["text"])
                if cmd:
                    try:
                        handle_command(state, chat_id, cmd, arg, on_wallet_added=on_wallet_added)
                    except Exception as e:
                        print(f"[telegram_commands] error handling command dari {chat_id}:", e)
        except Exception as e:
            print("[telegram_commands] error polling updates:", e)
            time.sleep(5)
