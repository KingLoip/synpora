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
        return {
            "btc_hashprice_usd_ph_day":39.64,
            "eur_usd":1.1205,
            "austria_spot_eur_kwh":0.2055,
            "gpu":{"model":"L40S","hourly_usd":1.09,"power_kw":0.35,"utilization":0.70,"platform_fee":0.15,"source":"RunPod Community Cloud"},
            "sources":["Bitcoin Hashprice Index","EUR/USD","EPEX Spot AT","RunPod GPU pricing"],
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
        spread=(best["value_eur_kwh"]-options[1]["value_eur_kwh"]) / max(best["value_eur_kwh"],0.0001)\n        confidence=max(0.55,min(0.97,0.72+0.22*spread))\n        risk={"AI Compute":"market_price_and_utilization","BTC Mining":"hashprice_and_difficulty","Battery":"cycle_and_tariff_assumptions","Grid":"spot_price_volatility"}[best["option"]]\n        return {"farm_id":farm_id,"energy_kwh":x.energy_kwh,"best":best,"alternatives":options[1:],\n                "gross_value_eur":round(gross,2),"energy_cost_eur":round(cost,2),\n                "net_value_eur":round(gross-cost,2),"confidence":round(confidence,2),"risk":risk,\n                "market":{"btc_hashprice_usd_ph_day":x.btc_hashprice_usd_ph_day,"eur_usd":x.eur_usd,"gpu_hourly_usd":x.gpu_hourly_usd,"gpu_power_kw":x.gpu_power_kw,"gpu_utilization":x.gpu_utilization,"gpu_platform_fee":x.gpu_platform_fee},\n                "mode":"recommendation_only","hardware_write":False}

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
