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
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
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

@app.get("/ops", include_in_schema=False)
def operations_dashboard():
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SYNPORA Betriebsstatus</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#10151d;color:#e7edf5}
body{max-width:1100px;margin:0 auto;padding:24px}h1{margin-bottom:4px}.muted{color:#aab6c5}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin:20px 0}
.card{background:#192331;border:1px solid #344255;border-radius:12px;padding:16px;overflow-wrap:anywhere}
.status{font-weight:700;font-size:1.15rem;margin:8px 0}.ok{color:#79d6a2}.warn{color:#ffd27a}.bad{color:#ff9292}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.84rem;color:#c8d4e3}
button{background:#315c8b;color:white;border:0;border-radius:8px;padding:10px 14px;font-size:1rem}
footer{margin-top:24px}.small{font-size:.9rem}
</style></head><body>
<h1>SYNPORA – Betriebsstatus</h1><p class="muted">Technische Übersicht · automatische Aktualisierung alle 30 Sekunden · Nur Empfehlungen – keine Hardwaresteuerung oder Trades</p>
<button id="refresh">Jetzt aktualisieren</button><span id="updated" class="muted small"> Noch nicht aktualisiert</span>
<div class="grid">
<section class="card"><h2>Produktionsbereitschaft</h2><div id="ready" class="status">Lade…</div><pre id="ready-detail"></pre></section>
<section class="card"><h2>Marktdaten</h2><div id="market" class="status">Lade…</div><pre id="market-detail"></pre></section>
<section class="card"><h2>Lern-Datensatz</h2><div id="learning" class="status">Lade…</div><pre id="learning-detail"></pre></section>
<section class="card"><h2>Release & Sicherheitsmodus</h2><div id="release" class="status">Lade…</div><pre id="release-detail"></pre></section>
</div>
<p class="muted small">Hinweis: „Ready“ bestätigt nur die Konfiguration und Erreichbarkeit, nicht die wirtschaftliche Qualität. Referenzdaten sind keine Live-Preise. Vor echten Entscheidungen müssen Datenquelle, Aktualität und Backtests separat geprüft werden. Diese Seite zeigt keine Zugangsschlüssel.</p>
<footer class="muted small">API-Status: <a href="/health">/health</a> · <a href="/api/v1/system/production-readiness">Readiness JSON</a> · <a href="/api/v1/market/data-health">Market Data JSON</a></footer>
<script>
const el=id=>document.getElementById(id);
function show(id,title,kind,detail){el(id).textContent=title;el(id).className='status '+kind;el(id+'-detail').textContent=detail;}
async function refresh(){
  el('updated').textContent=' Aktualisiere…';
  try{
    const results=await Promise.all([
      fetch('/api/v1/system/production-readiness',{cache:'no-store'}),
      fetch('/api/v1/market/data-health',{cache:'no-store'}),
      fetch('/api/v1/system/release',{cache:'no-store'})
    ]);
    if(results.some(r=>!r.ok))throw new Error('Ein Status-Endpunkt ist nicht erreichbar.');
    const [r,m,v]=await Promise.all(results.map(x=>x.json()));
    show('ready',r.status==='ready'?'Bereit':'Konfiguration prüfen',r.status==='ready'?'ok':'warn',JSON.stringify(r,null,2));
    const eligible=m.eligible_snapshots||0,excluded=m.excluded_snapshots||0;
    const marketKind=(m.latest_eligible_age_seconds!==null&&m.latest_eligible_age_seconds!==undefined&&m.latest_eligible_age_seconds<=1200&&eligible>0)?'ok':'warn';
    show('market',marketKind==='ok'?'Externe Daten vorhanden':'Datenqualität prüfen',marketKind,JSON.stringify(m,null,2));
    show('learning',eligible>0?'Geeignete Snapshots: '+eligible:'Noch keine geeigneten Snapshots',eligible>0?'ok':'warn','Ausgeschlossen: '+excluded+'\nLetzter geeigneter Snapshot (Alter Sekunden): '+(m.latest_eligible_age_seconds??'—')+'\nWarnungen: '+JSON.stringify(m.warnings||[]));
    show('release',(v.mode||'Unbekannt')+' · '+(v.release||''),v.hardware_write===false&&v.autonomous_control===false?'ok':'bad',JSON.stringify(v,null,2));
    el('updated').textContent=' Zuletzt aktualisiert: '+new Date().toLocaleTimeString();
  }catch(e){
    show('ready','Status nicht vollständig verfügbar','bad',String(e));
    el('updated').textContent=' Aktualisierung fehlgeschlagen';
  }
}
el('refresh').addEventListener('click',refresh);refresh();setInterval(refresh,30000);
</script></body></html>""")

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
