import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://api.india.delta.exchange"
mcp = FastMCP("Delta Exchange India Market Data", stateless_http=True, json_response=True)

async def delta_get(path: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{BASE_URL}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("success") is False:
            raise RuntimeError(str(payload))
        return payload.get("result", payload)

@mcp.tool()
async def list_tickers() -> dict:
    """Get public Delta Exchange India ticker data for all listed contracts. Read-only; no API key."""
    return await delta_get("/v2/tickers")

@mcp.tool()
async def get_ticker(symbol: str) -> dict:
    """Get public ticker/market data for a contract symbol, for example BTCUSD or P-BTC-90000-260131."""
    return await delta_get("/v2/tickers", {"契约": symbol, "symbol": symbol})

@mcp.tool()
async def get_candles(symbol: str, resolution: str = "5m", limit: int = 100) -> dict:
    """Get public OHLCV candles. resolution examples: 1m, 3m, 5m, 15m, 1h, 4h, 1d. limit is capped at 500."""
    allowed = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"}
    if resolution not in allowed:
        raise ValueError(f"Unsupported resolution. Choose from: {', '.join(sorted(allowed))}")
    limit = max(1, min(int(limit), 500))
    import time
    end = int(time.time())
    seconds = {"1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,"1h":3600,"2h":7200,"4h":14400,"6h":21600,"1d":86400}[resolution]
    start = end - seconds * limit
    return await delta_get("/v2/history/candles", {
        "symbol": symbol, "resolution": resolution, "start": start, "end": end
    })

@mcp.tool()
async def list_products() -> dict:
    """List public Delta Exchange India contracts/products and their symbols."""
    return await delta_get("/v2/products")

app = mcp.streamable_http_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
