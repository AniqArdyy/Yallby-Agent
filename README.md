# LP Wallet Tracker Bot (Telegram) — Multi-User

Bot Python yang memantau wallet-wallet pilihan kamu dan mengirim alert Telegram
tiap kali mereka melakukan aktivitas LP di Uniswap V3 (dan V4, best-effort) —
saat ini dikonfigurasi buat **Robinhood Chain** (Arbitrum Orbit L2, chain ID
4663), tapi bisa dipakai di chain EVM mana pun tinggal ganti `RPC_URL` +
alamat kontrak di `config.py`.

**Sekarang bot ini bisa dipakai rame-rame.** Siapa pun yang chat ke bot bisa
`/addwallet` wallet mereka sendiri, kasih nama sendiri, dan cuma dapat
notifikasi untuk wallet miliknya sendiri — wallet punya orang lain tidak
kelihatan dan tidak bisa diubah oleh orang lain.

## Yang berubah dari versi sebelumnya

- ✅ **Multi-user beneran.** Dulu cuma satu `TELEGRAM_CHAT_ID` yang boleh
  `/addwallet`; sekarang setiap chat_id Telegram punya daftar wallet sendiri.
- ✅ **Nama wallet.** `/addwallet 0x... NamaWallet` — dipakai di semua alert
  ("👛 Wallet: NamaWallet · 0x1234...abcd").
- ✅ **Bug diperbaiki: listener command tidak pernah jalan.** Di versi
  sebelumnya, `telegram_commands.py` (yang menangani `/addwallet` dkk) memang
  ada isinya, tapi **`monitor.py` tidak pernah memanggilnya** — jadi command
  itu sebenarnya tidak berfungsi sama sekali saat bot dijalankan.
- ✅ **Bug diperbaiki: wallet dari command tidak pernah kepakai.** Bahkan
  kalau listener-nya jalan, loop scan (`process_v3`/`process_v4`/`main`) di
  versi lama membaca `config.TRACKED_WALLETS` (daftar statis di file), BUKAN
  daftar wallet yang tersimpan di `state.py` — jadi wallet yang ditambah lewat
  `/addwallet` tidak akan pernah benar-benar dipantau.
- ✅ Format alert dirapikan: **Range harga mentah, Value (USD), dan saran
  preset "Zenith" dihapus.** Status in-range/out-of-range tetap ada.
- ✅ **Pool address & CA token dipindah ke bagian bawah** pesan, dan CA
  stablecoin/ETH/WETH tidak lagi ditampilkan berulang (cuma token "target").
- ✅ `requirements.txt` dirapikan (buang dependency `python-telegram-bot` yang
  sebenarnya tidak pernah dipakai — bot ini manggil Telegram lewat `requests`
  langsung).
- ➖ Fee USD cumulative diganti jadi cumulative dalam token asli (karena fitur
  Value/USD dihapus), bukan dihilangkan sama sekali.

## Yang dideteksi
- 🆕 New position (mint LP baru)
- ➕ Add liquidity
- ➖ Remove liquidity (partial / full close)
- 💰 Collect fees
- 📥📤 Transfer posisi (NFT LP masuk/keluar wallet)
- 📶 Status in-range / out-of-range
- 🏊 Pool address & 🪙 CA token target (di bagian bawah pesan, tap-copy friendly)
- 👛 Tambah/hapus/**ganti nama** wallet langsung dari chat Telegram — **milik
  masing-masing orang, tidak nyampur ke wallet orang lain**

## Setup

```bash
pip install -r requirements.txt
```

Isi environment variable (atau edit langsung `config.py`):

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."     # dari @BotFather
export RPC_URL="https://rpc.mainnet.chain.robinhood.com"   # atau provider berbayar
# opsional, lihat penjelasan di bawah:
export ADMIN_CHAT_ID="123456789"
```

Bot ini **tidak butuh `TELEGRAM_CHAT_ID`/`ADMIN_CHAT_ID` untuk bisa dipakai**
— begitu bot jalan, siapa pun tinggal chat bot-nya di Telegram dan
`/addwallet` wallet mereka sendiri. `ADMIN_CHAT_ID` cuma dipakai untuk dua hal
opsional:
1. Menerima notifikasi "🤖 Bot Aktif" tiap kali bot start.
2. Tujuan migrasi otomatis kalau kamu upgrade dari `lp_tracker_state.json`
   versi lama (single-user) yang sudah ada isinya — wallet lama otomatis
   dipindah jadi milik `ADMIN_CHAT_ID` ini, supaya tidak hilang.

Cara dapat chat ID (buat isi `ADMIN_CHAT_ID` kalau mau): chat bot kamu sekali
(apa aja, mis. `/start`), lalu buka
`https://api.telegram.org/bot<TOKEN>/getUpdates` dan cari field `chat.id`.

Alamat kontrak Uniswap di Robinhood Chain sudah diisi di `config.py`. Kalau
mau ganti chain, update `RPC_URL`, `CHAIN_ID`, dan 5 alamat kontrak
`UNISWAP_V3_*`/`UNISWAP_V4_*` sesuai chain tujuan (lihat komentar di
`config.py` buat cara verifikasi alamat resmi lewat dokumentasi Uniswap +
block explorer).

Jalankan:

```bash
python monitor.py
```

State (wallet siapa memantau apa & namanya, posisi yang sudah diketahui,
block terakhir yang diproses, offset command Telegram) otomatis disimpan ke
`lp_tracker_state.json` supaya tidak reset tiap restart.

## Manajemen wallet (multi-user, tanpa restart)

Chat langsung ke bot Telegram-nya — dari akun Telegram mana pun:

| Command | Fungsi |
|---|---|
| `/wallets` | Lihat wallet + nama yang **kamu** pantau |
| `/addwallet 0x... [nama]` | Tambah wallet ke daftar pantau **kamu** (nama opsional) |
| `/removewallet 0x...` atau `/removewallet nama` | Hapus wallet dari daftar **kamu** |
| `/renamewallet 0x... NamaBaru` atau `/renamewallet NamaLama NamaBaru` | Ganti nama wallet |
| `/help` | Tampilkan daftar command |

Setiap chat Telegram (setiap orang) punya daftar wallet **sendiri**. Kalau
Budi `/addwallet` wallet A dan Siti `/addwallet` wallet B, Budi tidak akan
pernah dikirimi notifikasi soal wallet B punya Siti, begitu juga sebaliknya —
masing-masing cuma lihat & atur daftarnya sendiri lewat `/wallets`.

Begitu wallet ditambah/dihapus/diganti nama, perubahan langsung kepakai di
scan berikutnya (maksimal nunggu satu siklus `POLL_INTERVAL_SECONDS`) — nggak
perlu restart proses bot sama sekali.

Ini jalan lewat listener terpisah (`telegram_commands.py`, long-polling
`getUpdates`) yang jalan di thread sendiri, paralel dengan loop scan
blockchain utama — jadi menambah wallet tidak akan memblok/menunda proses
scan yang sedang berjalan. **(Bug versi lama: thread ini sebelumnya tidak
pernah benar-benar dijalankan oleh `monitor.py` — sudah diperbaiki.)**

## Struktur file
- `monitor.py` — loop utama, logika deteksi event, & orkestrasi (sekarang juga
  menjalankan listener command Telegram)
- `config.py` — pengaturan chain, RPC, kontrak, Telegram, retry, dll
- `abis.py` — ABI minimal kontrak yang dipakai
- `uniswap_math.py` — cuma sisa `is_in_range()` (klasifikasi strategi & saran
  preset Zenith sudah dihapus)
- `state.py` — penyimpanan state lokal (JSON), **multi-user**, thread-safe,
  plus migrasi otomatis dari skema lama
- `price_feed.py` — sudah tidak dipakai lagi (fitur Value/USD dihapus), file
  disisakan kalau suatu saat mau diaktifkan lagi
- `telegram_notifier.py` — kirim pesan ke satu chat_id spesifik
- `telegram_commands.py` — listener `/addwallet` `/removewallet`
  `/renamewallet` `/wallets`, terbuka untuk siapa saja
- `rpc_retry.py` — retry+backoff buat panggilan RPC (mengatasi rate-limit 429)

## Batasan & hal yang perlu kamu tahu

1. **Uniswap V4 masih best-effort**, meski sudah cukup lengkap:
   - Arsitektur V4 itu *singleton* — event `ModifyLiquidity` di `PoolManager`
     cuma mencatat `msg.sender` (biasanya `PositionManager`, bukan wallet
     asli). Bot mengatasi ini lewat event `Transfer` NFT di
     `PositionManager` V4 + korelasi tx hash.
   - Detail pool (currency0/1, fee, hooks) diambil dari event `Initialize`
     `PoolManager`, di-scan dari `V4_POOL_MANAGER_DEPLOY_BLOCK` (default 0).
     Kalau chain-nya sudah sangat panjang umur, set config ini ke block
     deploy PoolManager yang sebenarnya biar pencarian nggak lambat.
   - Kalau event `Initialize` untuk suatu pool belum ketemu, bot **bilang
     jujur** kenapa (bukan ngirim pesan kosong) — cek pesan alert-nya.

2. **Cara kerja scan-nya "brute force" (polling `eth_getLogs`)**, bukan pakai
   indexer:
   - Event `IncreaseLiquidity` / `DecreaseLiquidity` / `Collect` di-scan untuk
     **semua** pool V3 (event-nya tidak menyimpan wallet), lalu difilter
     berdasarkan `tokenId` yang sudah dikenal bot. Kalau sebuah wallet sudah
     punya posisi LP **sebelum** ditambahkan lewat `/addwallet`, bot baru
     "kenal" tokenId itu setelah lihat event `Transfer` masuknya dalam
     rentang `LOOKBACK_BLOCKS_ON_START` (dihitung dari SAAT WALLET
     DITAMBAHKAN, bukan dari start bot pertama kali). Perbesar
     `LOOKBACK_BLOCKS_ON_START` di `config.py` kalau perlu cakupan lebih jauh.
   - RPC publik biasanya rate-limited — bot sudah pakai retry+backoff
     (`rpc_retry.py`, config `RPC_MAX_RETRIES` dkk) dan jeda antar-chunk
     (`CHUNK_SLEEP_SECONDS`), tapi untuk pemakaian serius tetap disarankan
     pakai RPC provider berbayar yang sudah mendukung chain target.

3. **Karena bot ini publik/multi-user, siapa pun yang tahu username bot bisa
   `/addwallet` wallet APA SAJA** (bukan cuma wallet miliknya sendiri secara
   kriptografis — tidak ada verifikasi kepemilikan wallet, bot cuma memantau
   alamat publik). Ini wajar untuk tracker LP (semua data yang dipantau
   memang publik di blockchain), tapi kalau kamu mau membatasi siapa saja
   yang boleh pakai bot, kamu perlu menambah pengecekan whitelist chat_id
   sendiri di `telegram_commands.py` (tidak disediakan di versi ini karena
   permintaannya justru sebaliknya: dibuka untuk semua orang).

## Contoh alert yang dikirim (format terbaru, sudah dirapikan)

```
🆕 New Liquidity Position
──────────────────────
👛 Wallet   : Dompet Utama · 0x1234...abcd
🔗 Pair     : USDG/ORBIO · Uniswap V3
📌 Fee Tier : 1.00%
🆔 Token ID : 891234

📶 Status   : 🟢 In-Range

💰 Amount   : 1,205.30 USDG + 38.75 ORBIO

──────────────────────
🏊 Pool : 0x75Ee1234...9f902e9f902e...(alamat lengkap)
🪙 CA ORBIO : 0xAbCd...ef12 (alamat lengkap)
```

Catatan soal format ini:
- **Range harga mentah, Value (USD), Strategy, dan saran preset "Zenith"
  sudah dihapus** — pesan sekarang fokus ke fakta on-chain: pair, fee tier,
  status in/out-of-range, dan jumlah token asli.
- **Nama wallet ditampilkan sesuai nama yang KAMU kasih** lewat
  `/addwallet`/`/renamewallet` — kalau ada dua orang memantau alamat yang
  sama dengan nama berbeda, masing-masing tetap lihat nama versinya sendiri.
- **Pool address & CA token dipindah ke bagian bawah**, ditulis lengkap
  (tidak dipotong) di dalam `<code>` biar gampang di-tap-copy langsung dari
  Telegram. **CA cuma ditampilkan untuk token yang BUKAN stablecoin/ETH/WETH**
  (USDG, USDC, WETH, ETH dkk tidak diulang-ulang tiap alert).
- Pesan V4 formatnya sama dengan V3 (pair, fee, status, amount, hooks kalau
  ada, CA), dengan Pool ID menggantikan Pool address di bagian bawah.

## Menjalankan lewat Termius (dari HP)

Termius itu SSH client — jadi dia bukan tempat menjalankan bot, melainkan
"remote control" ke server (VPS) tempat bot benar-benar jalan 24/7. Alurnya:

**1. Siapkan VPS kecil**
Sewa VPS murah (DigitalOcean, Vultr, Contabo, dll — spek 1 vCPU/1GB RAM sudah
cukup), OS Ubuntu 22.04/24.04.

**2. Tambah koneksi di Termius**
- Buka Termius → `Hosts` → `+ New Host`
- Isi `Address` (IP VPS), `Username` (biasanya `root`), lalu masukkan
  password atau key SSH
- Tap host tersebut untuk connect (terminal langsung terbuka di HP)

**3. Install Python & upload file**
Di terminal Termius:
```bash
apt update && apt install -y python3 python3-pip screen
```
Upload folder bot ke VPS — paling gampang lewat fitur **SFTP** di Termius
(icon folder di sidebar saat host terkoneksi, drag & drop file dari HP), atau
`git clone` kalau kamu taruh di repo, atau `scp` dari laptop.

**4. Install dependency & isi konfigurasi**
```bash
cd lp_tracker_bot
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="isi_token_kamu"
export RPC_URL="isi_rpc_url_kamu"
# opsional:
export ADMIN_CHAT_ID="isi_chat_id_kamu"
```
Supaya tidak perlu export ulang tiap login, taruh baris `export` di atas ke
`~/.bashrc`, atau edit langsung nilainya di `config.py`.

**5. Jalankan biar tetap hidup walau HP/Termius ditutup**
Pakai `screen` (paling simpel):
```bash
screen -S lpbot
python3 monitor.py
# tekan Ctrl+A lalu D untuk detach (bot tetap jalan di background)
```
Untuk cek lagi nanti: `screen -r lpbot`.

Alternatif lebih robust — pakai `systemd` supaya bot auto-restart kalau
crash/VPS reboot:
```bash
cat <<'EOF' > /etc/systemd/system/lpbot.service
[Unit]
Description=LP Tracker Bot
After=network.target

[Service]
WorkingDirectory=/root/lp_tracker_bot
ExecStart=/usr/bin/python3 monitor.py
Restart=always
Environment=TELEGRAM_BOT_TOKEN=isi_token_kamu
Environment=RPC_URL=isi_rpc_url_kamu
Environment=ADMIN_CHAT_ID=isi_chat_id_kamu

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now lpbot
systemctl status lpbot     # cek statusnya
journalctl -u lpbot -f     # lihat log real-time
```
Dengan `systemd`, kamu bisa tutup Termius kapan saja — bot tetap jalan di
VPS. Buka Termius lagi nanti cuma buat cek log atau restart kalau perlu.
Nambah/hapus/rename wallet juga nggak perlu buka Termius sama sekali lagi —
tinggal chat `/addwallet`, `/removewallet`, atau `/renamewallet` ke bot
Telegram-nya langsung dari HP, dan siapa pun yang kamu kasih tahu username
bot-nya bisa melakukan hal yang sama untuk wallet mereka sendiri.

Tips Termius lain yang berguna: fitur **Snippets** (simpan command yang sering
dipakai, misal `journalctl -u lpbot -f`, biar tinggal tap) dan **Port
Forwarding** (tidak dibutuhkan untuk bot ini, karena semua komunikasi keluar
lewat HTTPS ke RPC & Telegram).

## Deploy 24/7 (ringkasan)
Bot ini polling loop sederhana — yang penting dijalankan di server yang hidup
terus (VPS), bukan di laptop/HP yang sering mati. Gunakan `systemd` (lihat di
atas) untuk keandalan terbaik.
