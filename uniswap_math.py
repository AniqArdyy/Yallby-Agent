"""Konversi tick <-> harga untuk Uniswap V3/V4 (konsep tick sama persis).

Catatan: fungsi saran preset "Zenith", klasifikasi strategi, dan format range
dalam % (yang dulu ada di sini) sudah DIHAPUS sesuai permintaan — bot sekarang
cuma perlu tahu status in-range/out-of-range dan jumlah token, bukan
menerka-nerka preset/strategi orang lain."""


def is_in_range(current_tick: int, tick_lower: int, tick_upper: int) -> bool:
    return tick_lower <= current_tick <= tick_upper
