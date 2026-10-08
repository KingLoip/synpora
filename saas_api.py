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
        ai_value_eur_kwh: float=0.135
        btc_value_eur_kwh: float=0.083
        battery_value_eur_kwh: float=0.071
        grid_value_eur_kwh: float=0.055

    @app.post("/api/v1/farms/{farm_id}/optimize")
    def optimize(farm_id:str,x:OptimizeIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        ok=c.execute("SELECT 1 FROM farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone()
        if not ok: raise HTTPException(404,"Farm not found")
        options=[
          {"option":"AI Compute","value_eur_kwh":x.ai_value_eur_kwh},
          {"option":"BTC Mining","value_eur_kwh":x.btc_value_eur_kwh},
          {"option":"Battery","value_eur_kwh":x.battery_value_eur_kwh},
          {"option":"Grid","value_eur_kwh":x.grid_value_eur_kwh}
        ]
        options.sort(key=lambda z:z["value_eur_kwh"],reverse=True)
        best=options[0]
        gross=best["value_eur_kwh"]*x.energy_kwh
        cost=x.energy_cost_eur_kwh*x.energy_kwh
        return {"farm_id":farm_id,"energy_kwh":x.energy_kwh,"best":best,"alternatives":options[1:],"gross_value_eur":round(gross,2),"energy_cost_eur":round(cost,2),"net_value_eur":round(gross-cost,2),"confidence":0.89,"mode":"recommendation_only","hardware_write":False}

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
