# Delta Exchange India — read-only MCP

This is a separate subfolder so it does not change the existing Windows assistant app in the repository root.

## Render settings
- Create a **Web Service** from this GitHub repository.
- Root Directory: `delta_mcp`
- Runtime: Python 3
- Build Command: `pip install -r requirements.txt`
- Start Command: `python server.py`
- No environment variables or Delta API keys are required.

After deploy, use the service's HTTPS URL as the remote MCP URL in Claude. The MCP endpoint is typically the service URL with `/mcp` appended (for example, `https://YOUR-SERVICE.onrender.com/mcp`). Confirm the endpoint in Render logs if Claude cannot connect.

## Tools
- `list_tickers`
- `get_ticker`
- `get_candles`
- `list_products`

All tools call public market-data endpoints only. No order placement, account access, API secrets, or trading actions are implemented.
