"""ABI minimal — hanya fungsi/event yang benar-benar dipakai bot."""

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
]

# --- Uniswap V3 NonfungiblePositionManager ---
V3_NFPM_ABI = [
    {
        "anonymous": False, "name": "Transfer", "type": "event",
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": True, "name": "tokenId", "type": "uint256"},
        ],
    },
    {
        "anonymous": False, "name": "IncreaseLiquidity", "type": "event",
        "inputs": [
            {"indexed": True, "name": "tokenId", "type": "uint256"},
            {"indexed": False, "name": "liquidity", "type": "uint128"},
            {"indexed": False, "name": "amount0", "type": "uint256"},
            {"indexed": False, "name": "amount1", "type": "uint256"},
        ],
    },
    {
        "anonymous": False, "name": "DecreaseLiquidity", "type": "event",
        "inputs": [
            {"indexed": True, "name": "tokenId", "type": "uint256"},
            {"indexed": False, "name": "liquidity", "type": "uint128"},
            {"indexed": False, "name": "amount0", "type": "uint256"},
            {"indexed": False, "name": "amount1", "type": "uint256"},
        ],
    },
    {
        "anonymous": False, "name": "Collect", "type": "event",
        "inputs": [
            {"indexed": True, "name": "tokenId", "type": "uint256"},
            {"indexed": False, "name": "recipient", "type": "address"},
            {"indexed": False, "name": "amount0", "type": "uint256"},
            {"indexed": False, "name": "amount1", "type": "uint256"},
        ],
    },
    {
        "constant": True, "name": "positions", "type": "function",
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "outputs": [
            {"name": "nonce", "type": "uint96"},
            {"name": "operator", "type": "address"},
            {"name": "token0", "type": "address"},
            {"name": "token1", "type": "address"},
            {"name": "fee", "type": "uint24"},
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "liquidity", "type": "uint128"},
            {"name": "feeGrowthInside0LastX128", "type": "uint256"},
            {"name": "feeGrowthInside1LastX128", "type": "uint256"},
            {"name": "tokensOwed0", "type": "uint128"},
            {"name": "tokensOwed1", "type": "uint128"},
        ],
    },
    {
        "constant": True, "name": "ownerOf", "type": "function",
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "address"}],
    },
]

V3_FACTORY_ABI = [
    {
        "constant": True, "name": "getPool", "type": "function",
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"},
        ],
        "outputs": [{"name": "pool", "type": "address"}],
    }
]

V3_POOL_ABI = [
    {
        "constant": True, "name": "slot0", "type": "function",
        "inputs": [],
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint8"},
            {"name": "unlocked", "type": "bool"},
        ],
    },
    {
        "anonymous": False, "name": "Swap", "type": "event",
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": True, "name": "recipient", "type": "address"},
            {"indexed": False, "name": "amount0", "type": "int256"},
            {"indexed": False, "name": "amount1", "type": "int256"},
            {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
            {"indexed": False, "name": "liquidity", "type": "uint128"},
            {"indexed": False, "name": "tick", "type": "int24"},
        ],
    },
]

# topic0 event Swap(address,address,int256,int256,uint160,uint128,int24) — dipakai buat
# deteksi "apakah tx ini juga melakukan swap" (indikasi funding gaya Zap).
V3_SWAP_TOPIC0 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

# --- Uniswap V4 (best-effort, verifikasi ulang ABI resmi sebelum production) ---
V4_POOL_MANAGER_ABI = [
    {
        "anonymous": False, "name": "ModifyLiquidity", "type": "event",
        "inputs": [
            {"indexed": True, "name": "id", "type": "bytes32"},
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": False, "name": "tickLower", "type": "int24"},
            {"indexed": False, "name": "tickUpper", "type": "int24"},
            {"indexed": False, "name": "liquidityDelta", "type": "int256"},
            {"indexed": False, "name": "salt", "type": "bytes32"},
        ],
    },
    {
        "anonymous": False, "name": "Initialize", "type": "event",
        "inputs": [
            {"indexed": True, "name": "id", "type": "bytes32"},
            {"indexed": True, "name": "currency0", "type": "address"},
            {"indexed": True, "name": "currency1", "type": "address"},
            {"indexed": False, "name": "fee", "type": "uint24"},
            {"indexed": False, "name": "tickSpacing", "type": "int24"},
            {"indexed": False, "name": "hooks", "type": "address"},
            {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
            {"indexed": False, "name": "tick", "type": "int24"},
        ],
    },
]

# ERC-721 Transfer generik dipakai juga untuk V4 PositionManager
ERC721_TRANSFER_ABI = [
    {
        "anonymous": False, "name": "Transfer", "type": "event",
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": True, "name": "tokenId", "type": "uint256"},
        ],
    }
]

V4_STATE_VIEW_ABI = [
    {
        "constant": True, "name": "getSlot0", "type": "function",
        "inputs": [{"name": "poolId", "type": "bytes32"}],
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "protocolFee", "type": "uint24"},
            {"name": "lpFee", "type": "uint24"},
        ],
    }
]
