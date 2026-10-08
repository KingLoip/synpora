import uvicorn, threading, time
from backend.app.main import app
from backend.app.saas_api import install
app = install(app)

def _market_loop():
    # Collect a fresh market snapshot every 15 minutes. Failure is non-fatal.
    while True:
        try:
            import urllib.request, json, os
            req=urllib.request.Request("http://127.0.0.1:"+os.getenv("PORT","8000")+"/api/v1/market/collect")
            urllib.request.urlopen(req,timeout=8).read()
        except Exception:
            pass
        time.sleep(900)

threading.Thread(target=_market_loop,daemon=True).start()

@app.get("/health")
def health():
    return {"status":"ok","service":"synpora"}
if __name__ == "__main__":
    uvicorn.run(app,host="0.0.0.0",port=int(__import__("os").getenv("PORT","8000")))
