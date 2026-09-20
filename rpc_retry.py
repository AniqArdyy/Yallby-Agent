"""
Helper retry/backoff untuk panggilan RPC (web3) & HTTP lain yang bisa kena
rate-limit (HTTP 429) atau error transient (timeout, 5xx, koneksi putus).

Kenapa ini penting:
  RPC publik gratis (spt rpc.mainnet.chain.robinhood.com) rate-limit-nya
  ketat. Tanpa ini, web3.py akan pakai retry bawaannya sendiri yang bisa
  `time.sleep()` lama di dalam satu panggilan (bikin bot kelihatan
  "hang"/macet total — ini yang bikin proses harus di-Ctrl+C manual
  berkali-kali, makanya notifikasi "Bot Aktif" muncul berulang kali di
  Telegram). Dengan wrapper ini, retry jadi:
    - jumlahnya dibatasi (tidak retry selamanya),
    - delay-nya exponential backoff + jitter (biar tidak makin membebani
      RPC yang lagi rate-limit),
    - kalau tetap gagal setelah max_retries, exception dilempar balik ke
      pemanggil supaya main loop bisa lanjut ke wallet/block berikutnya
      alih-alih memblokir seluruh proses.
"""
import random
import time


def _is_rate_limited_or_transient(exc: Exception) -> bool:
    msg = str(exc).lower()
    markers = (
        "429", "too many requests", "timeout", "timed out",
        "connection", "reset by peer", "502", "503", "504",
        "temporarily unavailable",
    )
    return any(m in msg for m in markers)


def call_with_retry(fn, *args, max_retries=5, base_delay=2.0, max_delay=60.0,
                     label="rpc_call", **kwargs):
    """Jalankan fn(*args, **kwargs) dengan retry+exponential backoff kalau
    kena error rate-limit/transient. Error lain (mis. bug logika/ABI salah)
    langsung dilempar tanpa retry, biar tidak menyembunyikan bug asli."""
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            attempt += 1
            if attempt > max_retries or not _is_rate_limited_or_transient(e):
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay += random.uniform(0, delay * 0.25)  # jitter biar tidak sinkron antar request
            print(f"[rpc_retry] {label}: percobaan {attempt}/{max_retries} gagal ({e}); "
                  f"tunggu {delay:.1f}s...")
            time.sleep(delay)
            