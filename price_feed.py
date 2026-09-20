import time
import requests

from rpc_retry import call_with_retry

_cache = {}
# Dinaikkan dari 60 -> 120 detik: mengurangi frekuensi hit ke CoinGecko
# (endpoint publiknya juga rate-limited / bisa 429 kalau terlalu sering).
_CACHE_TTL = 120  # detik

# Mapping alamat token populer -> id CoinGecko (tambahkan sendiri sesuai kebutuhan)
TOKEN_ADDRESS_TO_COINGECKO_ID = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "weth",  # WETH
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "usd-coin",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "tether",  # USDT (note: alamat asli beda checksum, cek ulang)
    "0x6b175474e89094c44da98b954eedeac495271d0f": "dai",  # DAI
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "wrapped-bitcoin",  # WBTC
}


def get_usd_price(token_address: str, symbol: str = "") -> float | None:
    """Ambil harga USD token via CoinGecko. Return None kalau tidak ketemu / gagal."""
    addr = token_address.lower()
    cg_id = TOKEN_ADDRESS_TO_COINGECKO_ID.get(addr)

    cache_key = cg_id or addr
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    try:
        if cg_id:
            resp = call_with_retry(
                requests.get,
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": cg_id, "vs_currencies": "usd"},
                timeout=8,
                max_retries=2, base_delay=3, max_delay=20,
                label=f"coingecko:{cg_id}",
            )
            price = resp.json().get(cg_id, {}).get("usd")
        else:
            # CATATAN: platform "ethereum" di sini cuma valid untuk alamat token
            # di Ethereum mainnet. Untuk token native Robinhood Chain, alamatnya
            # BEDA, jadi lookup ini akan selalu kosong (None) sampai CoinGecko
            # menambah platform id resmi untuk Robinhood Chain (cek dokumentasi
            # CoinGecko utk update). Tidak fatal — bot cuma tampilkan value "N/A".
            resp = call_with_retry(
                requests.get,
                "https://api.coingecko.com/api/v3/simple/token_price/ethereum",
                params={"contract_addresses": addr, "vs_currencies": "usd"},
                timeout=8,
                max_retries=2, base_delay=3, max_delay=20,
                label=f"coingecko:{addr}",
            )
            price = resp.json().get(addr, {}).get("usd")

        if price is not None:
            _cache[cache_key] = (time.time(), float(price))
        return price
    except Exception:
        return None
        