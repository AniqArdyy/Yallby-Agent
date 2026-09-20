import json
import os
import threading

_lock = threading.RLock()


class BotState:
    """Penyimpanan berbasis file JSON, MULTI-USER.

    Struktur (skema v2):
    {
      "last_block": 123456,
      "telegram_update_offset": 123,
      "users": {
          "<chat_id>": {
              "wallets": {"0xwallet": "Nama Wallet", ...}
          }
      },
      "wallet_tokens": {"0xwallet": {"v3": [tokenId,...], "v4": [tokenId,...]}},
      "positions": {
          "v3:<tokenId>": {...},
          "v4:<tokenId>": {...}
      }
    }

    Setiap chat Telegram (chat_id) punya daftar wallet SENDIRI (lengkap dengan
    nama masing-masing) lewat /addwallet, /removewallet, /renamewallet — jadi
    bot ini bisa dipakai rame-rame oleh banyak orang sekaligus: wallet yang
    ditambahkan satu orang TIDAK kelihatan/tidak bisa diubah oleh orang lain,
    dan notifikasi Telegram cuma dikirim ke chat_id yang benar-benar
    menambahkan wallet tsb.

    Data on-chain (wallet_tokens, positions) tetap disimpan global per-alamat
    wallet, karena itu memang fakta on-chain (bukan milik satu user) — kalau
    dua orang kebetulan menambahkan alamat yang sama, keduanya akan dapat
    notifikasi masing-masing, tapi daftar wallet mereka sendiri tetap terpisah.

    Semua method dikunci pakai RLock modul-level, karena state ini diakses
    dari 2 thread: loop scan blockchain utama & listener command Telegram
    yang jalan bersamaan.
    """

    def __init__(self, path: str):
        self.path = path
        self.data = {
            "last_block": None, "telegram_update_offset": None,
            "users": {}, "wallet_tokens": {}, "positions": {},
        }
        self.load()

    def load(self):
        with _lock:
            if os.path.exists(self.path):
                with open(self.path, "r") as f:
                    loaded = json.load(f)
                self.data.update(loaded)
            self._migrate_legacy_schema()

    def _migrate_legacy_schema(self):
        """Migrasi otomatis dari skema lama single-user (`"tracked_wallets": [...]`)
        ke skema baru multi-user (`"users": {chat_id: {"wallets": {...}}}`).

        Wallet lama dipindah jadi milik `config.ADMIN_CHAT_ID` supaya histori &
        wallet yang sudah dipantau sebelumnya TIDAK HILANG saat upgrade ke
        versi multi-user ini — tinggal di-/renamewallet atau /removewallet
        manual lewat Telegram kalau perlu dirapikan lagi."""
        legacy = self.data.pop("tracked_wallets", None)
        if not legacy:
            return
        import config  # import lokal, hindari circular import di level modul
        owner = str(config.ADMIN_CHAT_ID) if config.ADMIN_CHAT_ID else "legacy"
        with _lock:
            user = self.data.setdefault("users", {}).setdefault(owner, {"wallets": {}})
            migrated = 0
            for i, w in enumerate(legacy, start=1):
                w = w.lower()
                if w not in user["wallets"]:
                    user["wallets"][w] = f"Wallet {i}"
                    migrated += 1
            self.save()
            print(f"[state] migrasi {migrated} wallet lama -> user '{owner}' (skema multi-user baru). "
                  f"Rename/hapus manual lewat /renamewallet atau /removewallet kalau perlu.")

    def save(self):
        with _lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def get_last_block(self):
        return self.data.get("last_block")

    def set_last_block(self, block_number: int):
        with _lock:
            self.data["last_block"] = block_number

    def get_telegram_offset(self):
        return self.data.get("telegram_update_offset")

    def set_telegram_offset(self, offset: int):
        with _lock:
            self.data["telegram_update_offset"] = offset
            self.save()

    # ── Wallet per-user: add/remove/rename lewat command Telegram ──────────

    def _find_wallet_key(self, chat_id: str, address_or_name: str):
        """Cocokkan input user ke address (case-insensitive) ATAU ke nama
        wallet yang sudah didaftarkan (case-insensitive), HANYA di dalam
        daftar wallet chat_id ini -> return address asli, atau None."""
        wallets = self.data.get("users", {}).get(str(chat_id), {}).get("wallets", {})
        needle = address_or_name.strip().lower()
        if needle in wallets:
            return needle
        for addr, name in wallets.items():
            if name.lower() == needle:
                return addr
        return None

    def add_wallet(self, chat_id: str, address: str, name: str = None) -> bool:
        """True kalau berhasil ditambah, False kalau chat ini sudah pernah
        menambahkan wallet yang sama sebelumnya."""
        chat_id = str(chat_id)
        address = address.lower()
        with _lock:
            user = self.data.setdefault("users", {}).setdefault(chat_id, {"wallets": {}})
            if address in user["wallets"]:
                return False
            if not name:
                name = f"Wallet {len(user['wallets']) + 1}"
            user["wallets"][address] = name
            self.save()
            return True

    def remove_wallet(self, chat_id: str, address_or_name: str):
        """Return address yang dihapus (str) kalau berhasil, None kalau tidak
        ketemu di daftar wallet chat_id ini."""
        chat_id = str(chat_id)
        with _lock:
            addr = self._find_wallet_key(chat_id, address_or_name)
            if not addr:
                return None
            del self.data["users"][chat_id]["wallets"][addr]
            self.save()
            return addr

    def rename_wallet(self, chat_id: str, address_or_name: str, new_name: str):
        """Return address yang di-rename (str) kalau berhasil, None kalau
        tidak ketemu."""
        chat_id = str(chat_id)
        with _lock:
            addr = self._find_wallet_key(chat_id, address_or_name)
            if not addr:
                return None
            self.data["users"][chat_id]["wallets"][addr] = new_name
            self.save()
            return addr

    def list_wallets(self, chat_id: str):
        """dict {address: name} milik SATU chat_id saja."""
        return dict(self.data.get("users", {}).get(str(chat_id), {}).get("wallets", {}))

    def get_all_tracked_wallets(self):
        """Semua wallet dari SEMUA user, digabung jadi satu set unik — dipakai
        loop scan utama supaya blockchain cuma di-scan sekali untuk semua
        orang (bukan berkali-kali per-user)."""
        seen = set()
        for user in self.data.get("users", {}).values():
            seen.update(user.get("wallets", {}).keys())
        return list(seen)

    def get_owners_of_wallet(self, address: str):
        """List chat_id yang memantau wallet ini — dipakai buat nentuin siapa
        SAJA yang harus dikirimi notifikasi (jadi wallet punya orang A tidak
        kekirim ke orang B, kecuali orang B juga menambahkan alamat yg sama)."""
        address = address.lower()
        return [cid for cid, u in self.data.get("users", {}).items()
                if address in u.get("wallets", {})]

    def get_wallet_name(self, chat_id: str, address: str):
        return self.data.get("users", {}).get(str(chat_id), {}).get("wallets", {}).get(address.lower())

    def total_users(self):
        return len(self.data.get("users", {}))

    # ── Posisi LP & mapping tokenId -> wallet (global, bukan per-user) ──────

    def add_wallet_token(self, wallet: str, protocol: str, token_id: int):
        with _lock:
            w = self.data["wallet_tokens"].setdefault(wallet, {"v3": [], "v4": []})
            if token_id not in w[protocol]:
                w[protocol].append(token_id)

    def claim_wallet_token(self, wallet: str, protocol: str, token_id: int) -> bool:
        """Cek-dan-daftarkan tokenId secara ATOMIK (satu operasi, di bawah lock
        yang sama) -- True kalau tokenId ini BARU pertama kali didaftarkan
        (pemanggil "menang", boleh lanjut kirim notifikasi), False kalau
        sudah ada duluan (pemanggil lain sudah/lagi nanganin -> JANGAN notify
        lagi, skip).

        Ini menggantikan pola lama (panggil wallet_owns_token() dulu buat
        cek, baru add_wallet_token() kalau belum ada) yang dilakukan sebagai
        DUA operasi terpisah -- rawan race condition kalau scan loop utama
        dan thread backfill (dari /addwallet) kebetulan cek tokenId yang
        sama di waktu yang HAMPIR bersamaan: dua-duanya bisa lolos cek
        "belum dikenal" sebelum salah satu sempat mendaftarkannya duluan,
        jadi dua-duanya kirim notifikasi utk event yang sama persis. Dengan
        method ini, cek + daftar terjadi di SATU langkah di bawah lock yang
        sama, jadi cuma satu pemanggil yang bisa "menang"."""
        with _lock:
            w = self.data["wallet_tokens"].setdefault(wallet, {"v3": [], "v4": []})
            if token_id in w[protocol]:
                return False
            w[protocol].append(token_id)
            return True

    def remove_wallet_token(self, wallet: str, protocol: str, token_id: int):
        with _lock:
            w = self.data["wallet_tokens"].get(wallet)
            if w and token_id in w[protocol]:
                w[protocol].remove(token_id)

    def wallet_owns_token(self, wallet: str, protocol: str, token_id: int) -> bool:
        w = self.data["wallet_tokens"].get(wallet)
        return bool(w and token_id in w[protocol])

    def find_wallet_for_token(self, protocol: str, token_id: int):
        for wallet, toks in self.data["wallet_tokens"].items():
            if token_id in toks.get(protocol, []):
                return wallet
        return None

    def set_position(self, key: str, position: dict):
        with _lock:
            self.data["positions"][key] = position

    def get_position(self, key: str):
        return self.data["positions"].get(key)
