"""
Konfigurasi bot. Isi lewat environment variable, atau edit langsung nilai default
di bawah untuk testing cepat.
"""
import os

# ── Telegram ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "ISI_SENDIRI")

# Bot ini MULTI-USER & TERBUKA: siapa pun yang chat ke bot bisa /addwallet
# wallet mereka sendiri (lihat telegram_commands.py) tanpa perlu ID di bawah
# ini. ADMIN_CHAT_ID sekarang OPSIONAL, cuma dipakai untuk:
#   1) nerima notifikasi "🤖 Bot Aktif" pas bot pertama kali start, dan
#   2) tujuan migrasi otomatis wallet lama kalau kamu upgrade dari
#      lp_tracker_state.json versi single-user sebelumnya (lihat state.py).
# Boleh dikosongkan (hapus env var-nya / set string kosong) kalau tidak perlu.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "6747473870")) or None

# ── Chain: Robinhood Chain (Arbitrum Orbit L2, chainId 4663, gas token ETH) ─
# RPC publik resmi Robinhood (gratis, tapi rate-limited — cocok utk testing;
# untuk produksi pakai provider berbayar spt Alchemy/QuickNode yg sudah
# mendukung Robinhood Chain, biar eth_getLogs range-nya lebih lega).
CHAIN_ID = int(os.environ.get("CHAIN_ID", "4663"))
RPC_URL = os.environ.get("RPC_URL", "https://rpc.mainnet.chain.robinhood.com")

# ── Wallet yang dipantau ────────────────────────────────────────────────
# TIDAK ADA LAGI daftar wallet hardcoded di sini — bot sekarang murni
# multi-user, wallet dikelola tiap orang lewat /addwallet /removewallet
# /renamewallet /wallets langsung di Telegram (lihat state.py & README).
# Wallet yang sudah kepantau sebelumnya (lp_tracker_state.json versi lama)
# otomatis dimigrasikan ke ADMIN_CHAT_ID di atas saat bot pertama kali start.

# ── Polling ─────────────────────────────────────────────────────────────
# Dinaikkan dari 15 -> 20 detik: RPC publik Robinhood rate-limit-nya ketat,
# jadi polling terlalu rapat cuma bikin bot lebih sering kena 429.
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "20"))
# Berapa block mundur saat start pertama kali (kalau tidak ada state tersimpan)
LOOKBACK_BLOCKS_ON_START = int(os.environ.get("LOOKBACK_BLOCKS_ON_START", "7200"))  # ~1 hari
# Berapa block mundur saat ADA WALLET BARU DITAMBAHKAN lewat /addwallet, buat
# cari posisi LP yang SUDAH ADA sebelum wallet itu ditambahkan (bug lama: ini
# cuma diklaim di README tapi tidak pernah benar2 dijalankan -> posisi lama
# tidak pernah "dikenal" bot -> tidak ada alert Add/Remove/Collect untuk
# posisi itu selamanya). Default lebih besar dari LOOKBACK_BLOCKS_ON_START
# karena posisi existing bisa jauh lebih tua dari histori startup biasa.
WALLET_ADD_LOOKBACK_BLOCKS = int(os.environ.get("WALLET_ADD_LOOKBACK_BLOCKS", "200000"))
# Batas block per panggilan eth_getLogs (sesuaikan dengan limit provider RPC kamu)
BLOCK_CHUNK_SIZE = int(os.environ.get("BLOCK_CHUNK_SIZE", "2000"))
# Jeda antar chunk block saat catch-up (mengurangi beban ke RPC publik)
CHUNK_SLEEP_SECONDS = float(os.environ.get("CHUNK_SLEEP_SECONDS", "1.0"))

# ── Retry / backoff RPC (mengatasi 429 Too Many Requests di RPC publik) ────
RPC_MAX_RETRIES = int(os.environ.get("RPC_MAX_RETRIES", "5"))
RPC_BACKOFF_BASE_SECONDS = float(os.environ.get("RPC_BACKOFF_BASE_SECONDS", "2"))
RPC_BACKOFF_MAX_SECONDS = float(os.environ.get("RPC_BACKOFF_MAX_SECONDS", "120"))

# ── Lock file (cegah 2+ instance bot jalan bersamaan -> notifikasi dobel) ──
LOCK_FILE = os.environ.get("LOCK_FILE", "lp_tracker_bot.lock")

# ── Kontrak Uniswap V3/V4 di ROBINHOOD CHAIN ───────────────────────────
# ⚠️ BELUM DIISI. Uniswap v2/v3/v4 memang sudah live di Robinhood Chain,
# TAPI alamat kontraknya BEDA dari Ethereum mainnet (alamat lama di bawah
# ini sengaja dikomentari, JANGAN dipakai di chain ini) — dan saya tidak
# berhasil menarik tabel alamat resminya secara otomatis (halamannya
# render lewat JS, jadi tool fetch saya cuma dapat teks kosong di sekitar
# tabel). Isi manual dari sumber resmi di bawah sebelum bot dijalankan:
#
#   1) Uniswap (resmi)  : https://docs.uniswap.org/contracts/v3/reference/deployments/robinhood-chain-deployments
#                          https://docs.uniswap.org/contracts/v4/deployments  (cari baris "Robinhood Chain")
#   2) Robinhood (resmi): https://docs.robinhood.com/chain/protocol-contracts
#   3) Cross-check      : https://robinhoodchain.blockscout.com  (pastikan address itu
#                          "Contract" terverifikasi dgn nama UniswapV3Factory dst,
#                          bukan sekadar wallet/proxy kosong)
#
# UNISWAP_V3_NFPM = "0x..."       # NonfungiblePositionManager
# UNISWAP_V3_FACTORY = "0x..."    # UniswapV3Factory
# UNISWAP_V4_POOL_MANAGER = "0x..."
# UNISWAP_V4_POSITION_MANAGER = "0x..."
# UNISWAP_V4_STATE_VIEW = "0x..."
#
# (Alamat Ethereum mainnet lama, TIDAK berlaku di Robinhood Chain — disimpan
#  cuma buat referensi kalau kamu mau balik pantau di mainnet lagi:)
# UNISWAP_V3_NFPM_ETH_MAINNET = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
# UNISWAP_V3_FACTORY_ETH_MAINNET = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
# UNISWAP_V4_POOL_MANAGER_ETH_MAINNET = "0x000000000004444c5dc75cB358380D2e3dE08A90"
# UNISWAP_V4_POSITION_MANAGER_ETH_MAINNET = "0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e"
# UNISWAP_V4_STATE_VIEW_ETH_MAINNET = "0x7fFE42C4a5DEeA5b0feC41C94C136Cf115597227"

UNISWAP_V3_NFPM = os.environ.get(
    "UNISWAP_V3_NFPM",
    "0x73991a25c818bf1f1128deaab1492d45638de0d3"
)

UNISWAP_V3_FACTORY = os.environ.get(
    "UNISWAP_V3_FACTORY",
    "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
)

UNISWAP_V4_POOL_MANAGER = os.environ.get(
    "UNISWAP_V4_POOL_MANAGER",
    "0x8366a39cc670b4001a1121b8f6a443a643e40951"
)

UNISWAP_V4_POSITION_MANAGER = os.environ.get(
    "UNISWAP_V4_POSITION_MANAGER",
    "0x58daec3116aae6d93017baaea7749052e8a04fa7"
)

UNISWAP_V4_STATE_VIEW = os.environ.get(
    "UNISWAP_V4_STATE_VIEW",
    "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"
)

# Block deploy PoolManager V4 -- dipakai sbg titik awal scan event `Initialize`
# (lihat get_v4_pool_info() di monitor.py). Default 0 = scan dari genesis,
# yang AMAN tapi lambat di chain yang sudah panjang umur. Kalau tau block
# deploy PoolManager V4 di Robinhood Chain yang sebenarnya, isi di sini biar
# scan-nya jauh lebih cepat dan nggak keliatan "Belum nemu event Initialize"
# terus-menerus di alert. Cek di block explorer: cari tx deploy kontrak
# UNISWAP_V4_POOL_MANAGER di atas, block number-nya itu yang diisi ke sini.
V4_POOL_MANAGER_DEPLOY_BLOCK = int(os.environ.get("V4_POOL_MANAGER_DEPLOY_BLOCK", "0"))

# ── USD pricing (opsional, pakai CoinGecko) ────────────────────────────
USE_COINGECKO_FOR_USD = os.environ.get("USE_COINGECKO_FOR_USD", "true").lower() == "true"
# Stablecoin utama di Robinhood Chain saat ini adalah USDG (bukan USDC Circle).
STABLECOINS = {"usdg", "usdc", "usdt", "dai", "usde", "fdusd", "tusd", "pyusd"}
# WETH di Robinhood Chain mainnet (dikonfirmasi via 1inch help center + repo
# integrasi pihak ketiga — silakan cross-check sekali lagi di Blockscout).
WETH_ADDRESS = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73".lower()

STATE_FILE = os.environ.get("STATE_FILE", "lp_tracker_state.json")
