import os, sqlite3, hashlib, hmac, secrets, json, time
from pathlib import Path
from urllib.parse import urlparse

DB_URL = os.getenv("DATABASE_URL","").strip() or os.getenv("POSTGRES_URL","").strip()
JWT_SECRET = os.getenv("SYNPORA_JWT_SECRET","change-me-in-production")
DB_PATH = os.getenv("SYNPORA_SQLITE_PATH","/tmp/synpora.db")

def _conn():
    if DB_URL:
        try:
            import psycopg
            return psycopg.connect(DB_URL, autocommit=True)
        except Exception as e:
            if os.getenv("SYNPORA_REQUIRE_DATABASE","0")=="1":
                raise RuntimeError("PostgreSQL connection required but unavailable") from e
    c=sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=_conn()
    if DB_URL and c.__class__.__module__.startswith("psycopg"):
        c.execute("""CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS farms(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,farm_id TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,power_kw DOUBLE PRECISION DEFAULT 0,created_at DOUBLE PRECISION NOT NULL)""")
    else:
        c.executescript("""CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS farms(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,farm_id TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,power_kw REAL DEFAULT 0,created_at REAL NOT NULL);""")
        c.commit()
    return c

def _hash(pw,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+"$"+hashlib.pbkdf2_hmac("sha256",pw.encode(),bytes.fromhex(salt),210000).hex()
def _verify(pw,stored):
    try:
        salt,_=stored.split("$",1); return hmac.compare_digest(_hash(pw,salt),stored)
    except Exception: return False
def _token(uid):
    import base64
    body=json.dumps({"uid":uid,"exp":int(time.time())+86400},separators=(",",":")).encode()
    b=base64.urlsafe_b64encode(body).decode().rstrip("=")
    sig=hmac.new(JWT_SECRET.encode(),b.encode(),hashlib.sha256).hexdigest()
    return b+"."+sig
def _uid(token):
    import base64
    try:
        b,sig=token.split(".",1)
        if not hmac.compare_digest(sig,hmac.new(JWT_SECRET.encode(),b.encode(),hashlib.sha256).hexdigest()): return None
        raw=base64.urlsafe_b64decode(b+"="*((4-len(b)%4)%4)); d=json.loads(raw)
        return d["uid"] if d["exp"]>time.time() else None
    except Exception: return None

def install(app):
    from fastapi import Header, HTTPException
    from pydantic import BaseModel
    init_db()
    @app.get("/api/v1/system/status")
    def status():
        return {"service":"synpora","version":"1.1.0","database":{"type":"postgresql" if DB_URL else "sqlite_fallback","configured":bool(DB_URL)},"hardware_write":False,"mode":"recommendation_only"}

    class OptimizeIn(BaseModel):
        energy_kwh: float=10
        energy_cost_eur_kwh: float=0.05
        ai_value_eur_kwh: float=0.0
        gpu_hourly_usd: float=1.09
        gpu_power_kw: float=0.35
        gpu_utilization: float=0.70
        gpu_platform_fee: float=0.15
        battery_value_eur_kwh: float=0.071
        grid_value_eur_kwh: float=0.055
        btc_hashprice_usd_ph_day: float=39.64
        eur_usd: float=1.1205
        asic_efficiency_j_th: float=20.0
        uptime: float=0.98
        pool_fee: float=0.02

    @app.get("/api/v1/market/live")
    def live_market():
        ext=_external_market()
        return {
            "btc_price_usd":ext.get("btcPrice"),
            "btc_hashprice_usd_ph_day":ext.get("hashpriceUsd") or 38.75,
            "btc_difficulty":ext.get("difficulty"),
            "network_hashrate_eh":ext.get("networkHashrate"),
            "eur_usd":1.1205,
            "austria_spot_eur_kwh":0.2055,
            "gpu":{"model":"L40S","hourly_usd":1.09,"power_kw":0.35,"utilization":0.70,"platform_fee":0.15,"source":"RunPod Secure Cloud"},
            "sources":["Startmining API","EUR/USD reference","EPEX Spot AT reference","RunPod pricing"],
            "timestamp":time.time()
        }

    @app.post("/api/v1/farms/{farm_id}/optimize")
    def optimize(farm_id:str,x:OptimizeIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        ok=c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone()
        if not ok: raise HTTPException(404,"Farm not found")
        # BTC gross revenue per kWh = hashprice / (J/TH) / 1000, adjusted for uptime/pool fee.
        btc_gross=(x.btc_hashprice_usd_ph_day/x.eur_usd)/(x.asic_efficiency_j_th*1000)
        btc_value=max(0,btc_gross*x.uptime*(1-x.pool_fee))
        # GPU revenue/kWh converts a market GPU-hour into energy economics.
        # Revenue is haircut by utilization and platform fee; power includes only the GPU load.
        gpu_revenue_per_kwh=((x.gpu_hourly_usd/x.eur_usd)*x.gpu_utilization*(1-x.gpu_platform_fee))/max(x.gpu_power_kw,0.01)
        ai_value=max(0,gpu_revenue_per_kwh)
        options=[
          {"option":"AI Compute","value_eur_kwh":ai_value,"source":"live_gpu_market"},
          {"option":"BTC Mining","value_eur_kwh":btc_value,"source":"live_hashprice"},
          {"option":"Battery","value_eur_kwh":x.battery_value_eur_kwh,"source":"farm_model"},
          {"option":"Grid","value_eur_kwh":x.grid_value_eur_kwh,"source":"energy_model"}
        ]
        options.sort(key=lambda z:z["value_eur_kwh"],reverse=True)
        best=options[0]
        gross=best["value_eur_kwh"]*x.energy_kwh
        cost=x.energy_cost_eur_kwh*x.energy_kwh
        spread=(best["value_eur_kwh"]-options[1]["value_eur_kwh"]) / max(best["value_eur_kwh"],0.0001)\n        confidence=max(0.55,min(0.97,0.72+0.22*spread))\n        learned_confidence=None
        try:
            _ensure_learning_tables(c)
            lr=c.execute("SELECT actual_value,predicted_value FROM decision_ledger WHERE farm_id=? AND chosen=? AND status='settled' AND actual_value IS NOT NULL ORDER BY ts DESC LIMIT 50",(farm_id,best["option"])).fetchall()
            if len(lr)>=5:
                mae=sum(abs(float(r[0])-float(r[1])) for r in lr)/len(lr)
                hit=sum(1 for r in lr if float(r[0])>=float(r[1]))/len(lr)
                learned_confidence=max(0.50,min(0.98,0.45+0.40*hit+0.15*(1/(1+mae*10))))
                confidence=round((confidence+learned_confidence)/2,2)
        except Exception:
            pass
        risk={"AI Compute":"market_price_and_utilization","BTC Mining":"hashprice_and_difficulty","Battery":"cycle_and_tariff_assumptions","Grid":"spot_price_volatility"}[best["option"]]\n        try:\n            series_key="gpu" if best["option"]=="AI Compute" else ("btc" if best["option"]=="BTC Mining" else "energy")\n            forecast_confidence=_ensemble_confidence(c,series_key)\n            confidence=round(min(confidence,forecast_confidence) if forecast_confidence<0.65 else confidence,2)\n        except Exception:\n            forecast_confidence=0.55\n        return {"farm_id":farm_id,"energy_kwh":x.energy_kwh,"best":best,"alternatives":options[1:],\n                "gross_value_eur":round(gross,2),"energy_cost_eur":round(cost,2),\n                "net_value_eur":round(gross-cost,2),"confidence":round(confidence,2),"risk":risk,"learned_confidence":learned_confidence,\n                "market":{"btc_hashprice_usd_ph_day":x.btc_hashprice_usd_ph_day,"eur_usd":x.eur_usd,"gpu_hourly_usd":x.gpu_hourly_usd,"gpu_power_kw":x.gpu_power_kw,"gpu_utilization":x.gpu_utilization,"gpu_platform_fee":x.gpu_platform_fee},\n                "mode":"recommendation_only","hardware_write":False}

    class ScenarioIn(BaseModel):
        energy_kwh: float=100
        energy_cost_eur_kwh: float=0.05
        btc_hashprice_usd_ph_day: float=39.64
        eur_usd: float=1.1205
        gpu_hourly_usd: float=1.09
        gpu_power_kw: float=0.35
        gpu_utilization: float=0.70
        gpu_platform_fee: float=0.15
        asic_efficiency_j_th: float=20.0
        battery_value_eur_kwh: float=0.071
        grid_value_eur_kwh: float=0.055

    def _economics(x):
        btc=((x.btc_hashprice_usd_ph_day/x.eur_usd)/(x.asic_efficiency_j_th*1000))*0.98*0.98
        gpu=((x.gpu_hourly_usd/x.eur_usd)*x.gpu_utilization*(1-x.gpu_platform_fee))/max(x.gpu_power_kw,0.01)
        return {"AI Compute":max(0,gpu),"BTC Mining":max(0,btc),"Battery":x.battery_value_eur_kwh,"Grid":x.grid_value_eur_kwh}

    def _external_market():
        import urllib.request
        out={}
        try:
            req=urllib.request.Request("https://pro.startmining.io/api/market-summary",headers={"User-Agent":"SYNPORA/1.0"})
            with urllib.request.urlopen(req,timeout=5) as r: out.update(json.loads(r.read().decode()))
        except Exception:
            pass
        return out

    @app.post("/api/v1/market/collect")
    def collect_market():
        ext=_external_market()
        snap={"timestamp":time.time(),
              "btc_price_usd":ext.get("btcPrice"),
              "btc_hashprice_usd_ph_day":ext.get("hashpriceUsd"),
              "btc_difficulty":ext.get("difficulty"),
              "network_hashrate_eh":ext.get("networkHashrate"),
              "eur_usd":1.1205,
              "gpu_l40s_usd_hour":1.09,
              "gpu_l40s_power_kw":0.35,
              "gpu_utilization":0.70,
              "gpu_platform_fee":0.15,
              "source":"Startmining API + RunPod reference" if ext else "fallback"}
        c=init_db()
        if DB_URL and c.__class__.__module__.startswith("psycopg"):
            c.execute("CREATE TABLE IF NOT EXISTS market_snapshots(id TEXT PRIMARY KEY,ts DOUBLE PRECISION NOT NULL,payload TEXT NOT NULL)")
            c.execute("INSERT INTO market_snapshots VALUES(%s,%s,%s)",(secrets.token_hex(12),snap["timestamp"],json.dumps(snap)))
        else:
            c.execute("CREATE TABLE IF NOT EXISTS market_snapshots(id TEXT PRIMARY KEY,ts REAL NOT NULL,payload TEXT NOT NULL)")
            c.execute("INSERT INTO market_snapshots VALUES(?,?,?)",(secrets.token_hex(12),snap["timestamp"],json.dumps(snap))); c.commit()
        return snap

    @app.post("/api/v1/market/backfill")
    def backfill_market(limit:int=365):
        import urllib.request
        url="https://pro.startmining.io/api/price-history"
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"SYNPORA/1.0"})
            with urllib.request.urlopen(req,timeout=8) as r: data=json.loads(r.read().decode())
        except Exception as e:
            raise HTTPException(502,"Historical market source unavailable")
        rows=data if isinstance(data,list) else data.get("data",[])
        rows=rows[-min(limit,365):]
        c=init_db()
        c.execute("CREATE TABLE IF NOT EXISTS market_snapshots(id TEXT PRIMARY KEY,ts REAL NOT NULL,payload TEXT NOT NULL)")
        inserted=0
        for row in rows:
            if isinstance(row,dict):
                ts=row.get("timestamp") or row.get("time") or row.get("ts")
            else:
                ts=row[0] if len(row)>1 else None
            if ts:
                payload=json.dumps(row)
                try:
                    c.execute("INSERT INTO market_snapshots VALUES(?,?,?)",(secrets.token_hex(12),float(ts)/1000 if float(ts)>1e11 else float(ts),payload)); inserted+=1
                except Exception: pass
        try: c.commit()
        except Exception: pass
        return {"inserted":inserted,"requested":limit,"source":"Startmining price history"}

    def _ensure_learning_tables(c):
        c.execute("CREATE TABLE IF NOT EXISTS decision_ledger(id TEXT PRIMARY KEY,farm_id TEXT NOT NULL,ts REAL NOT NULL,chosen TEXT NOT NULL,predicted_value REAL NOT NULL,confidence REAL NOT NULL,status TEXT NOT NULL,actual_value REAL,settled_at REAL)")
        try: c.commit()
        except Exception: pass

    @app.post("/api/v1/farms/{farm_id}/decision")
    def record_decision(farm_id:str,x:ScenarioIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        vals=_economics(x); chosen=max(vals,key=vals.get); did=secrets.token_hex(12)
        c.execute("INSERT INTO decision_ledger VALUES(?,?,?,?,?,?,?,?,?)",(did,farm_id,time.time(),chosen,vals[chosen],0.80,"open",None,None))
        try: c.commit()
        except Exception: pass
        return {"decision_id":did,"chosen":chosen,"predicted_value_eur_kwh":round(vals[chosen],6),"confidence":0.80,"status":"open"}

    @app.post("/api/v1/market/snapshot")
    def market_snapshot():
        c=init_db()
        snap={"timestamp":time.time(),"btc_hashprice_usd_ph_day":39.6395,"eur_usd":1.1205,
              "gpu_l40s_usd_hour":1.09,"gpu_l40s_power_kw":0.35}
        # SQLite/Postgres-compatible JSON snapshot store.
        if DB_URL and c.__class__.__module__.startswith("psycopg"):
            c.execute("CREATE TABLE IF NOT EXISTS market_snapshots(id TEXT PRIMARY KEY,ts DOUBLE PRECISION NOT NULL,payload TEXT NOT NULL)")
            c.execute("INSERT INTO market_snapshots VALUES(%s,%s,%s)",(secrets.token_hex(12),snap["timestamp"],json.dumps(snap)))
        else:
            c.execute("CREATE TABLE IF NOT EXISTS market_snapshots(id TEXT PRIMARY KEY,ts REAL NOT NULL,payload TEXT NOT NULL)")
            c.execute("INSERT INTO market_snapshots VALUES(?,?,?)",(secrets.token_hex(12),snap["timestamp"],json.dumps(snap))); c.commit()
        return snap

    @app.get("/api/v1/farms/{farm_id}/benchmark")
    def benchmark(farm_id:str,limit:int=500,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        try:
            rows=c.execute("SELECT ts,payload FROM market_snapshots ORDER BY ts ASC LIMIT ?",(min(limit,1000),)).fetchall()
        except Exception:
            rows=[]
        ai_total=btc_total=0.0; points=[]
        for r in rows:
            d=json.loads(r[1]); h=d.get("btc_hashprice_usd_ph_day")
            g=d.get("gpu_l40s_usd_hour")
            if not h or not g: continue
            s=ScenarioIn(energy_kwh=100,energy_cost_eur_kwh=0.05,btc_hashprice_usd_ph_day=float(h),gpu_hourly_usd=float(g))
            vals=_economics(s)
            ai=(vals["AI Compute"]-s.energy_cost_eur_kwh)*100
            btc=(vals["BTC Mining"]-s.energy_cost_eur_kwh)*100
            ai_total+=ai; btc_total+=btc
            points.append({"timestamp":r[0],"ai_net_eur":round(ai,2),"btc_net_eur":round(btc,2),"winner":"AI Compute" if ai>btc else "BTC Mining"})
        return {"farm_id":farm_id,"points":points,"samples":len(points),
                "ai_total_net_eur":round(ai_total,2),"btc_total_net_eur":round(btc_total,2),
                "ai_delta_vs_btc_eur":round(ai_total-btc_total,2),
                "winner_share":round(sum(p["winner"]=="AI Compute" for p in points)/len(points),3) if points else None,
                "method":"stored_market_snapshots","recommendation_only":True}

    @app.post("/api/v1/farms/{farm_id}/learning/settle")
    def settle_learning(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        rows=c.execute("SELECT id,chosen,predicted_value FROM decision_ledger WHERE farm_id=? AND status='open' ORDER BY ts LIMIT 100",(farm_id,)).fetchall()
        markets=c.execute("SELECT ts,payload FROM market_snapshots ORDER BY ts").fetchall()
        errors=[]
        for r in rows:
            if not markets: continue
            d=json.loads(markets[-1][1]); h=d.get("btc_hashprice_usd_ph_day"); g=d.get("gpu_l40s_usd_hour")
            if not h or not g: continue
            s=ScenarioIn(btc_hashprice_usd_ph_day=float(h),gpu_hourly_usd=float(g))
            actual=_economics(s).get(r[1],0); errors.append(actual-float(r[2]))
            c.execute("UPDATE decision_ledger SET status='settled',actual_value=?,settled_at=? WHERE id=?",(actual,time.time(),r[0]))
        try: c.commit()
        except Exception: pass
        mae=sum(abs(e) for e in errors)/len(errors) if errors else None
        return {"settled":len(errors),"mae_eur_kwh":round(mae,6) if mae is not None else None}

    @app.get("/api/v1/farms/{farm_id}/learning/calibration")
    def learning_calibration(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        rows=c.execute("SELECT chosen,predicted_value,actual_value,confidence FROM decision_ledger WHERE farm_id=? AND status='settled' AND actual_value IS NOT NULL ORDER BY ts DESC LIMIT 500",(farm_id,)).fetchall()
        by={}
        for r in rows:
            name=str(r[0]); by.setdefault(name,[]).append(r)
        result={}
        for name,items in by.items():
            errors=[abs(float(r[2])-float(r[1])) for r in items]
            hits=[1 if float(r[2])>=float(r[1]) else 0 for r in items]
            mae=sum(errors)/len(errors)
            hit=sum(hits)/len(hits)
            # Confidence combines directional hit-rate and normalized error.
            calibrated=max(0.50,min(0.98,0.45+0.40*hit+0.15*(1/(1+mae*10))))
            result[name]={"samples":len(items),"hit_rate":round(hit,3),"mae_eur_kwh":round(mae,6),"calibrated_confidence":round(calibrated,3)}
        overall=max(result.values(),key=lambda x:x["calibrated_confidence"])["calibrated_confidence"] if result else 0.70
        return {"farm_id":farm_id,"strategies":result,"overall_confidence":overall,"calibration_ready":len(rows)>=5}

    @app.get("/api/v1/farms/{farm_id}/model-score")
    def model_score(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        rows=c.execute("SELECT chosen,predicted_value,actual_value FROM decision_ledger WHERE farm_id=? AND status='settled' AND actual_value IS NOT NULL",(farm_id,)).fetchall()
        if not rows: return {"samples":0,"score":0.0,"status":"cold_start"}
        mae=sum(abs(float(r[2])-float(r[1])) for r in rows)/len(rows)
        directional=sum(1 for r in rows if float(r[2])>=float(r[1]))/len(rows)
        score=max(0,min(100,50*directional+50*(1/(1+mae*10))))
        return {"samples":len(rows),"mae_eur_kwh":round(mae,6),"directional_accuracy":round(directional,3),"score":round(score,1),"status":"learning"}

    @app.get("/api/v1/farms/{farm_id}/learning")
    def learning_status(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        rows=c.execute("SELECT status,predicted_value,actual_value,confidence FROM decision_ledger WHERE farm_id=? ORDER BY ts DESC LIMIT 500").fetchall()
        settled=[r for r in rows if r[0]=="settled" and r[2] is not None]
        mae=sum(abs(float(r[2])-float(r[1])) for r in settled)/len(settled) if settled else None
        return {"samples":len(rows),"settled":len(settled),"open":len(rows)-len(settled),"mae_eur_kwh":round(mae,6) if mae is not None else None,"learning_ready":len(settled)>=5}

    @app.get("/api/v1/market/history")
    def market_history(limit:int=100):
        c=init_db()
        try:
            rows=c.execute("SELECT ts,payload FROM market_snapshots ORDER BY ts DESC LIMIT ?",(min(limit,500),)).fetchall()
        except Exception:
            rows=[]
        return [{"timestamp":r[0],"data":json.loads(r[1])} for r in rows]

    class PortfolioIn(BaseModel):
        energy_kwh: float=100
        horizon_hours: float=1
        energy_cost_eur_kwh: float=0.05
        btc_hashprice_usd_ph_day: float=38.75
        eur_usd: float=1.1205
        gpu_hourly_usd: float=1.09
        gpu_utilization: float=0.70
        gpu_platform_fee: float=0.15
        asic_efficiency_j_th: float=20.0

    @app.post("/api/v1/farms/{farm_id}/portfolio-optimize")
    def portfolio_optimize(farm_id:str,x:PortfolioIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        assets=[dict(r) for r in c.execute("SELECT id,name,kind,power_kw FROM assets WHERE farm_id=? ORDER BY created_at",(farm_id,)).fetchall()]
        candidates=[]
        for a in assets:
            kind=a["kind"].upper(); power=max(float(a["power_kw"] or 0),0.01)
            if kind=="GPU":
                value=(x.gpu_hourly_usd/x.eur_usd)*x.gpu_utilization*(1-x.gpu_platform_fee)/power
                candidates.append({"asset_id":a["id"],"asset":a["name"],"kind":"GPU","value_eur_kwh":value,"capacity_kwh":power*x.horizon_hours})
            elif kind=="BTC":
                value=(x.btc_hashprice_usd_ph_day/x.eur_usd)/(x.asic_efficiency_j_th*1000)*0.98*0.98
                candidates.append({"asset_id":a["id"],"asset":a["name"],"kind":"BTC","value_eur_kwh":value,"capacity_kwh":power*x.horizon_hours})
        candidates.sort(key=lambda z:z["value_eur_kwh"],reverse=True)
        remaining=max(0,x.energy_kwh); allocation=[]
        for a in candidates:
            kwh=min(remaining,a["capacity_kwh"])
            if kwh>0:
                allocation.append({**a,"allocated_kwh":round(kwh,3),"net_eur":round((a["value_eur_kwh"]-x.energy_cost_eur_kwh)*kwh,2)})
                remaining-=kwh
        grid_value=0.055
        if remaining>0:
            allocation.append({"asset_id":None,"asset":"Grid/Unallocated","kind":"GRID","value_eur_kwh":grid_value,"capacity_kwh":remaining,"allocated_kwh":round(remaining,3),"net_eur":round((grid_value-x.energy_cost_eur_kwh)*remaining,2)})
        total=sum(a["net_eur"] for a in allocation)
        return {"farm_id":farm_id,"energy_kwh":x.energy_kwh,"horizon_hours":x.horizon_hours,"allocation":allocation,
                "unallocated_kwh":round(max(0,remaining),3),"total_net_eur":round(total,2),
                "objective":"maximize_net_value","recommendation_only":True,"hardware_write":False}

    class DispatchIn(BaseModel):
        horizon_hours: int=24
        interval_hours: float=1
        energy_cost_eur_kwh: float=0.05
        pv_kwh: float=0
        battery_soc_pct: float=50
        battery_capacity_kwh: float=100
        battery_reserve_pct: float=20
        battery_value_eur_kwh: float=0.071
        grid_value_eur_kwh: float=0.055
        btc_hashprice_usd_ph_day: float=38.75
        eur_usd: float=1.1205
        gpu_hourly_usd: float=1.09
        gpu_utilization: float=0.70
        gpu_platform_fee: float=0.15
        asic_efficiency_j_th: float=20.0

    def _adaptive_model_weights(c, series_key):
        rows=c.execute("SELECT ts,btc_hashprice_usd_ph_day,gpu_hourly_usd,austria_spot_eur_kwh FROM market_snapshots ORDER BY ts DESC LIMIT 168").fetchall()
        idx={"btc":1,"gpu":2,"energy":3}[series_key]
        y=[float(r[idx]) for r in rows if r[idx] is not None]
        if len(y)<8:
            return {"recent_mean":.34,"last_value":.33,"trend":.33}
        cut=min(48,len(y)-4); test=y[:cut]; train=y[cut:]
        mean=sum(train)/len(train); last=train[0]
        slope=(train[0]-train[-1])/(len(train)-1) if len(train)>1 else 0
        preds={"recent_mean":[mean]*len(test),"last_value":[last]*len(test),
               "trend":[train[0]+slope*(i+1) for i in range(len(test))]}
        errors={k:sum(abs(a-b) for a,b in zip(test,v))/len(test) for k,v in preds.items()}
        inv={k:1.0/(v+1e-9) for k,v in errors.items()}
        total=sum(inv.values())
        return {k:round(v/total,4) for k,v in inv.items()}

    def _ensemble_forecast(c, series_key, horizon, fallback):
        weights=_adaptive_model_weights(c,series_key)
        rows=c.execute("SELECT btc_hashprice_usd_ph_day,gpu_hourly_usd,austria_spot_eur_kwh FROM market_snapshots ORDER BY ts DESC LIMIT 48").fetchall()
        idx={"btc":0,"gpu":1,"energy":2}[series_key]
        y=[float(r[idx]) for r in rows if r[idx] is not None]
        if not y: return [fallback]*horizon,weights
        mean=sum(y)/len(y); last=y[0]
        slope=(y[0]-y[-1])/(len(y)-1) if len(y)>1 else 0
        out=[]
        for h in range(1,horizon+1):
            pred=(weights["recent_mean"]*mean + weights["last_value"]*last +
                  weights["trend"]*(last+slope*h))
            out.append(pred)
        return out,weights

    def _model_select(c):
        rows=c.execute("SELECT ts,btc_hashprice_usd_ph_day,gpu_hourly_usd,austria_spot_eur_kwh FROM market_snapshots WHERE btc_hashprice_usd_ph_day IS NOT NULL ORDER BY ts DESC LIMIT 168").fetchall()
        result={}
        for name,idx in (("btc",1),("gpu",2),("energy",3)):
            y=[float(r[idx]) for r in rows if r[idx] is not None]
            if len(y)<8:
                result[name]={"model":"recent_mean","mae":None,"samples":len(y),"ready":False}
                continue
            cut=min(48,len(y)-4); test=y[:cut]; train=y[cut:]
            mean=sum(train)/len(train); last=train[0]
            preds={"recent_mean":[mean]*len(test),"last_value":[last]*len(test)}
            slope=(train[0]-train[-1])/(len(train)-1) if len(train)>1 else 0
            preds["trend"]=[train[0]+slope*(i+1) for i in range(len(test))]
            scores={k:sum(abs(a-b) for a,b in zip(test,v))/len(test) for k,v in preds.items()}
            best=min(scores,key=scores.get)
            result[name]={"model":best,"mae":round(scores[best],8),"scores":{k:round(v,8) for k,v in scores.items()},"samples":len(y),"ready":len(y)>=24}
        return result

    def _ensemble_confidence(c, series_key):
        w=_adaptive_model_weights(c,series_key)
        vals=list(w.values())
        concentration=max(vals) if vals else 0.33
        rows=c.execute("SELECT btc_hashprice_usd_ph_day,gpu_hourly_usd,austria_spot_eur_kwh FROM market_snapshots ORDER BY ts DESC LIMIT 48").fetchall()
        idx={"btc":0,"gpu":1,"energy":2}[series_key]
        y=[float(r[idx]) for r in rows if r[idx] is not None]
        if len(y)<8:
            return 0.55
        mean=sum(y)/len(y)
        volatility=(sum(abs(v-mean) for v in y)/len(y))/(abs(mean)+1e-9)
        stability=max(0.0,min(1.0,1.0-volatility))
        return round(max(.55,min(.95,.55+.25*concentration+.20*stability)),3)

    @app.post("/api/v1/farms/{farm_id}/forecast-plan")
    def forecast_plan(farm_id:str,x:DispatchIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        learn=_forecast_learning(c)
        models=_model_select(c)\n        conf={"btc":_ensemble_confidence(c,"btc"),"gpu":_ensemble_confidence(c,"gpu"),"energy":_ensemble_confidence(c,"energy")}
        hours=max(1,min(72,x.horizon_hours))
        btc_fc,btc_w=_ensemble_forecast(c,"btc",hours,x.btc_hashprice_usd_ph_day)
        gpu_fc,gpu_w=_ensemble_forecast(c,"gpu",hours,x.gpu_hourly_usd)
        energy_fc,energy_w=_ensemble_forecast(c,"energy",hours,x.energy_cost_eur_kwh)
        import math
        rows=[]
        for h in range(hours):
            # learned baseline: recent observed level + transparent cyclical prior
            recent=c.execute("SELECT btc_hashprice_usd_ph_day,gpu_hourly_usd,austria_spot_eur_kwh FROM market_snapshots ORDER BY ts DESC LIMIT 24").fetchall()
            def avg(idx,default):
                a=[float(r[idx]) for r in recent if r[idx] is not None]
                return sum(a)/len(a) if a else default
            btc0=avg(0,x.btc_hashprice_usd_ph_day); gpu0=avg(1,x.gpu_hourly_usd)
            pv_shape=max(0.0,math.sin((h+1)/hours*math.pi))
            btc=btc_fc[h]; gpu=gpu_fc[h]; energy=energy_fc[h]
            pv=x.pv_kwh/hours*(0.35+1.3*pv_shape)
            gpu_v=(gpu/x.eur_usd)*x.gpu_utilization*(1-x.gpu_platform_fee)/0.35
            btc_v=(btc/x.eur_usd)/(x.asic_efficiency_j_th*1000)*0.98*0.98
            rows.append({"hour":h,"pv_kwh":round(pv,3),"gpu_hourly_usd":round(gpu,4),
                         "btc_hashprice_usd_ph_day":round(btc,4),"gpu_value_eur_kwh":round(gpu_v,5),"forecast_energy_cost_eur_kwh":round(energy,5),
                         "btc_value_eur_kwh":round(btc_v,5),"best_option":"AI Compute" if gpu_v>=btc_v else "BTC Mining"})
        return {"farm_id":farm_id,"horizon_hours":hours,"forecast":rows,
                "method":"historical_adaptive_model_selection","learning":learn,"models":models,"ensemble_weights":{"btc":btc_w,"gpu":gpu_w,"energy":energy_w},"ensemble_confidence":conf,
                "recommendation_only":True,"hardware_write":False}

    @app.post("/api/v1/farms/{farm_id}/dispatch-plan")
    def dispatch_plan(farm_id:str,x:DispatchIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        assets=[dict(r) for r in c.execute("SELECT id,name,kind,power_kw FROM assets WHERE farm_id=? ORDER BY created_at",(farm_id,)).fetchall()]
        gpu_value=(x.gpu_hourly_usd/x.eur_usd)*x.gpu_utilization*(1-x.gpu_platform_fee)
        btc_value=(x.btc_hashprice_usd_ph_day/x.eur_usd)/(x.asic_efficiency_j_th*1000)*0.98*0.98
        candidates=[]
        for a in assets:
            kind=a["kind"].upper(); power=max(float(a["power_kw"] or 0),0)
            if kind=="GPU": candidates.append({"id":a["id"],"name":a["name"],"kind":kind,"power_kw":power,"value":gpu_value})
            elif kind=="BTC": candidates.append({"id":a["id"],"name":a["name"],"kind":kind,"power_kw":power,"value":btc_value})
        hours=max(1,min(168,x.horizon_hours)); dt=max(0.25,x.interval_hours)
        reserve=max(0,min(100,x.battery_reserve_pct))
        battery=max(0,(x.battery_soc_pct-reserve)/100*x.battery_capacity_kwh)
        # Joint horizon optimization: rank every asset-hour opportunity globally,
        # then allocate the finite PV/battery/asset capacity to the highest-value slots.
        opportunities=[]
        for h in range(hours):
            for a in candidates:
                kwh=a["power_kw"]*dt
                if kwh>0: opportunities.append({"hour":h,"asset":a,"capacity_kwh":kwh,"value":a["value"]})
        opportunities.sort(key=lambda z:z["value"],reverse=True)
        pv_budget=max(0,x.pv_kwh)
        allocations={}
        for op in opportunities:
            supply=min(op["capacity_kwh"],pv_budget+battery)
            if supply<=0: continue
            pv_used=min(supply,pv_budget); pv_budget-=pv_used
            batt_used=supply-pv_used; battery=max(0,battery-batt_used)
            key=(op["hour"],op["asset"]["id"])
            allocations[key]={"hour":op["hour"],"asset":op["asset"]["name"],"kind":op["asset"]["kind"],
                              "energy_kwh":round(supply,3),"source":"PV" if batt_used==0 else ("PV+Battery" if pv_used else "Battery"),
                              "value_eur_kwh":round(op["value"],5),
                              "net_eur":round((op["value"]-x.energy_cost_eur_kwh)*supply,2)}
        plan=sorted(allocations.values(),key=lambda z:z["hour"])
        total=sum(r["net_eur"] for r in plan)
        used=sum(r["energy_kwh"] for r in plan)
        return {"farm_id":farm_id,"horizon_hours":hours,"interval_hours":dt,"plan":plan,
                "total_net_eur":round(total,2),"energy_allocated_kwh":round(used,3),
                "energy_unallocated_kwh":round(max(0,x.pv_kwh+max(0,(x.battery_soc_pct-reserve)/100*x.battery_capacity_kwh)-used),3),
                "battery_remaining_kwh":round(battery,2),"pv_remaining_kwh":round(pv_budget,2),
                "constraints":{"battery_reserve_pct":reserve,"battery_capacity_kwh":x.battery_capacity_kwh},
                "optimizer":"multi_period_value_rank","mode":"recommendation_only","hardware_write":False}

    @app.post("/api/v1/farms/{farm_id}/scenario")
    def scenario(farm_id:str,x:ScenarioIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        values=_economics(x)
        assets=[dict(r) for r in c.execute("SELECT id,name,kind,power_kw FROM assets WHERE farm_id=? ORDER BY created_at",(farm_id,)).fetchall()]\n        for a in assets:\n            if a["kind"].upper()=="GPU" and a["power_kw"]>0: values["AI Compute"]=(x.gpu_hourly_usd/x.eur_usd)*x.gpu_utilization*(1-x.gpu_platform_fee)/a["power_kw"]\n            if a["kind"].upper()=="BTC" and a["power_kw"]>0: values["BTC Mining"]=(x.btc_hashprice_usd_ph_day/x.eur_usd)/(x.asic_efficiency_j_th*1000)*0.98*0.98\n        rows=[{"option":k,"value_eur_kwh":round(v,5),"net_eur":round((v-x.energy_cost_eur_kwh)*x.energy_kwh,2)} for k,v in values.items()]
        rows.sort(key=lambda r:r["value_eur_kwh"],reverse=True)
        best=rows[0]
        return {"farm_id":farm_id,"inputs":x.model_dump(),"ranking":rows,"best":best,
                "spread_eur_kwh":round(best["value_eur_kwh"]-rows[1]["value_eur_kwh"],5),
                "recommendation_only":True,"hardware_write":False}

    @app.post("/api/v1/farms/{farm_id}/backtest")
    def backtest(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        base=ScenarioIn()
        multipliers=[0.65,0.8,0.95,1.0,1.1,1.25,1.4]
        samples=[]; total_ai=total_btc=0.0
        for m in multipliers:
            s=ScenarioIn(energy_kwh=100,energy_cost_eur_kwh=0.05,btc_hashprice_usd_ph_day=base.btc_hashprice_usd_ph_day*m,gpu_hourly_usd=base.gpu_hourly_usd*m)
            vals=_economics(s); best=max(vals,key=vals.get)
            samples.append({"market_multiplier":m,"best":best,"best_value_eur_kwh":round(vals[best],5),"ai_net_eur":round((vals["AI Compute"]-s.energy_cost_eur_kwh)*100,2),"btc_net_eur":round((vals["BTC Mining"]-s.energy_cost_eur_kwh)*100,2)})
            total_ai+=(vals["AI Compute"]-s.energy_cost_eur_kwh)*100; total_btc+=(vals["BTC Mining"]-s.energy_cost_eur_kwh)*100
        wins={k:sum(1 for s in samples if s["best"]==k) for k in ["AI Compute","BTC Mining","Battery","Grid"]}
        return {"farm_id":farm_id,"samples":samples,"wins":wins,"cumulative_net_eur":{"ai":round(total_ai,2),"btc":round(total_btc,2),"delta_ai_vs_btc":round(total_ai-total_btc,2)},"method":"synthetic_market_sensitivity","recommendation_only":True}

    class AuthIn(BaseModel):
        email: str
        password: str
    class FarmIn(BaseModel):
        name: str
    class AssetIn(BaseModel):
        name: str
        kind: str
        power_kw: float=0

    def user(authorization):
        if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Authentication required")
        uid=_uid(authorization[7:])
        if not uid: raise HTTPException(401,"Invalid or expired token")
        return uid

    @app.post("/api/v1/auth/register")
    def register(x:AuthIn):
        if len(x.password)<8: raise HTTPException(400,"Password must be at least 8 characters")
        c=init_db(); uid=secrets.token_hex(12)
        try:
            c.execute("INSERT INTO users VALUES(?,?,?,?)",(uid,x.email.lower(),_hash(x.password),time.time()))
        except Exception: raise HTTPException(409,"Email already registered")
        farm_id=secrets.token_hex(12); c.execute("INSERT INTO farms VALUES(?,?,?,?)",(farm_id,uid,"My first farm",time.time()))
        return {"token":_token(uid),"user":{"id":uid,"email":x.email.lower()},"farm":{"id":farm_id,"name":"My first farm"}}

    @app.post("/api/v1/auth/login")
    def login(x:AuthIn):
        c=init_db(); row=c.execute("SELECT * FROM users WHERE email=?",(x.email.lower(),)).fetchone()
        if not row or not _verify(x.password,row["password_hash"]): raise HTTPException(401,"Invalid credentials")
        return {"token":_token(row["id"]),"user":{"id":row["id"],"email":row["email"]}}

    @app.get("/api/v1/auth/me")
    def me(authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db(); row=c.execute("SELECT id,email,created_at FROM users WHERE id=?",(uid,)).fetchone()
        if not row: raise HTTPException(404,"User not found")
        return dict(row)

    @app.get("/api/v1/farms")
    def farms(authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        return [dict(r) for r in c.execute("SELECT id,name,created_at FROM farms WHERE user_id=? ORDER BY created_at",(uid,)).fetchall()]

    @app.post("/api/v1/farms")
    def create_farm(x:FarmIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db(); fid=secrets.token_hex(12)
        c.execute("INSERT INTO farms VALUES(?,?,?,?)",(fid,uid,x.name.strip()[:120] or "Farm",time.time()))
        return {"id":fid,"name":x.name.strip()[:120] or "Farm"}

    @app.get("/api/v1/farms/{farm_id}/assets")
    def assets(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        ok=c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone()
        if not ok: raise HTTPException(404,"Farm not found")
        return [dict(r) for r in c.execute("SELECT id,name,kind,power_kw,created_at FROM assets WHERE farm_id=? ORDER BY created_at",(farm_id,)).fetchall()]

    @app.post("/api/v1/farms/{farm_id}/assets")
    def create_asset(farm_id:str,x:AssetIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        ok=c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone()
        if not ok: raise HTTPException(404,"Farm not found")
        aid=secrets.token_hex(12); c.execute("INSERT INTO assets VALUES(?,?,?,?,?,?)",(aid,farm_id,x.name.strip()[:120],x.kind.strip()[:50],x.power_kw,time.time()))
        return {"id":aid,"name":x.name.strip()[:120],"kind":x.kind.strip()[:50],"power_kw":x.power_kw}

    @app.get("/api/v1/system/database")
    def database():
        return {"persistent_database":"postgresql" if DB_URL else "sqlite_fallback","configured":bool(DB_URL)}

    return app
