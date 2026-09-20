"""
LP Wallet Tracker Bot — Uniswap V3 (penuh) + V4 (best-effort/experimental).
MULTI-USER: siapa pun bisa /addwallet wallet mereka sendiri lewat Telegram,
tanpa nyampur ke daftar wallet orang lain (lihat state.py & telegram_commands.py).

Alur:
  1. Poll block baru tiap POLL_INTERVAL_SECONDS.
  2. Scan event Transfer di NFPM V3 / PositionManager V4 untuk deteksi posisi baru
     yang masuk ke salah satu wallet yang dipantau (gabungan semua user).
  3. Scan event IncreaseLiquidity / DecreaseLiquidity / Collect (V3) dan
     ModifyLiquidity (V4), lalu cocokkan tokenId/poolId ke wallet yang sudah
     diketahui lewat state lokal.
  4. Hitung status in/out-of-range & jumlah token, lalu kirim notifikasi
     Telegram HANYA ke chat_id yang benar-benar memantau wallet tsb.

CATATAN JUJUR:
  - Value USD, Strategy, dan saran preset Zenith SUDAH DIHAPUS dari pesan
    (permintaan pemilik bot) — bot cuma melaporkan fakta on-chain: pair, fee,
    status in/out-of-range, dan jumlah token asli.
  - Dukungan V4 masih best-effort: arsitektur singleton-nya bikin atribusi
    wallet -> event lebih rumit dibanding V3. Cek README untuk detail & apa
    yang perlu diverifikasi ulang sebelum dipakai serius.
"""
import os
import sys
import threading
import time

from web3 import Web3
from web3.logs import IGNORE as W3_LOG_IGNORE

import config
from abis import (
    ERC20_ABI, V3_NFPM_ABI, V3_FACTORY_ABI, V3_POOL_ABI,
    V4_POOL_MANAGER_ABI, ERC721_TRANSFER_ABI, V4_STATE_VIEW_ABI, V3_SWAP_TOPIC0,
)
from state import BotState
from telegram_notifier import send_message, short
from telegram_commands import poll_commands_forever
from uniswap_math import is_in_range
from rpc_retry import call_with_retry

w3 = Web3(Web3.HTTPProvider(config.RPC_URL))

nfpm = w3.eth.contract(address=Web3.to_checksum_address(config.UNISWAP_V3_NFPM), abi=V3_NFPM_ABI)
factory = w3.eth.contract(address=Web3.to_checksum_address(config.UNISWAP_V3_FACTORY), abi=V3_FACTORY_ABI)
v4_pool_manager = w3.eth.contract(address=Web3.to_checksum_address(config.UNISWAP_V4_POOL_MANAGER), abi=V4_POOL_MANAGER_ABI)
v4_position_manager = w3.eth.contract(address=Web3.to_checksum_address(config.UNISWAP_V4_POSITION_MANAGER), abi=ERC721_TRANSFER_ABI)
v4_state_view = w3.eth.contract(address=Web3.to_checksum_address(config.UNISWAP_V4_STATE_VIEW), abi=V4_STATE_VIEW_ABI)

state = BotState(config.STATE_FILE)
_erc20_cache = {}
_pool_cache = {}
ZERO_ADDR = "0x0000000000000000000000000000000000000000"
DIVIDER = "─" * 22
DYNAMIC_FEE_FLAG = 0x800000  # penanda "fee dinamis" di PoolKey V4 -> fee asli ada di slot0.lpFee


def rpc(fn, *args, label="rpc_call", **kwargs):
    """Bungkus panggilan RPC pakai retry+backoff (lihat rpc_retry.py).
    Dipakai di semua tempat yang manggil node (get_logs, .call(), receipt, dst)
    supaya satu RPC publik yang lagi rate-limit (429) tidak bikin seluruh bot
    macet/hang — kalau tetap gagal setelah beberapa kali coba, exception
    dilempar ke pemanggil supaya block/wallet lain tetap bisa diproses."""
    return call_with_retry(
        fn, *args,
        max_retries=config.RPC_MAX_RETRIES,
        base_delay=config.RPC_BACKOFF_BASE_SECONDS,
        max_delay=config.RPC_BACKOFF_MAX_SECONDS,
        label=label,
        **kwargs,
    )


def filter_valid_logs(raw_logs, label="logs"):
    """Jaga-jaga: kadang satu entri dari eth_getLogs gagal ter-decode penuh
    jadi objek event (harusnya punya .args) dan malah balik sebagai dict log
    mentah (observasi nyata di produksi, khususnya lewat RPC publik). Tanpa
    filter ini, satu entri "aneh" begini bikin exception .args yang
    menggagalkan SELURUH siklus scan (semua wallet, bukan cuma satu log).
    Entri yang tidak valid di-skip + di-print detailnya (untuk diagnosis),
    entri yang valid tetap diproses normal."""
    clean = []
    for entry in raw_logs:
        if hasattr(entry, "args"):
            clean.append(entry)
        else:
            try:
                detail = dict(entry) if hasattr(entry, "keys") else repr(entry)
            except Exception:
                detail = repr(entry)
            print(f"[filter_valid_logs] {label}: skip 1 log tanpa .args (kemungkinan gagal decode) -> {detail}")
    return clean


def acquire_single_instance_lock():
    """Cegah 2+ proses bot jalan bersamaan di server yang sama (penyebab umum
    notifikasi '🤖 LP Tracker Bot Aktif' terkirim berkali-kali ke Telegram
    kalau bot di-restart manual/berulang tanpa mematikan proses lama dulu)."""
    lock_path = config.LOCK_FILE
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)  # cek proses masih hidup? (tidak benar2 kirim signal)
            print(f"[lock] Bot sudah jalan di PID {old_pid} (lock: {lock_path}). "
                  f"Hentikan proses lama dulu atau hapus file lock kalau itu proses zombie.")
            sys.exit(1)
        except (ValueError, OSError):
            pass  # lock basi (proses lama sudah mati) -> boleh dilanjut, ditimpa di bawah
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))


def release_single_instance_lock():
    try:
        os.remove(config.LOCK_FILE)
    except OSError:
        pass


# ───────────────────────── Helpers umum ─────────────────────────

def erc20_meta(address: str):
    address = Web3.to_checksum_address(address)
    if address in _erc20_cache:
        return _erc20_cache[address]
    c = w3.eth.contract(address=address, abi=ERC20_ABI)
    try:
        symbol = rpc(c.functions.symbol().call, label=f"symbol({address})")
    except Exception:
        symbol = address[:6]
    try:
        decimals = rpc(c.functions.decimals().call, label=f"decimals({address})")
    except Exception:
        decimals = 18
    meta = {"symbol": symbol, "decimals": decimals}
    _erc20_cache[address] = meta
    return meta


def v4_currency_meta(address: str):
    """Kayak erc20_meta, tapi paham currency native V4 (address(0) = ETH) —
    di V4 salah satu sisi pool boleh native currency, bukan wrapped ERC20,
    dan address(0) itu bukan kontrak jadi erc20_meta() biasa bakal gagal."""
    if address.lower() == ZERO_ADDR.lower():
        return {"symbol": "ETH", "decimals": 18, "address": config.WETH_ADDRESS}
    meta = erc20_meta(address)
    return {**meta, "address": address.lower()}


def is_common_token(symbol: str, address: str) -> bool:
    """Token 'umum' yang CA-nya nggak perlu ditampilkan berulang tiap alert
    (stablecoin & ETH/WETH) — biar pesan tetap rapi & fokus ke token target."""
    if symbol.lower() in config.STABLECOINS:
        return True
    if address and address.lower() in (config.WETH_ADDRESS.lower(), ZERO_ADDR.lower()):
        return True
    if symbol.upper() in ("ETH", "WETH"):
        return True
    return False


def ca_lines(*token_metas):
    """Baris CA (contract address) untuk token yang BUKAN stablecoin/ETH/WETH
    saja — dipanggil di bagian bawah pesan biar gampang di-tap-copy."""
    lines = []
    for meta in token_metas:
        if not is_common_token(meta["symbol"], meta.get("address", "")):
            lines.append(f"🪙 CA {meta['symbol']} : <code>{meta['address']}</code>")
    return lines


_v4_pool_info_cache = {}
_v4_pool_info_missing = set()  # poolId yang sudah dicari tapi Initialize-nya belum ketemu


def get_v4_pool_info(pool_id_bytes):
    """Cari currency0/1, fee, tickSpacing, hooks sebuah pool V4 dari event
    `Initialize` di PoolManager (singleton V4 tidak punya kontrak per-pool
    kayak V3, jadi detail pool cuma bisa didapat dari event ini, bukan call
    langsung ke suatu 'pool contract'). Di-cache per poolId biar nggak
    scan ulang tiap kali dipanggil. `pool_id_bytes` harus bytes32 mentah
    (bukan string hex) karena itu yang dibutuhkan web3 buat filter topic."""
    pool_id_hex = pool_id_bytes.hex()
    if pool_id_hex in _v4_pool_info_cache:
        return _v4_pool_info_cache[pool_id_hex]
    if pool_id_hex in _v4_pool_info_missing:
        return None
    try:
        logs = filter_valid_logs(rpc(
            v4_pool_manager.events.Initialize().get_logs,
            from_block=config.V4_POOL_MANAGER_DEPLOY_BLOCK, to_block="latest",
            argument_filters={"id": pool_id_bytes},
            label=f"v4.Initialize(id={pool_id_hex[:10]}...)",
        ), label=f"v4.Initialize(id={pool_id_hex[:10]}...)")
    except Exception as e:
        print(f"[v4] gagal cari Initialize utk pool {pool_id_hex[:10]}...:", e)
        return None
    if not logs:
        _v4_pool_info_missing.add(pool_id_hex)
        return None
    ev = logs[0]
    info = {
        "currency0": ev.args["currency0"],
        "currency1": ev.args["currency1"],
        "fee": ev.args["fee"],
        "tick_spacing": ev.args["tickSpacing"],
        "hooks": ev.args["hooks"],
    }
    _v4_pool_info_cache[pool_id_hex] = info
    return info


def v3_pool_and_tick(token0: str, token1: str, fee: int):
    key = (token0.lower(), token1.lower(), fee)
    if key in _pool_cache:
        pool_addr = _pool_cache[key]
    else:
        pool_addr = rpc(
            factory.functions.getPool(
                Web3.to_checksum_address(token0), Web3.to_checksum_address(token1), fee
            ).call,
            label="factory.getPool",
        )
        _pool_cache[key] = pool_addr
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_addr), abi=V3_POOL_ABI)
    slot0 = rpc(pool.functions.slot0().call, label=f"slot0({pool_addr})")
    return pool_addr, slot0[0], slot0[1]  # pool_addr, sqrtPriceX96, tick


def tx_has_swap(tx_hash) -> bool:
    """True kalau tx yang sama juga punya event Swap (indikasi funding lewat
    swap dulu baru deposit). Dipakai internal saja sekarang (tidak lagi buat
    saran preset apa pun), tapi tetap berguna untuk debugging kalau perlu."""
    try:
        receipt = rpc(w3.eth.get_transaction_receipt, tx_hash, label="get_transaction_receipt")
        for log in receipt.logs:
            if log.topics and log.topics[0].hex().lower() == V3_SWAP_TOPIC0.lower():
                return True
        return False
    except Exception:
        return False


def amounts_from_liquidity(liquidity: int, tick_current: int, tick_lower: int, tick_upper: int,
                            decimals0: int, decimals1: int):
    """Formula standar Uniswap V3 buat balikin (amount0_human, amount1_human)."""
    sqrt_lower = 1.0001 ** (tick_lower / 2)
    sqrt_upper = 1.0001 ** (tick_upper / 2)
    sqrt_current = 1.0001 ** (tick_current / 2)

    L = float(liquidity)
    if tick_current < tick_lower:
        amount0 = L * (1 / sqrt_lower - 1 / sqrt_upper)
        amount1 = 0.0
    elif tick_current >= tick_upper:
        amount0 = 0.0
        amount1 = L * (sqrt_upper - sqrt_lower)
    else:
        amount0 = L * (1 / sqrt_current - 1 / sqrt_upper)
        amount1 = L * (sqrt_current - sqrt_lower)

    return amount0 / (10 ** decimals0), amount1 / (10 ** decimals1)


def fmt(n, max_dec=4):
    if n is None:
        return "N/A"
    if abs(n) >= 1000:
        return f"{n:,.2f}"
    return f"{n:,.{max_dec}f}"


# ───────────────────────── Notifikasi (per-owner, bukan broadcast) ─────────

def notify(wallet: str, title: str, body_lines: list):
    """Kirim notifikasi HANYA ke chat_id yang benar-benar memantau `wallet`
    ini (lihat state.get_owners_of_wallet). Nama wallet yang ditampilkan juga
    diambil per-chat_id, karena tiap orang boleh kasih nama beda untuk alamat
    yang sama. Kalau wallet ini sudah tidak dipantau siapa pun (mis. baru saja
    di-/removewallet oleh satu-satunya pemiliknya), tidak ada yang dikirimi."""
    owners = state.get_owners_of_wallet(wallet)
    if not owners:
        return
    for chat_id in owners:
        name = state.get_wallet_name(chat_id, wallet)
        wallet_label = f"{name} · <code>{short(wallet)}</code>" if name else f"<code>{short(wallet)}</code>"
        lines = [f"<b>{title}</b>", DIVIDER, f"👛 Wallet   : {wallet_label}"] + body_lines
        send_message(chat_id, "\n".join(lines))


# ───────────────────────── Pesan builders (V3) ─────────────────────────

def build_v3_body(token0_meta, token1_meta, fee: int, tick_lower: int, tick_upper: int,
                   current_tick: int, liquidity: int, pool_addr: str, token_id: int,
                   extra_lines=None):
    """Bangun isi pesan V3 (tanpa title/divider/wallet — itu ditambah oleh
    notify() per-penerima). Range harga mentah, Value USD, dan Strategy sudah
    DIHAPUS dari sini; yang tetap ada cuma status in/out-of-range dan jumlah
    token asli. Pool address & CA token dipindah ke bagian BAWAH pesan."""
    in_range = is_in_range(current_tick, tick_lower, tick_upper)
    status = "🟢 In-Range" if in_range else "🟠 Out-of-Range"
    amt0, amt1 = amounts_from_liquidity(liquidity, current_tick, tick_lower, tick_upper,
                                         token0_meta["decimals"], token1_meta["decimals"])

    lines = [
        f"🔗 Pair     : {token0_meta['symbol']}/{token1_meta['symbol']} · Uniswap V3",
        f"📌 Fee Tier : {fee/10000:.2f}%",
        f"🆔 Token ID : {token_id}",
        "",
        f"📶 Status   : {status}",
        "",
        f"💰 Amount   : {fmt(amt0)} {token0_meta['symbol']} + {fmt(amt1)} {token1_meta['symbol']}",
    ]
    if extra_lines:
        lines.append("")
        lines.extend(extra_lines)
    lines.append("")
    lines.append(DIVIDER)
    lines.append(f"🏊 Pool     : <code>{pool_addr}</code>")
    lines.extend(ca_lines(token0_meta, token1_meta))
    return lines, amt0, amt1


def build_v4_body(token0_meta, token1_meta, pool_fee: int, lp_fee: int, hooks: str,
                   tick_lower: int, tick_upper: int, current_tick: int, liquidity: int,
                   pool_id_hex: str, token_id: int):
    effective_fee = lp_fee if pool_fee == DYNAMIC_FEE_FLAG else pool_fee
    fee_label = f"{effective_fee/10000:.2f}%" + (" (dinamis)" if pool_fee == DYNAMIC_FEE_FLAG else "")

    in_range = is_in_range(current_tick, tick_lower, tick_upper)
    status = "🟢 In-Range" if in_range else "🟠 Out-of-Range"
    amt0, amt1 = amounts_from_liquidity(liquidity, current_tick, tick_lower, tick_upper,
                                         token0_meta["decimals"], token1_meta["decimals"])

    lines = [
        f"🔗 Pair     : {token0_meta['symbol']}/{token1_meta['symbol']} · Uniswap V4",
        f"📌 Fee Tier : {fee_label}",
        f"🆔 Token ID : {token_id}",
    ]
    if hooks and hooks.lower() != ZERO_ADDR.lower():
        lines.append(f"🪝 Hooks    : <code>{short(hooks)}</code>")
    lines += [
        "",
        f"📶 Status   : {status}",
        "",
        f"💰 Amount   : {fmt(amt0)} {token0_meta['symbol']} + {fmt(amt1)} {token1_meta['symbol']}",
        "",
        DIVIDER,
        f"🔑 Pool ID  : <code>0x{pool_id_hex}</code>",
    ]
    lines.extend(ca_lines(token0_meta, token1_meta))
    return lines, amt0, amt1


# ───────────────────────── Core scan V3 ─────────────────────────

def process_v3(from_block: int, to_block: int):
    tracked = state.get_all_tracked_wallets()
    if not tracked:
        return  # belum ada satu pun user yang /addwallet -> tidak ada yg discan
    checksum_wallets = [Web3.to_checksum_address(w) for w in tracked]

    # 1) Transfer -> deteksi posisi baru / posisi keluar dari wallet
    transfer_in = filter_valid_logs(rpc(
        nfpm.events.Transfer().get_logs,
        from_block=from_block, to_block=to_block, argument_filters={"to": checksum_wallets},
        label="nfpm.Transfer(to=wallets)",
    ), label="nfpm.Transfer(to=wallets)")
    transfer_out = filter_valid_logs(rpc(
        nfpm.events.Transfer().get_logs,
        from_block=from_block, to_block=to_block, argument_filters={"from": checksum_wallets},
        label="nfpm.Transfer(from=wallets)",
    ), label="nfpm.Transfer(from=wallets)")

    for log in transfer_in:
        wallet = log.args["to"].lower()
        token_id = log.args["tokenId"]
        is_mint = log.args["from"] == ZERO_ADDR
        if not state.claim_wallet_token(wallet, "v3", token_id):
            continue  # sudah dikenal (atomik) -> jangan notif dobel

        try:
            pos = rpc(nfpm.functions.positions(token_id).call, label=f"nfpm.positions({token_id})")
        except Exception as e:
            # Posisi ini sudah ditutup & NFT-nya di-burn sebelum bot sempat proses
            # (umum kejadian pas catch-up histori LOOKBACK_BLOCKS_ON_START) —
            # skip token ini saja, jangan sampai nge-hang seluruh proses block range.
            print(f"[v3] skip token {token_id}: positions() gagal (kemungkinan sudah closed/burned): {e}")
            pos_state = state.get_position(f"v3:{token_id}") or {}
            pos_state["status"] = "closed"
            state.set_position(f"v3:{token_id}", pos_state)
            continue
        (_, _, token0, token1, fee, tick_lower, tick_upper, liquidity, _, _, _, _) = pos
        t0meta = {**erc20_meta(token0), "address": token0}
        t1meta = {**erc20_meta(token1), "address": token1}
        pool_addr, _, current_tick = v3_pool_and_tick(token0, token1, fee)

        title = "🆕 New Liquidity Position" if is_mint else "📥 Position Transferred In"
        body, amt0, amt1 = build_v3_body(t0meta, t1meta, fee, tick_lower, tick_upper,
                                          current_tick, liquidity, pool_addr, token_id)
        notify(wallet, title, body)
        state.set_position(f"v3:{token_id}", {
            "wallet": wallet, "token0": token0, "token1": token1, "fee": fee,
            "tick_lower": tick_lower, "tick_upper": tick_upper,
            "entry_amount0": amt0, "entry_amount1": amt1,
            "fees_collected": {}, "status": "open",
        })

    for log in transfer_out:
        wallet = log.args["from"].lower()
        token_id = log.args["tokenId"]
        state.remove_wallet_token(wallet, "v3", token_id)
        if log.args["to"] != ZERO_ADDR:  # bukan burn, tapi dipindah ke address lain
            notify(wallet, "📤 Position Transferred Out", [
                f"🆔 Token ID: {token_id}",
                f"➡️ To      : <code>{short(log.args['to'])}</code>",
            ])

    # 2) IncreaseLiquidity (Add Liquidity, kecuali baru saja mint di atas)
    increase_logs = filter_valid_logs(rpc(
        nfpm.events.IncreaseLiquidity().get_logs,
        from_block=from_block, to_block=to_block, label="nfpm.IncreaseLiquidity",
    ), label="nfpm.IncreaseLiquidity")
    for log in increase_logs:
        try:
            token_id = log.args["tokenId"]
            wallet = state.find_wallet_for_token("v3", token_id)
            if not wallet:
                continue
            pos_state = state.get_position(f"v3:{token_id}")
            if pos_state and pos_state.get("status") == "open" and log.transactionHash.hex() in pos_state.get("_seen_mint_tx", []):
                continue
            try:
                pos = rpc(nfpm.functions.positions(token_id).call, label=f"nfpm.positions({token_id})")
            except Exception as e:
                print(f"[v3] skip token {token_id} (IncreaseLiquidity): positions() gagal: {e}")
                continue
            (_, _, token0, token1, fee, tick_lower, tick_upper, liquidity, _, _, _, _) = pos
            t0meta = {**erc20_meta(token0), "address": token0}
            t1meta = {**erc20_meta(token1), "address": token1}
            pool_addr, _, current_tick = v3_pool_and_tick(token0, token1, fee)
            added0 = log.args["amount0"] / (10 ** t0meta["decimals"])
            added1 = log.args["amount1"] / (10 ** t1meta["decimals"])
            body, _, _ = build_v3_body(
                t0meta, t1meta, fee, tick_lower, tick_upper, current_tick, liquidity, pool_addr, token_id,
                extra_lines=[f"➕ Added     : {fmt(added0)} {t0meta['symbol']} + {fmt(added1)} {t1meta['symbol']}"],
            )
            notify(wallet, "➕ Add Liquidity", body)
        except Exception as e:
            print(f"[v3.increase] skip 1 log krn error tak terduga (token={token_id}): {e}")
            continue

    # 3) DecreaseLiquidity (Remove Liquidity, full = close position)
    decrease_logs = filter_valid_logs(rpc(
        nfpm.events.DecreaseLiquidity().get_logs,
        from_block=from_block, to_block=to_block, label="nfpm.DecreaseLiquidity",
    ), label="nfpm.DecreaseLiquidity")
    for log in decrease_logs:
        try:
            token_id = log.args["tokenId"]
            wallet = state.find_wallet_for_token("v3", token_id)
            if not wallet:
                continue
            try:
                pos = rpc(nfpm.functions.positions(token_id).call, label=f"nfpm.positions({token_id})")
            except Exception as e:
                print(f"[v3] skip token {token_id} (DecreaseLiquidity): positions() gagal: {e}")
                pos_state = state.get_position(f"v3:{token_id}") or {}
                pos_state["status"] = "closed"
                state.set_position(f"v3:{token_id}", pos_state)
                continue
            (_, _, token0, token1, fee, tick_lower, tick_upper, liquidity_after, _, _, _, _) = pos
            t0meta = {**erc20_meta(token0), "address": token0}
            t1meta = {**erc20_meta(token1), "address": token1}
            pool_addr, _, current_tick = v3_pool_and_tick(token0, token1, fee)
            removed0 = log.args["amount0"] / (10 ** t0meta["decimals"])
            removed1 = log.args["amount1"] / (10 ** t1meta["decimals"])
            label = "➖ Remove Liquidity · Position Closed" if liquidity_after == 0 else "➖ Remove Liquidity · Partial"
            body, _, _ = build_v3_body(
                t0meta, t1meta, fee, tick_lower, tick_upper, current_tick, liquidity_after, pool_addr, token_id,
                extra_lines=[f"➖ Removed   : {fmt(removed0)} {t0meta['symbol']} + {fmt(removed1)} {t1meta['symbol']}"],
            )
            notify(wallet, label, body)
            if liquidity_after == 0:
                pos_state = state.get_position(f"v3:{token_id}") or {}
                pos_state["status"] = "closed"
                state.set_position(f"v3:{token_id}", pos_state)
        except Exception as e:
            print(f"[v3.decrease] skip 1 log krn error tak terduga (token={token_id}): {e}")
            continue

    # 4) Collect (fee harvest) — cumulative sekarang disimpan dalam token asli
    #    (bukan USD, karena fitur value sudah dihapus)
    collect_logs = filter_valid_logs(rpc(
        nfpm.events.Collect().get_logs,
        from_block=from_block, to_block=to_block, label="nfpm.Collect",
    ), label="nfpm.Collect")
    for log in collect_logs:
        try:
            token_id = log.args["tokenId"]
            wallet = state.find_wallet_for_token("v3", token_id)
            if not wallet:
                continue
            try:
                pos = rpc(nfpm.functions.positions(token_id).call, label=f"nfpm.positions({token_id})")
            except Exception as e:
                print(f"[v3] skip token {token_id} (Collect): positions() gagal: {e}")
                continue
            (_, _, token0, token1, fee, tick_lower, tick_upper, liquidity, _, _, _, _) = pos
            t0meta = {**erc20_meta(token0), "address": token0}
            t1meta = {**erc20_meta(token1), "address": token1}
            c0 = log.args["amount0"] / (10 ** t0meta["decimals"])
            c1 = log.args["amount1"] / (10 ** t1meta["decimals"])
            pos_state = state.get_position(f"v3:{token_id}") or {"fees_collected": {}}
            fees = pos_state.setdefault("fees_collected", {})
            fees[t0meta["symbol"]] = fees.get(t0meta["symbol"], 0.0) + c0
            fees[t1meta["symbol"]] = fees.get(t1meta["symbol"], 0.0) + c1
            state.set_position(f"v3:{token_id}", pos_state)
            claimed_str = f"{fmt(c0)} {t0meta['symbol']} + {fmt(c1)} {t1meta['symbol']}"
            cumulative_str = " + ".join(f"{fmt(v)} {sym}" for sym, v in fees.items())
            notify(wallet, "💰 Fees Collected", [
                f"🔗 Pair      : {t0meta['symbol']}/{t1meta['symbol']}",
                f"🆔 Token ID  : {token_id}",
                "",
                f"💰 Claimed   : {claimed_str}",
                f"📈 Total Fees Terkumpul (akumulatif): {cumulative_str}",
            ])
        except Exception as e:
            print(f"[v3.collect] skip 1 log krn error tak terduga (token={token_id}): {e}")
            continue


# ───────────────────────── Core scan V4 (best-effort) ─────────────────────────

def process_v4(from_block: int, to_block: int):
    """
    V4 pakai singleton PoolManager, jadi ModifyLiquidity event TIDAK langsung
    berisi wallet pemilik (yang muncul cuma msg.sender, biasanya PositionManager).
    Kita deteksi wallet lewat Transfer NFT di PositionManager V4, lalu ambil
    tickLower/tickUpper/poolId dari event ModifyLiquidity di tx yang sama.
    Detail pool (currency0/1, fee, hooks) didapat dari event `Initialize` di
    PoolManager (di-cache per poolId), dan harga/tick SEKARANG dari
    `StateView.getSlot0(poolId)` — bukan dari harga saat Initialize, biar
    status in/out-of-range akurat.
    """
    tracked = state.get_all_tracked_wallets()
    if not tracked:
        return set()
    checksum_wallets = [Web3.to_checksum_address(w) for w in tracked]
    try:
        transfer_in = filter_valid_logs(rpc(
            v4_position_manager.events.Transfer().get_logs,
            from_block=from_block, to_block=to_block, argument_filters={"to": checksum_wallets},
            label="v4.Transfer(to=wallets)",
        ), label="v4.Transfer(to=wallets)")
    except Exception as e:
        print("[v4] gagal ambil log Transfer PositionManager:", e)
        return set()

    handled_tx_hashes = set()  # tx yg sudah dinotif di sini -> di-skip di process_v4_modify_liquidity
    for log in transfer_in:
        wallet = log.args["to"].lower()
        token_id = log.args["tokenId"]
        is_mint = log.args["from"] == ZERO_ADDR
        handled_tx_hashes.add(log.transactionHash)  # tetap skip di process_v4_modify_liquidity walau di-skip di bawah
        if not state.claim_wallet_token(wallet, "v4", token_id):
            continue  # sudah dikenal (atomik) -> jangan notif dobel
        title = "🆕 New Liquidity Position (V4)" if is_mint else "📥 Position Transferred In (V4)"

        # cari ModifyLiquidity di tx yang sama buat ambil tickLower/tickUpper/poolId/liquidity
        rpc_error = None
        try:
            receipt = rpc(w3.eth.get_transaction_receipt, log.transactionHash, label="get_transaction_receipt")
            ml_events = filter_valid_logs(v4_pool_manager.events.ModifyLiquidity().process_receipt(receipt, errors=W3_LOG_IGNORE), label="v4.ModifyLiquidity.process_receipt")
        except Exception as e:
            # PENTING: bedakan "tx-nya memang tidak punya event ModifyLiquidity" vs
            # "gagal ambil/decode receipt-nya" (mis. RPC 429 setelah retry habis).
            # Sebelumnya dua kasus ini digabung jadi satu pesan "tidak nemu event" yang
            # menyesatkan tiap RPC lagi rate-limit pas ada burst mint -> spam Telegram.
            print(f"[v4] gagal ambil/decode receipt tx {log.transactionHash.hex()} (token {token_id}):", e)
            ml_events = []
            rpc_error = e
        # jeda kecil antar tx biar tidak nembak get_transaction_receipt beruntun tanpa
        # jeda ke RPC publik saat ada banyak posisi baru numpuk di satu poll cycle
        time.sleep(config.CHUNK_SLEEP_SECONDS)

        if not ml_events:
            if rpc_error is not None:
                # Error RPC asli, bukan "memang tidak ada event" -> jangan kirim notif
                # yang salah info. Token ID sudah kesimpan lewat state.add_wallet_token()
                # di atas, jadi kalau nanti ada event lain buat token ini bot tetap kenal.
                continue
            notify(wallet, title, [
                f"🆔 Token ID : {token_id}",
                "",
                "⚠️ Tidak nemu event ModifyLiquidity di tx yang sama, jadi status/amount "
                "belum bisa dihitung untuk posisi ini.",
            ])
            continue

        for ev in ml_events:
            pool_id_bytes = ev.args["id"]
            pool_id_hex = pool_id_bytes.hex()
            tick_lower, tick_upper = ev.args["tickLower"], ev.args["tickUpper"]
            liquidity = ev.args["liquidityDelta"]  # utk mint, delta = liquidity penuh posisi

            pool_info = get_v4_pool_info(pool_id_bytes)
            if pool_info is None:
                notify(wallet, title, [
                    f"🆔 Token ID : {token_id}",
                    f"📊 Tick     : {tick_lower} → {tick_upper}",
                    "",
                    f"⚠️ Belum nemu event <code>Initialize</code> utk pool "
                    f"<code>0x{pool_id_hex[:10]}...</code> — kemungkinan pool ini dibuat SEBELUM "
                    "V4_POOL_MANAGER_DEPLOY_BLOCK di config.py. Perkecil angka itu lalu restart bot "
                    "biar fee tier, hooks & amount bisa dihitung.",
                ])
                continue

            try:
                slot0 = rpc(v4_state_view.functions.getSlot0(pool_id_bytes).call,
                             label=f"stateView.getSlot0({pool_id_hex[:10]}...)")
                current_tick, lp_fee = slot0[1], slot0[3]
            except Exception as e:
                print(f"[v4] gagal getSlot0 utk pool {pool_id_hex[:10]}...:", e)
                current_tick, lp_fee = tick_lower, pool_info["fee"]

            token0_meta = v4_currency_meta(pool_info["currency0"])
            token1_meta = v4_currency_meta(pool_info["currency1"])

            body, _, _ = build_v4_body(
                token0_meta, token1_meta, pool_info["fee"], lp_fee, pool_info["hooks"],
                tick_lower, tick_upper, current_tick, liquidity, pool_id_hex, token_id,
            )
            notify(wallet, title, body)

    return handled_tx_hashes


def process_v4_modify_liquidity(from_block: int, to_block: int, skip_tx_hashes=None):
    """
    Menangani Add/Remove/Collect di posisi V4 yang SUDAH DIKENAL (NFT-nya
    tetap di wallet yang sama, jadi TIDAK ada event Transfer sama sekali —
    process_v4() di atas cuma nangkep Transfer, jadi tanpa fungsi ini semua
    Add/Remove/Collect susulan pada posisi V4 existing TIDAK PERNAH terdeteksi,
    walau posisinya sendiri sudah dikenal bot dari mint awal.

    V4 tidak punya event terpisah per-tokenId kayak V3 (IncreaseLiquidity/
    DecreaseLiquidity/Collect) — semua perubahan lewat SATU event
    ModifyLiquidity di PoolManager, dan field `salt`-nya, sesuai standar resmi
    PositionManager Uniswap v4, SELALU diisi tokenId posisi (salt =
    bytes32(tokenId)) — itu satu-satunya cara korelasi event ini ke tokenId
    tanpa indexer. Collect fee di V4 dikirim sebagai ModifyLiquidity dengan
    liquidityDelta = 0 (bukan event Collect terpisah kayak V3).

    `skip_tx_hashes`: set tx hash yang SUDAH dinotif oleh process_v4() (mint
    posisi baru) di siklus scan yang sama -- tx mint JUGA memicu event
    ModifyLiquidity (liquidityDelta positif penuh), jadi tanpa filter ini
    posisi yang baru di-mint bakal dapat 2 notifikasi terpisah untuk
    kejadian yang sama persis ("New Liquidity Position" + "Add Liquidity").
    """
    skip_tx_hashes = skip_tx_hashes or set()
    logs = filter_valid_logs(rpc(
        v4_pool_manager.events.ModifyLiquidity().get_logs,
        from_block=from_block, to_block=to_block,
        label="v4_pool_manager.ModifyLiquidity",
    ), label="v4_pool_manager.ModifyLiquidity")
    for ev in logs:
        try:
            if ev.transactionHash in skip_tx_hashes:
                continue
            salt = ev.args["salt"]
            try:
                token_id = int.from_bytes(salt, "big") if isinstance(salt, (bytes, bytearray)) else int(salt)
            except Exception:
                continue
            if token_id == 0:
                continue  # salt kosong -> bukan posisi lewat PositionManager standar, skip
            wallet = state.find_wallet_for_token("v4", token_id)
            if not wallet:
                continue  # bukan tokenId yang kita kenal

            liquidity_delta = ev.args["liquidityDelta"]
            pool_id_bytes = ev.args["id"]
            pool_id_hex = pool_id_bytes.hex()
            tick_lower, tick_upper = ev.args["tickLower"], ev.args["tickUpper"]

            pool_info = get_v4_pool_info(pool_id_bytes)
            if pool_info is None:
                notify(wallet, "📶 Aktivitas Posisi V4", [
                    f"🆔 Token ID : {token_id}",
                    "",
                    f"⚠️ Belum nemu event <code>Initialize</code> utk pool "
                    f"<code>0x{pool_id_hex[:10]}...</code>, detail transaksi tidak bisa dihitung.",
                ])
                continue
            try:
                slot0 = rpc(v4_state_view.functions.getSlot0(pool_id_bytes).call,
                             label=f"stateView.getSlot0({pool_id_hex[:10]}...)")
                current_tick, lp_fee = slot0[1], slot0[3]
            except Exception as e:
                print(f"[v4] gagal getSlot0 utk pool {pool_id_hex[:10]}...:", e)
                current_tick, lp_fee = tick_lower, pool_info["fee"]

            token0_meta = v4_currency_meta(pool_info["currency0"])
            token1_meta = v4_currency_meta(pool_info["currency1"])

            if liquidity_delta > 0:
                title = "➕ Add Liquidity (V4)"
                liq_for_amount = liquidity_delta
            elif liquidity_delta < 0:
                title = "➖ Remove Liquidity (V4)"
                liq_for_amount = -liquidity_delta
            else:
                # liquidityDelta == 0 -> collect fee murni (standar PositionManager
                # V4: collect = DECREASE_LIQUIDITY dgn liquidity=0 + TAKE_PAIR).
                # Event ModifyLiquidity sendiri TIDAK punya field amount0/amount1
                # (beda dari V3 yg punya event Collect eksplisit) -- tapi dana yg
                # di-collect tetap disetel lewat Transfer token ERC20 ASLI di
                # transaksi yg sama, jadi hitung dari situ: scan event
                # Transfer(to=wallet) pada kontrak token0/token1 di tx ini.
                claimed0 = claimed1 = None
                try:
                    receipt = rpc(w3.eth.get_transaction_receipt, ev.transactionHash,
                                   label="get_transaction_receipt(v4.collect)")
                    wallet_cs = Web3.to_checksum_address(wallet)
                    addr0, addr1 = pool_info["currency0"], pool_info["currency1"]
                    if int(addr0, 16) != 0:  # bukan native ETH (address(0))
                        c0 = w3.eth.contract(address=Web3.to_checksum_address(addr0), abi=ERC20_ABI)
                        t0_logs = filter_valid_logs(
                            c0.events.Transfer().process_receipt(receipt, errors=W3_LOG_IGNORE),
                            label="v4.collect.token0.Transfer",
                        )
                        claimed0 = sum(l.args["value"] for l in t0_logs if l.args["to"] == wallet_cs) \
                            / (10 ** token0_meta["decimals"])
                    if int(addr1, 16) != 0:
                        c1 = w3.eth.contract(address=Web3.to_checksum_address(addr1), abi=ERC20_ABI)
                        t1_logs = filter_valid_logs(
                            c1.events.Transfer().process_receipt(receipt, errors=W3_LOG_IGNORE),
                            label="v4.collect.token1.Transfer",
                        )
                        claimed1 = sum(l.args["value"] for l in t1_logs if l.args["to"] == wallet_cs) \
                            / (10 ** token1_meta["decimals"])
                except Exception as e:
                    print(f"[v4.collect] gagal hitung jumlah fee utk token {token_id}:", e)

                if claimed0 is not None or claimed1 is not None:
                    claimed_str = (f"{fmt(claimed0 or 0)} {token0_meta['symbol']} + "
                                    f"{fmt(claimed1 or 0)} {token1_meta['symbol']}")
                    notify(wallet, "💰 Fees Collected (V4)", [
                        f"🔗 Pair     : {token0_meta['symbol']}/{token1_meta['symbol']}",
                        f"🆔 Token ID : {token_id}",
                        "",
                        f"💰 Claimed  : {claimed_str}",
                    ])
                else:
                    notify(wallet, "💰 Fees Collected (V4)", [
                        f"🔗 Pair     : {token0_meta['symbol']}/{token1_meta['symbol']}",
                        f"🆔 Token ID : {token_id}",
                        "",
                        "ℹ️ Jumlah fee tidak bisa dihitung otomatis (kemungkinan salah satu "
                        "sisi token native ETH, atau transfer-nya tidak lewat event ERC20 "
                        "standar) — cek detail tx di block explorer untuk nominal pastinya.",
                    ])
                continue

            body, amt0, amt1 = build_v4_body(
                token0_meta, token1_meta, pool_info["fee"], lp_fee, pool_info["hooks"],
                tick_lower, tick_upper, current_tick, liq_for_amount, pool_id_hex, token_id,
            )
            # build_v4_body label baris "💰 Amount" sebagai jumlah TOTAL posisi;
            # di sini nilainya adalah jumlah DELTA (yang ditambah/dikurangi), jadi
            # override label baris itu biar nggak menyesatkan.
            for i, line in enumerate(body):
                if line.startswith("💰 Amount"):
                    verb = "Ditambah" if liquidity_delta > 0 else "Dikurangi"
                    body[i] = f"💰 {verb}   : {fmt(amt0)} {token0_meta['symbol']} + {fmt(amt1)} {token1_meta['symbol']}"
                    break
            notify(wallet, title, body)
        except Exception as e:
            print(f"[v4_modify] skip 1 event krn error tak terduga (tx={ev.transactionHash.hex()[:12]}...): {e}")
            continue
# Dipanggil SEKALI setiap ada /addwallet baru berhasil (lihat main() & wiring
# ke telegram_commands.poll_commands_forever di bawah). Tujuannya: scan
# histori block dari wallet ini SEBELUM masuk daftar pantau, supaya posisi LP
# yang SUDAH ADA (bukan baru dibuat setelah /addwallet) langsung dikenal
# tokenId-nya. Tanpa ini, posisi lama tidak akan pernah match ke wallet mana
# pun di scan loop utama -> user cuma bisa add/remove wallet tapi TIDAK
# PERNAH dapat alert aktivitas beneran untuk posisi yang sudah ada duluan.
# Notifikasi hasil backfill dikirim LANGSUNG ke chat_id yang nambahin (bukan
# lewat notify(), yang broadcast ke SEMUA owner wallet ini) supaya tidak
# nge-spam owner lain yang sudah pernah dapat notif serupa sebelumnya.

def _wallet_label_for(chat_id: str, wallet: str) -> str:
    name = state.get_wallet_name(chat_id, wallet)
    return f"{name} · <code>{short(wallet)}</code>" if name else f"<code>{short(wallet)}</code>"


def _backfill_v3_chunk(wallet: str, wallet_cs: str, chat_id: str, from_block: int, to_block: int) -> bool:
    found = False
    logs = filter_valid_logs(rpc(
        nfpm.events.Transfer().get_logs,
        from_block=from_block, to_block=to_block, argument_filters={"to": wallet_cs},
        label="backfill.nfpm.Transfer",
    ), label="backfill.nfpm.Transfer")
    for log in logs:
        token_id = log.args["tokenId"]
        if not state.claim_wallet_token(wallet, "v3", token_id):
            continue  # sudah dikenal (atomik) -> mis. race dgn scan loop utama atau user lain addwallet alamat sama
        try:
            pos = rpc(nfpm.functions.positions(token_id).call, label=f"nfpm.positions({token_id})")
        except Exception as e:
            print(f"[backfill v3] skip token {token_id}: positions() gagal (kemungkinan sudah closed): {e}")
            continue
        (_, _, token0, token1, fee, tick_lower, tick_upper, liquidity, _, _, _, _) = pos
        if liquidity == 0:
            state.set_position(f"v3:{token_id}", {"status": "closed"})
            continue  # posisi sudah ditutup sebelum bot sempat lihat -> tidak perlu notif
        t0meta = {**erc20_meta(token0), "address": token0}
        t1meta = {**erc20_meta(token1), "address": token1}
        pool_addr, _, current_tick = v3_pool_and_tick(token0, token1, fee)
        body, amt0, amt1 = build_v3_body(t0meta, t1meta, fee, tick_lower, tick_upper,
                                          current_tick, liquidity, pool_addr, token_id)
        lines = ["<b>📋 Posisi LP Existing Ditemukan</b>", DIVIDER,
                  f"👛 Wallet   : {_wallet_label_for(chat_id, wallet)}"] + body
        send_message(chat_id, "\n".join(lines))
        state.set_position(f"v3:{token_id}", {
            "wallet": wallet, "token0": token0, "token1": token1, "fee": fee,
            "tick_lower": tick_lower, "tick_upper": tick_upper,
            "entry_amount0": amt0, "entry_amount1": amt1,
            "fees_collected": {}, "status": "open",
        })
        found = True
        time.sleep(config.CHUNK_SLEEP_SECONDS)
    return found


def _backfill_v4_chunk(wallet: str, wallet_cs: str, chat_id: str, from_block: int, to_block: int) -> bool:
    found = False
    logs = filter_valid_logs(rpc(
        v4_position_manager.events.Transfer().get_logs,
        from_block=from_block, to_block=to_block, argument_filters={"to": wallet_cs},
        label="backfill.v4.Transfer",
    ), label="backfill.v4.Transfer")
    for log in logs:
        token_id = log.args["tokenId"]
        if not state.claim_wallet_token(wallet, "v4", token_id):
            continue  # sudah dikenal (atomik) -> race dgn scan loop utama atau user lain
        try:
            receipt = rpc(w3.eth.get_transaction_receipt, log.transactionHash, label="get_transaction_receipt")
            ml_events = filter_valid_logs(v4_pool_manager.events.ModifyLiquidity().process_receipt(receipt, errors=W3_LOG_IGNORE), label="v4.ModifyLiquidity.process_receipt")
        except Exception as e:
            print(f"[backfill v4] gagal ambil/decode receipt tx {log.transactionHash.hex()}:", e)
            continue
        time.sleep(config.CHUNK_SLEEP_SECONDS)
        for ev in ml_events:
            pool_id_bytes = ev.args["id"]
            pool_id_hex = pool_id_bytes.hex()
            tick_lower, tick_upper = ev.args["tickLower"], ev.args["tickUpper"]
            liquidity = ev.args["liquidityDelta"]
            if liquidity <= 0:
                continue  # posisi sudah ditutup / bukan penambahan bersih
            pool_info = get_v4_pool_info(pool_id_bytes)
            if pool_info is None:
                continue  # sama seperti process_v4: belum nemu event Initialize
            try:
                slot0 = rpc(v4_state_view.functions.getSlot0(pool_id_bytes).call,
                             label=f"stateView.getSlot0({pool_id_hex[:10]}...)")
                current_tick, lp_fee = slot0[1], slot0[3]
            except Exception:
                current_tick, lp_fee = tick_lower, pool_info["fee"]
            token0_meta = v4_currency_meta(pool_info["currency0"])
            token1_meta = v4_currency_meta(pool_info["currency1"])
            body, _, _ = build_v4_body(
                token0_meta, token1_meta, pool_info["fee"], lp_fee, pool_info["hooks"],
                tick_lower, tick_upper, current_tick, liquidity, pool_id_hex, token_id,
            )
            lines = ["<b>📋 Posisi LP Existing Ditemukan (V4)</b>", DIVIDER,
                      f"👛 Wallet   : {_wallet_label_for(chat_id, wallet)}"] + body
            send_message(chat_id, "\n".join(lines))
            found = True
    return found


def backfill_wallet_positions(wallet: str, chat_id: str):
    """Entry point backfill, dipanggil di thread terpisah (lihat main())
    supaya tidak memblok listener command Telegram selama proses scan."""
    try:
        wallet = wallet.lower()
        wallet_cs = Web3.to_checksum_address(wallet)
        latest = rpc(lambda: w3.eth.block_number, label="block_number")
        from_block = max(0, latest - config.WALLET_ADD_LOOKBACK_BLOCKS)
        found_any = False
        start = from_block
        while start <= latest:
            end = min(start + config.BLOCK_CHUNK_SIZE - 1, latest)
            if _backfill_v3_chunk(wallet, wallet_cs, chat_id, start, end):
                found_any = True
            if _backfill_v4_chunk(wallet, wallet_cs, chat_id, start, end):
                found_any = True
            start = end + 1
            time.sleep(config.CHUNK_SLEEP_SECONDS)
        if not found_any:
            send_message(
                chat_id,
                f"🔍 Sudah dicek {config.WALLET_ADD_LOOKBACK_BLOCKS:,} block terakhir untuk "
                f"<code>{wallet}</code> — belum ada posisi LP existing yang ketemu. Kalau kamu "
                "yakin wallet ini punya posisi lebih lama dari itu, perbesar "
                "<code>WALLET_ADD_LOOKBACK_BLOCKS</code> di config.py lalu /removewallet + "
                "/addwallet lagi.",
            )
    except Exception as e:
        print(f"[backfill] gagal backfill wallet {wallet}:", e)
        send_message(chat_id, "⚠️ Gagal cek posisi LP existing untuk wallet ini (error RPC). "
                               "Posisi baru ke depannya tetap akan terdeteksi normal.")


def _on_wallet_added(wallet: str, chat_id: str):
    """Callback dari telegram_commands — jalankan backfill di thread sendiri
    biar listener command tidak nge-block nunggu scan selesai."""
    threading.Thread(target=backfill_wallet_positions, args=(wallet, chat_id), daemon=True).start()


# ───────────────────────── Main loop ─────────────────────────

def main():
    acquire_single_instance_lock()
    try:
        if not w3.is_connected():
            raise SystemExit("Tidak bisa connect ke RPC_URL. Cek config.py / env var RPC_URL.")

        # Listener command Telegram (/addwallet dkk) jalan di thread terpisah,
        # paralel dengan loop scan blockchain utama di bawah — bug versi lama:
        # listener ini pernah ada tapi TIDAK PERNAH DIJALANKAN dari monitor.py,
        # jadi /addwallet /removewallet tidak benar-benar berfungsi saat bot jalan.
        threading.Thread(
            target=poll_commands_forever, args=(state,),
            kwargs={"on_wallet_added": _on_wallet_added}, daemon=True,
        ).start()

        wallets = state.get_all_tracked_wallets()
        print(f"LP Tracker Bot jalan. {state.total_users()} user terdaftar, {len(wallets)} wallet unik dipantau:")
        for w in wallets:
            print(" -", w)

        last_block = state.get_last_block()
        if last_block is None:
            last_block = rpc(lambda: w3.eth.block_number, label="block_number") - config.LOOKBACK_BLOCKS_ON_START

        if config.ADMIN_CHAT_ID:
            send_message(
                config.ADMIN_CHAT_ID,
                "<b>🤖 LP Tracker Bot Aktif</b>\n" + DIVIDER +
                f"\nMemantau {len(wallets)} wallet ({state.total_users()} user) untuk aktivitas LP "
                "di Uniswap V3/V4.\nSiapa pun bisa chat bot ini dan /addwallet wallet mereka sendiri.",
            )

        consecutive_errors = 0
        while True:
            try:
                latest = rpc(lambda: w3.eth.block_number, label="block_number")
                if latest > last_block:
                    start = last_block + 1
                    while start <= latest:
                        end = min(start + config.BLOCK_CHUNK_SIZE - 1, latest)
                        if state.get_all_tracked_wallets():
                            process_v3(start, end)
                            v4_mint_tx_hashes = process_v4(start, end)
                            process_v4_modify_liquidity(start, end, skip_tx_hashes=v4_mint_tx_hashes)
                        # Simpan last_block TIAP CHUNK (bukan nunggu semua chunk
                        # selesai) -- kalau bot crash/restart di tengah catch-up
                        # panjang, tanpa ini dia bakal scan ULANG dari titik lama
                        # pas nyala lagi, dan notif yang sudah terkirim bisa
                        # terkirim BERKALI-KALI lagi (jumlahnya beda tiap kali
                        # krn dihitung pakai harga live saat itu -> kelihatan
                        # kayak "New Liquidity Position" yang sama nyepam).
                        last_block = end
                        state.set_last_block(last_block)
                        state.save()
                        start = end + 1
                        # kasih jeda antar chunk biar tidak membanjiri RPC publik
                        # (penting terutama saat catch-up backlog panjang)
                        time.sleep(config.CHUNK_SLEEP_SECONDS)
                consecutive_errors = 0
                sleep_for = config.POLL_INTERVAL_SECONDS
            except Exception as e:
                consecutive_errors += 1
                print(f"[main loop] error (berturut-turut ke-{consecutive_errors}):", e)
                # Backoff bertahap kalau error terus-menerus (mis. RPC lagi rate-limit
                # berat), biar tidak spam request tiap POLL_INTERVAL_SECONDS.
                sleep_for = min(
                    config.POLL_INTERVAL_SECONDS * (2 ** min(consecutive_errors, 5)),
                    config.RPC_BACKOFF_MAX_SECONDS,
                )
            time.sleep(sleep_for)
    finally:
        release_single_instance_lock()


if __name__ == "__main__":
    main()
