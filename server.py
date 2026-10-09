import uvicorn, threading, time
from backend.app.main import app
from backend.app.saas_api import install
app = install(app)

@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response

def _market_loop():
    # Collect a fresh market snapshot every 15 minutes. Never log credentials.
    import logging
    logger=logging.getLogger("synpora.market_collection")
    time.sleep(10)  # Let the ASGI server begin accepting requests before the first collection.
    missing_token_warned=False
    while True:
        try:
            import urllib.request, json, os
            token=os.getenv("SYNPORA_MARKET_ADMIN_TOKEN","").strip()
            if not token:
                if not missing_token_warned:
                    logger.warning("Market collection is disabled: SYNPORA_MARKET_ADMIN_TOKEN is not configured.")
                    missing_token_warned=True
            else:
                url="http://127.0.0.1:"+os.getenv("PORT","8000")+"/api/v1/market/collect"
                req=urllib.request.Request(url,headers={"X-SYNPORA-MARKET-TOKEN":token,"Accept":"application/json"},method="POST")
                with urllib.request.urlopen(req,timeout=8) as response:
                    payload=json.loads(response.read().decode())
                quality=payload.get("data_quality",{})
                logger.info("Market snapshot collected; source=%s quality=%s external_fields=%s reference_fields=%s",
                    payload.get("source","unknown"),quality.get("status","unknown"),
                    quality.get("external_fields",0),quality.get("reference_fields",0))
        except Exception as exc:
            logger.warning("Market snapshot collection failed (%s).",type(exc).__name__)
        time.sleep(900)

threading.Thread(target=_market_loop,daemon=True).start()

@app.get("/health")
def health():
    return {"status":"ok","service":"synpora"}

# The health endpoint is registered after install(); keep the frontend mount
# last so it cannot shadow /health or API requests.
try:
    from starlette.routing import Mount
    root_mounts=[r for r in app.router.routes if isinstance(r,Mount) and r.path in ("", "/")]
    if root_mounts:
        app.router.routes=[r for r in app.router.routes if r not in root_mounts]+root_mounts
except Exception:
    pass

if __name__ == "__main__":
    uvicorn.run(app,host="0.0.0.0",port=int(__import__("os").getenv("PORT","8000")))
