import requests

from config import TELEGRAM_BOT_TOKEN, DEVELOPER_NAME, SHOW_DEVELOPER_CREDIT


def send_message(chat_id, text: str):
    """Kirim pesan ke SATU chat_id spesifik. Bot ini multi-user, jadi tidak
    ada lagi satu chat_id global — tiap notifikasi dikirim ke pemilik wallet
    yang bersangkutan saja (lihat notify() di monitor.py)."""
    if not chat_id:
        return
    if SHOW_DEVELOPER_CREDIT:
        text = f"{text}\n\n<i>🛠 Dev by {DEVELOPER_NAME}</i>"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            print(f"[telegram] gagal kirim pesan ke {chat_id}:", r.status_code, r.text)
    except Exception as e:
        print(f"[telegram] error kirim pesan ke {chat_id}:", e)


def short(addr: str) -> str:
    return addr[:6] + "..." + addr[-4:] if addr else "-"
