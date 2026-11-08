"""Minimal stand-in for the simulator's /ws endpoint: logs hello and counts tool messages."""
import asyncio, json, sys, time
from aiohttp import web

async def ws(request):
    w = web.WebSocketResponse(); await w.prepare(request)
    n = 0; t0 = time.time()
    async for m in w:
        d = json.loads(m.data)
        if d.get("type") == "hello":
            print("hello", d, flush=True)
            await w.send_json({"type": "presence", "roles": {"hardware": 1}})
        elif d.get("type") == "tools":
            n += 1
            if n % 100 == 1:
                print(f"tools #{n} ({n / (time.time() - t0):.1f}/s):", json.dumps(d["tools"])[:300], flush=True)
    return w

app = web.Application(); app.router.add_get("/ws", ws)
web.run_app(app, port=int(sys.argv[1]) if len(sys.argv) > 1 else 8799, print=None)
