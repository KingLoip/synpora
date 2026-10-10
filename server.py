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
    # Allow same-origin application assets while retaining a restrictive policy.
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'")
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/v1/auth/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

MARKET_COLLECTION_INTERVAL_SECONDS=900
_market_status_lock=threading.Lock()
app.state.market_collector_status={
    "enabled":bool(__import__("os").getenv("SYNPORA_MARKET_ADMIN_TOKEN","").strip()),
    "interval_seconds":MARKET_COLLECTION_INTERVAL_SECONDS,
    "last_attempt_at":None,"last_success_at":None,"last_failure_at":None,
    "last_error_class":None,"last_source":None,"last_quality":None,
    "last_attempt_result":"not_started","consecutive_failures":0,
    "last_attempt_duration_ms":None,"next_attempt_at":None,"retry_delay_seconds":0
}

def _market_status_update(**values):
    with _market_status_lock:
        app.state.market_collector_status.update(values)

@app.get("/api/v1/system/collector-status", include_in_schema=False)
def market_collector_status():
    with _market_status_lock:
        status=dict(app.state.market_collector_status)
    token_configured=bool(__import__("os").getenv("SYNPORA_MARKET_ADMIN_TOKEN","").strip())
    status["enabled"]=token_configured
    if not token_configured and status["last_attempt_result"]=="not_started":
        status["last_attempt_result"]="disabled_missing_token"
    return status

def _market_loop():
    # Collect every 15 minutes on success; retry failures with bounded exponential backoff.
    # Never log credentials or exception messages.
    import logging
    logger=logging.getLogger("synpora.market_collection")
    time.sleep(10)  # Let the ASGI server begin accepting requests before the first collection.
    missing_token_warned=False
    retry_delay=60
    while True:
        attempt_at=time.time()
        attempt_started=time.monotonic()
        _market_status_update(last_attempt_at=attempt_at,last_attempt_result="running",next_attempt_at=None)
        try:
            import urllib.request, json, os
            token=os.getenv("SYNPORA_MARKET_ADMIN_TOKEN","").strip()
            if not token:
                if not missing_token_warned:
                    logger.warning("Market collection is disabled: SYNPORA_MARKET_ADMIN_TOKEN is not configured.")
                    missing_token_warned=True
                next_attempt=attempt_at+MARKET_COLLECTION_INTERVAL_SECONDS
                _market_status_update(last_attempt_result="disabled_missing_token",last_error_class=None,
                    retry_delay_seconds=0,next_attempt_at=next_attempt,
                    last_attempt_duration_ms=round((time.monotonic()-attempt_started)*1000))
            else:
                url="http://127.0.0.1:"+os.getenv("PORT","8000")+"/api/v1/market/collect"
                req=urllib.request.Request(url,headers={"X-SYNPORA-MARKET-TOKEN":token,"Accept":"application/json"},method="POST")
                with urllib.request.urlopen(req,timeout=8) as response:
                    payload=json.loads(response.read().decode())
                quality=payload.get("data_quality",{})
                completed_at=time.time()
                logger.info("Market snapshot collected; source=%s quality=%s external_fields=%s reference_fields=%s",
                    payload.get("source","unknown"),quality.get("status","unknown"),
                    quality.get("external_fields",0),quality.get("reference_fields",0))
                next_attempt=completed_at+MARKET_COLLECTION_INTERVAL_SECONDS
                _market_status_update(last_success_at=completed_at,last_attempt_result="success",
                    last_error_class=None,last_source=payload.get("source","unknown"),
                    last_quality=quality.get("status","unknown"),consecutive_failures=0,
                    retry_delay_seconds=0,next_attempt_at=next_attempt,
                    last_attempt_duration_ms=round((time.monotonic()-attempt_started)*1000))
                retry_delay=60
        except Exception as exc:
            logger.warning("Market snapshot collection failed (%s).",type(exc).__name__)
            retry_delay=min(MARKET_COLLECTION_INTERVAL_SECONDS,max(60,retry_delay*2))
            next_attempt=time.time()+retry_delay
            with _market_status_lock:
                failures=int(app.state.market_collector_status.get("consecutive_failures",0))+1
                app.state.market_collector_status.update(last_failure_at=time.time(),
                    last_attempt_result="failure",last_error_class=type(exc).__name__,
                    consecutive_failures=failures,retry_delay_seconds=retry_delay,
                    next_attempt_at=next_attempt,
                    last_attempt_duration_ms=round((time.monotonic()-attempt_started)*1000))
        time.sleep(max(1, next_attempt-time.time()))

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
<section class="card"><h2>Datensammlung</h2><div id="collector" class="status">Lade…</div><pre id="collector-detail"></pre></section>
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
      fetch('/api/v1/system/release',{cache:'no-store'}),
      fetch('/api/v1/system/collector-status',{cache:'no-store'})
    ]);
    if(results.some(r=>!r.ok))throw new Error('Ein Status-Endpunkt ist nicht erreichbar.');
    const [r,m,v,k]=await Promise.all(results.map(x=>x.json()));
    show('ready',r.status==='ready'?'Bereit':'Konfiguration prüfen',r.status==='ready'?'ok':'warn',JSON.stringify(r,null,2));
    const eligible=m.eligible_snapshots||0,excluded=m.excluded_or_invalid_snapshots||0;
    const marketKind=(m.latest_eligible_age_seconds!==null&&m.latest_eligible_age_seconds!==undefined&&m.latest_eligible_age_seconds<=1200&&eligible>0)?'ok':'warn';
    show('market',marketKind==='ok'?'Externe Daten vorhanden':'Datenqualität prüfen',marketKind,JSON.stringify(m,null,2));
    show('learning',eligible>0?'Geeignete Snapshots: '+eligible:'Noch keine geeigneten Snapshots',eligible>0?'ok':'warn','Ausgeschlossen: '+excluded+'\nLetzter geeigneter Snapshot (Alter Sekunden): '+(m.latest_eligible_age_seconds??'—')+'\nWarnungen: '+JSON.stringify(m.warnings||[]));
    const successAge=k.last_success_at?Math.max(0,Date.now()/1000-k.last_success_at):null;
    const collectorOk=k.enabled&&k.last_attempt_result==='success'&&successAge!==null&&successAge<=1200&&k.last_quality==='live';
    const collectorKind=!k.enabled?'warn':collectorOk?'ok':k.last_attempt_result==='failure'?'bad':'warn';
    const collectorTitle=!k.enabled?'Sammlung deaktiviert':collectorOk?'Sammlung läuft · externe Daten':'Sammlung prüfen';
    show('collector',collectorTitle,collectorKind,'Letzter Versuch: '+(k.last_attempt_at?new Date(k.last_attempt_at*1000).toLocaleString():'noch keiner')+'\nLetzter Erfolg: '+(k.last_success_at?new Date(k.last_success_at*1000).toLocaleString():'noch keiner')+'\nLetzter gespeicherter Snapshot: '+(m.latest_snapshot_timestamp?new Date(m.latest_snapshot_timestamp*1000).toLocaleString():'noch keiner')+'\nSnapshot-Alter (Sekunden): '+(m.latest_snapshot_age_seconds??'—')+'\nLetzte Qualität: '+(k.last_quality||m.latest_snapshot_quality?.status||'—')+'\nFehler in Folge: '+k.consecutive_failures+'\nLetzter Fehlertyp: '+(k.last_error_class||'—')+'\nQuelle: '+(k.last_source||m.latest_snapshot_source||'—')+'\nProvider: '+JSON.stringify(m.latest_snapshot_providers||[]));
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
