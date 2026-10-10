import os, sqlite3, hashlib, hmac, secrets, json, time, threading, contextvars
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

DB_URL = os.getenv("DATABASE_URL","").strip() or os.getenv("POSTGRES_URL","").strip()
JWT_SECRET = os.getenv("SYNPORA_JWT_SECRET","").strip()
DB_PATH = os.getenv("SYNPORA_SQLITE_PATH","/tmp/synpora.db")
_REQUEST_CONNECTIONS = contextvars.ContextVar("synpora_request_db_connections", default=None)

def _track_connection(conn):
    active=_REQUEST_CONNECTIONS.get()
    if active is not None:
        active.append(conn)
    return conn

class _CompatRow(dict):
    """Mapping row with SQLite-compatible integer indexing for legacy query code."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)

def _compat_dict_row(cursor):
    from psycopg.rows import dict_row
    make_dict=dict_row(cursor)
    def make_row(values):
        return _CompatRow(make_dict(values))
    return make_row

class _DBCompat:
    def __init__(self, conn, postgres=False):
        self._conn=conn
        self.is_postgres=postgres
        self._closed=False
    def execute(self, sql, params=None):
        if self.is_postgres and "?" in sql:
            sql=sql.replace("?", "%s")
        return self._conn.execute(sql, params or ())
    def executescript(self, sql):
        if self.is_postgres:
            for stmt in sql.split(";"):
                stmt=stmt.strip()
                if stmt: self._conn.execute(stmt)
        else:
            return self._conn.executescript(sql)
    def commit(self):
        return self._conn.commit()
    @contextmanager
    def transaction(self):
        if self.is_postgres:
            with self._conn.transaction():
                yield
        else:
            self._conn.execute("BEGIN")
            try:
                yield
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
    def close(self):
        if self._closed:
            return None
        self._closed=True
        return self._conn.close()

def _database_required():
    # Railway deployments must fail closed rather than silently using ephemeral SQLite.
    return os.getenv("SYNPORA_REQUIRE_DATABASE","0")=="1" or bool(os.getenv("RAILWAY_ENVIRONMENT","").strip())

def _conn():
    if DB_URL:
        try:
            import psycopg
            # Preserve both named lookups and legacy integer indexing across drivers.
            wrapped=_DBCompat(psycopg.connect(DB_URL, autocommit=True, row_factory=_compat_dict_row), postgres=True)
            return _track_connection(wrapped)
        except Exception as e:
            if _database_required():
                raise RuntimeError("PostgreSQL connection required but unavailable") from e
    elif _database_required():
        raise RuntimeError("DATABASE_URL is required; SQLite fallback is disabled in production")
    conn=sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory=sqlite3.Row
    return _track_connection(_DBCompat(conn, postgres=False))

def init_db():
    c=_conn()
    if c.is_postgres:
        c.execute("""CREATE TABLE IF NOT EXISTS synpora_saas_users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS synpora_saas_farms(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS synpora_saas_assets(id TEXT PRIMARY KEY,farm_id TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,power_kw DOUBLE PRECISION DEFAULT 0,created_at DOUBLE PRECISION NOT NULL)""")
    else:
        c.executescript("""CREATE TABLE IF NOT EXISTS synpora_saas_users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS synpora_saas_farms(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS synpora_saas_assets(id TEXT PRIMARY KEY,farm_id TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,power_kw REAL DEFAULT 0,created_at REAL NOT NULL);""")
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
    if not JWT_SECRET:
        raise RuntimeError("SYNPORA_JWT_SECRET is required")
    import base64
    body=json.dumps({"uid":uid,"exp":int(time.time())+86400},separators=(",",":")).encode()
    b=base64.urlsafe_b64encode(body).decode().rstrip("=")
    sig=hmac.new(JWT_SECRET.encode(),b.encode(),hashlib.sha256).hexdigest()
    return b+"."+sig
def _uid(token):
    if not JWT_SECRET: return None
    import base64
    try:
        b,sig=token.split(".",1)
        if not hmac.compare_digest(sig,hmac.new(JWT_SECRET.encode(),b.encode(),hashlib.sha256).hexdigest()): return None
        raw=base64.urlsafe_b64decode(b+"="*((4-len(b)%4)%4)); d=json.loads(raw)
        if not isinstance(d,dict):
            return None
        uid=d.get("uid")
        exp=d.get("exp")
        # Reject malformed or ambiguous claims instead of accepting any truthy identifier.
        if not isinstance(uid,str) or not uid or len(uid)>128:
            return None
        if isinstance(exp,bool) or not isinstance(exp,(int,float)) or not __import__("math").isfinite(exp):
            return None
        return uid if exp>time.time() else None
    except Exception: return None

def install(app):
    from fastapi import Header, HTTPException
    from pydantic import BaseModel
    routes_before=list(app.router.routes)
    original_route_ids={id(r) for r in routes_before}
    if _database_required() and len(JWT_SECRET)<32:
        raise RuntimeError("SYNPORA_JWT_SECRET must contain at least 32 characters in production")
    startup_connection=init_db()
    startup_connection.close()

    # Close every database connection created while handling an HTTP request.
    # A per-request list is shared with synchronous FastAPI handlers through ContextVar
    # propagation, preventing slow connection leaks under repeated API traffic.
    @app.middleware("http")
    async def close_request_database_connections(request, call_next):
        opened=[]
        token=_REQUEST_CONNECTIONS.set(opened)
        try:
            return await call_next(request)
        finally:
            for connection in reversed(opened):
                try:
                    connection.close()
                except Exception:
                    pass
            _REQUEST_CONNECTIONS.reset(token)

    # Best-effort per-account login throttling. This is process-local and intentionally
    # does not log emails, passwords, tokens, or client-supplied forwarding headers.
    _login_limit_lock=threading.Lock()
    _login_failures={}
    _LOGIN_WINDOW_SECONDS=900
    _LOGIN_MAX_FAILURES=5
    _LOGIN_LOCKOUT_SECONDS=300

    def _login_allowed(key, now=None):
        now=time.time() if now is None else now
        with _login_limit_lock:
            state=_login_failures.get(key,{"attempts":[],"locked_until":0.0})
            state["attempts"]=[t for t in state["attempts"] if now-t < _LOGIN_WINDOW_SECONDS]
            _login_failures[key]=state
            return now >= state["locked_until"]

    def _login_failed(key, now=None):
        now=time.time() if now is None else now
        with _login_limit_lock:
            state=_login_failures.get(key,{"attempts":[],"locked_until":0.0})
            state["attempts"]=[t for t in state["attempts"] if now-t < _LOGIN_WINDOW_SECONDS]
            state["attempts"].append(now)
            if len(state["attempts"]) >= _LOGIN_MAX_FAILURES:
                state["locked_until"]=now+_LOGIN_LOCKOUT_SECONDS
            _login_failures[key]=state
            # Bound memory growth under random-email abuse; expired entries are disposable.
            if len(_login_failures)>10000:
                cutoff=now-_LOGIN_WINDOW_SECONDS
                for old_key,old_state in list(_login_failures.items()):
                    if old_state.get("locked_until",0)<=now and not any(t>=cutoff for t in old_state.get("attempts",[])):
                        _login_failures.pop(old_key,None)

    @app.get("/api/v1/system/status")
    def status():
        configured=bool(DB_URL)
        return {"service":"synpora","version":"1.2.0","database":{"type":"postgresql" if configured else "sqlite_fallback","configured":configured},"security":{"jwt_configured":bool(JWT_SECRET)},"hardware_write":False,"autonomous_control":False,"mode":"recommendation_only"}

    class OptimizeIn(BaseModel):
        energy_kwh: float=10
        energy_cost_eur_kwh: float=0.05
        facility_overhead_kw: float=0.0
        btc_facility_overhead_kw: float=0.0
        gpu_facility_overhead_kw: float=0.0
        battery_round_trip_efficiency: float=0.90
        battery_degradation_eur_kwh: float=0.015
        grid_export_fee_eur_kwh: float=0.0
        ai_value_eur_kwh: float=0.0
        gpu_hourly_usd: float=1.09
        gpu_power_kw: float=0.35
        gpu_utilization: float=0.70
        gpu_platform_fee: float=0.15
        battery_value_eur_kwh: float=0.071
        grid_value_eur_kwh: float=0.055
        samples: int=500
        seed: int=42
        risk_aversion: float=0.75
        btc_hashprice_usd_ph_day: float=39.64
        eur_usd: float=1.1205
        asic_efficiency_j_th: float=20.0
        btc_device_power_kw: float=3.5
        battery_power_kw: float=100.0
        battery_charge_efficiency: float=0.95
        battery_discharge_efficiency: float=0.95
        uptime: float=0.98
        pool_fee: float=0.02

    @app.get("/api/v1/market/live")
    def live_market():
        ext=_external_market()
        now=time.time()
        quality=_market_quality(ext,now)
        fields=quality["fields"]
        return {
            "btc_price_usd":ext.get("btcPrice"),
            "btc_hashprice_usd_ph_day":ext.get("hashpriceUsd") if ext.get("hashpriceUsd") is not None else 38.75,
            "btc_difficulty":ext.get("difficulty"),
            "network_hashrate_eh":ext.get("networkHashrate"),
            "eur_usd":fields["eur_usd"]["value"],
            "austria_spot_eur_kwh":fields["austria_spot_eur_kwh"]["value"],
            "gpu":{"model":"L40S","hourly_usd":fields["gpu_l40s_usd_hour"]["value"],
                   "power_kw":ext.get("gpuPowerKw",0.35),"utilization":ext.get("gpuUtilization",0.70),
                   "platform_fee":ext.get("gpuPlatformFee",0.15),
                   "source":fields["gpu_l40s_usd_hour"].get("provider","pricing reference"),
                   "age_seconds":fields["gpu_l40s_usd_hour"].get("age_seconds")},
            "sources":sorted(set(["Startmining API (best effort)"]+
                [fields[k].get("provider") for k in ("gpu_l40s_usd_hour","eur_usd","austria_spot_eur_kwh")
                 if fields[k]["source"]=="external" and fields[k].get("provider")]+
                (["Configured external market feed"] if ext.get("_configuredFeedAccepted") else []))),
            "data_quality":{"btc":"live_external" if fields["btc_price_usd"]["available"] else "missing",
                "hashprice":"live_external" if fields["btc_hashprice_usd_ph_day"]["source"]=="external" else "fallback",
                "gpu":fields["gpu_l40s_usd_hour"]["source"],"energy":fields["austria_spot_eur_kwh"]["source"],
                "overall":quality["status"],"details":quality},
            "snapshot_freshness":_market_snapshot_freshness(init_db(),now),
            "timestamp":now
        }

    @app.post("/api/v1/market/collect")
    def collect_market(x_market_token:str|None=Header(default=None,alias="X-SYNPORA-MARKET-TOKEN")):
        require_market_admin(x_market_token)
        ext=_external_market()
        now=time.time()
        ext_quality=_market_quality(ext,now)
        fields=ext_quality["fields"]
        all_critical_external=all(fields[k]["source"]=="external" for k in
            ("btc_hashprice_usd_ph_day","gpu_l40s_usd_hour","eur_usd","austria_spot_eur_kwh"))
        snap={"timestamp":now,
              "btc_price_usd":ext.get("btcPrice"),
              "btc_hashprice_usd_ph_day":fields["btc_hashprice_usd_ph_day"]["value"],
              "btc_difficulty":ext.get("difficulty"),
              "network_hashrate_eh":ext.get("networkHashrate"),
              "eur_usd":fields["eur_usd"]["value"],
              "austria_spot_eur_kwh":fields["austria_spot_eur_kwh"]["value"],
              "gpu_l40s_usd_hour":fields["gpu_l40s_usd_hour"]["value"],
              "gpu_l40s_power_kw":ext.get("gpuPowerKw",0.35),
              "gpu_utilization":ext.get("gpuUtilization",0.70),
              "gpu_platform_fee":ext.get("gpuPlatformFee",0.15),
              "source":"multi_provider_market_collection" if all_critical_external else "mixed_provider_market_collection",
              "providers":sorted(set([fields[k].get("provider") for k in
                  ("btc_hashprice_usd_ph_day","gpu_l40s_usd_hour","eur_usd","austria_spot_eur_kwh")
                  if fields[k]["source"]=="external" and fields[k].get("provider")])),
              "data_quality":ext_quality}
        c=init_db()
        _ensure_market_table(c)
        values=(secrets.token_hex(12),snap["timestamp"],json.dumps(snap),
                snap["btc_price_usd"],snap["btc_hashprice_usd_ph_day"],snap["btc_difficulty"],snap["network_hashrate_eh"],
                snap["eur_usd"],snap["gpu_l40s_usd_hour"],snap["gpu_l40s_power_kw"],snap["gpu_utilization"],
                snap["gpu_platform_fee"],snap["austria_spot_eur_kwh"])
        c.execute("""INSERT INTO market_snapshots
            (id,ts,payload,btc_price_usd,btc_hashprice_usd_ph_day,btc_difficulty,network_hashrate_eh,eur_usd,
             gpu_l40s_usd_hour,gpu_l40s_power_kw,gpu_utilization,gpu_platform_fee,austria_spot_eur_kwh)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",values)
        try: c.commit()
        except Exception: pass
        return snap

    @app.post("/api/v1/farms/{farm_id}/optimize")
    def optimize(farm_id:str,x:OptimizeIn,authorization:str|None=Header(default=None)):
        _validate_numeric_inputs(x)
        uid=user(authorization); c=init_db()
        ok=c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone()
        if not ok: raise HTTPException(404,"Farm not found")
        # BTC gross revenue per kWh = hashprice / (J/TH × 24), adjusted for uptime/pool fee.
        energy_cost=max(0.0,x.energy_cost_eur_kwh)
        btc_gross=(x.btc_hashprice_usd_ph_day/max(x.eur_usd,0.01))/(max(x.asic_efficiency_j_th,0.01)*24)
        btc_gross*=max(0.0,min(1.0,x.uptime))*(1-max(0.0,min(0.99,x.pool_fee)))
        btc_overhead=max(0.0,x.btc_facility_overhead_kw)+max(0.0,x.facility_overhead_kw)
        btc_device_power=max(x.btc_device_power_kw,0.01)
        btc_value=btc_gross*(btc_device_power/(btc_device_power+btc_overhead)) if btc_overhead else btc_gross
        gpu_power=max(x.gpu_power_kw,0.01)
        gpu_gross=((max(0.0,x.gpu_hourly_usd)/max(x.eur_usd,0.01))*max(0.0,min(1.0,x.gpu_utilization))*(1-max(0.0,min(0.99,x.gpu_platform_fee))))/gpu_power
        gpu_overhead=max(0.0,x.gpu_facility_overhead_kw)+max(0.0,x.facility_overhead_kw)
        ai_value=gpu_gross*(gpu_power/(gpu_power+gpu_overhead)) if gpu_overhead else gpu_gross
        battery_value=max(0.0,x.battery_value_eur_kwh*max(0.01,min(1.0,x.battery_round_trip_efficiency))-max(0.0,x.battery_degradation_eur_kwh))
        grid_value=max(0.0,x.grid_value_eur_kwh-max(0.0,x.grid_export_fee_eur_kwh))
        options=[
          {"option":"AI Compute","value_eur_kwh":ai_value-energy_cost,"gross_value_eur_kwh":ai_value,"energy_cost_eur_kwh":energy_cost,"source":"gpu_market_reference"},
          {"option":"BTC Mining","value_eur_kwh":btc_value-energy_cost,"gross_value_eur_kwh":btc_value,"energy_cost_eur_kwh":energy_cost,"source":"hashprice_reference"},
          {"option":"Battery","value_eur_kwh":battery_value,"gross_value_eur_kwh":x.battery_value_eur_kwh,"energy_cost_eur_kwh":0.0,"source":"farm_model"},
          {"option":"Grid","value_eur_kwh":grid_value,"gross_value_eur_kwh":x.grid_value_eur_kwh,"energy_cost_eur_kwh":0.0,"source":"energy_model"}
        ]
        options.sort(key=lambda z:z["value_eur_kwh"],reverse=True)
        best=options[0]
        gross=best["gross_value_eur_kwh"]*x.energy_kwh
        cost=best["energy_cost_eur_kwh"]*x.energy_kwh
        spread=(best["value_eur_kwh"]-options[1]["value_eur_kwh"]) / max(best["value_eur_kwh"],0.0001)
        confidence=max(0.55,min(0.97,0.72+0.22*spread))
        learned_confidence=None
        calibrated_confidence=None
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
        risk={"AI Compute":"market_price_and_utilization","BTC Mining":"hashprice_and_difficulty","Battery":"cycle_and_tariff_assumptions","Grid":"spot_price_volatility"}[best["option"]]
        try:
            calibrated_confidence=_calibrated_confidence(c,farm_id,best["option"],confidence)
            confidence=round((confidence+calibrated_confidence)/2,2)
        except Exception:
            pass
        try:
            series_key="gpu" if best["option"]=="AI Compute" else ("btc" if best["option"]=="BTC Mining" else "energy")
            forecast_confidence=_ensemble_confidence(c,series_key)
            confidence=round(min(confidence,forecast_confidence) if forecast_confidence<0.65 else confidence,2)
        except Exception:
            forecast_confidence=0.55
        score_factors={"economics":round(min(1,best["value_eur_kwh"]/max(best["value_eur_kwh"]+x.energy_cost_eur_kwh,0.0001)),3),"margin":round(min(1,max(0,spread*2)),3),"forecast":round(forecast_confidence,3),"learning":round(learned_confidence if learned_confidence is not None else 0.55,3)}
        decision_score=round(100*(0.45*score_factors["economics"]+0.20*score_factors["margin"]+0.20*score_factors["forecast"]+0.15*score_factors["learning"]),1)
        margin=round(best["value_eur_kwh"]-options[1]["value_eur_kwh"],5) if len(options)>1 else 0
        explanation={"primary_reason":"highest modeled net value per kWh","value_margin_eur_kwh":margin,"confidence_driver":"forecast_and_historical_learning","risk_driver":risk,"fallback_option":options[1]["option"] if len(options)>1 else None}
        try:
            c.execute("INSERT INTO decision_ledger (id,farm_id,ts,chosen,predicted_value,confidence,status) VALUES (?,?,?,?,?,?,?)",(str(__import__("uuid").uuid4()),farm_id,time.time(),best["option"],best["value_eur_kwh"],confidence,"open"))
            c.commit()
        except Exception:
            pass
        return {"farm_id":farm_id,"energy_kwh":x.energy_kwh,"best":best,"alternatives":options[1:],"explanation":explanation,"decision_score":decision_score,"score_factors":score_factors,
                "gross_value_eur":round(gross,2),"energy_cost_eur":round(cost,2),
                "net_value_eur":round(gross-cost,2),"confidence":round(confidence,2),"risk":risk,"learned_confidence":learned_confidence,"calibrated_confidence":calibrated_confidence,
                "market":{"btc_hashprice_usd_ph_day":x.btc_hashprice_usd_ph_day,"eur_usd":x.eur_usd,"gpu_hourly_usd":x.gpu_hourly_usd,"gpu_power_kw":x.gpu_power_kw,"gpu_utilization":x.gpu_utilization,"gpu_platform_fee":x.gpu_platform_fee},
                "mode":"recommendation_only","hardware_write":False}

    class ScenarioIn(BaseModel):
        energy_kwh: float=100
        energy_cost_eur_kwh: float=0.05
        facility_overhead_kw: float=0.0
        btc_hashprice_usd_ph_day: float=39.64
        eur_usd: float=1.1205
        gpu_hourly_usd: float=1.09
        gpu_power_kw: float=0.35
        gpu_utilization: float=0.70
        gpu_platform_fee: float=0.15
        asic_efficiency_j_th: float=20.0
        btc_device_power_kw: float=3.5
        btc_facility_overhead_kw: float=0.0
        gpu_facility_overhead_kw: float=0.0
        battery_round_trip_efficiency: float=0.90
        battery_degradation_eur_kwh: float=0.015
        grid_export_fee_eur_kwh: float=0.0
        battery_value_eur_kwh: float=0.071
        grid_value_eur_kwh: float=0.055
        gpu_capex_eur: float=0.0
        gpu_lifetime_years: float=5.0
        gpu_maintenance_eur_year: float=0.0
        gpu_uptime: float=0.98
        btc_capex_eur: float=0.0
        btc_lifetime_years: float=4.0
        btc_maintenance_eur_year: float=0.0
        btc_uptime: float=0.98
        pool_fee: float=0.02
        tax_rate: float=0.0

    def _validate_numeric_inputs(x):
        # Reject NaN/Infinity and impossible physical/financial inputs before they
        # can silently become misleading recommendations or non-JSON responses.
        import math
        values=getattr(x,"__dict__",{})
        for key,value in values.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                number=float(value)
                if not math.isfinite(number):
                    raise HTTPException(422, f"{key} must be a finite number")
                # Finite IEEE-754 values can still overflow downstream multiplications.
                # Keep user-controlled magnitudes within a generous, explicit bound.
                if abs(number)>1e12:
                    raise HTTPException(422, f"{key} exceeds the supported numeric range")
        positive=("energy_kwh","eur_usd","gpu_power_kw","asic_efficiency_j_th",
                  "btc_device_power_kw","interval_hours","gpu_lifetime_years","btc_lifetime_years")
        nonnegative=("btc_hashprice_usd_ph_day","gpu_hourly_usd",
                     "facility_overhead_kw","btc_facility_overhead_kw","gpu_facility_overhead_kw",
                     "battery_degradation_eur_kwh","grid_export_fee_eur_kwh","battery_value_eur_kwh",
                     "grid_value_eur_kwh","pv_kwh","battery_capacity_kwh","battery_power_kw",
                     "ai_value_eur_kwh","gpu_capex_eur","btc_capex_eur",
                     "gpu_maintenance_eur_year","btc_maintenance_eur_year")
        unit_interval=("gpu_utilization","gpu_platform_fee","uptime","gpu_uptime","btc_uptime","pool_fee","tax_rate",
                       "battery_charge_efficiency","battery_discharge_efficiency",
                       "battery_round_trip_efficiency")
        for key in positive:
            if key in values and float(values[key]) <= 0:
                raise HTTPException(422, f"{key} must be greater than zero")
        for key in nonnegative:
            if key in values and float(values[key]) < 0:
                raise HTTPException(422, f"{key} must be zero or greater")
        if "energy_cost_eur_kwh" in values and not -1.0 <= float(values["energy_cost_eur_kwh"]) <= 10.0:
            raise HTTPException(422, "energy_cost_eur_kwh must be between -1 and 10 EUR/kWh")
        for key in unit_interval:
            if key not in values:
                continue
            lower=0.0 if key in ("gpu_utilization","gpu_platform_fee","uptime","gpu_uptime","btc_uptime","pool_fee","tax_rate") else 0.0000001
            if not lower <= float(values[key]) <= 1:
                raise HTTPException(422, f"{key} must be between zero and one")
        for key in ("battery_soc_pct","battery_reserve_pct"):
            if key in values and not 0 <= float(values[key]) <= 100:
                raise HTTPException(422, f"{key} must be between 0 and 100")
        if "shock_pct" in values and not 0 <= float(values["shock_pct"]) <= 0.80:
            raise HTTPException(422, "shock_pct must be between 0 and 0.8")
        if "risk_aversion" in values and not 0 <= float(values["risk_aversion"]) <= 1:
            raise HTTPException(422, "risk_aversion must be between 0 and 1")
        if "horizon_hours" in values and not 1 <= int(values["horizon_hours"]) <= 72:
            raise HTTPException(422, "horizon_hours must be between 1 and 72")

    def _economics(x):
        _validate_numeric_inputs(x)
        eur_usd=max(float(x.eur_usd),0.01)
        energy_cost=float(x.energy_cost_eur_kwh)
        asic_eff=max(float(x.asic_efficiency_j_th),0.01)
        pool_fee=max(0.0,min(1.0,float(getattr(x,"pool_fee",0.02))))
        tax_rate=max(0.0,min(1.0,float(getattr(x,"tax_rate",0.0))))
        btc_gross=(max(0.0,float(x.btc_hashprice_usd_ph_day))/eur_usd)/(asic_eff*24)*(1-pool_fee)
        btc_overhead=max(0.0,float(getattr(x,"btc_facility_overhead_kw",0.0)))+max(0.0,float(getattr(x,"facility_overhead_kw",0.0)))
        gpu_power=max(float(x.gpu_power_kw),0.01)
        gpu_overhead=max(0.0,float(getattr(x,"gpu_facility_overhead_kw",0.0)))+max(0.0,float(getattr(x,"facility_overhead_kw",0.0)))
        gpu_gross=((max(0.0,float(x.gpu_hourly_usd))/eur_usd)*max(0.0,min(1.0,float(x.gpu_utilization)))*(1-max(0.0,min(0.99,float(x.gpu_platform_fee)))))/gpu_power
        btc_device_power=max(float(getattr(x,"btc_device_power_kw",3.5)),0.01)
        btc_active_value=btc_gross*(btc_device_power/(btc_device_power+btc_overhead)) if btc_overhead else btc_gross
        gpu_active_value=gpu_gross*(gpu_power/(gpu_power+gpu_overhead)) if gpu_overhead else gpu_gross
        btc_uptime=max(0.0,min(1.0,float(getattr(x,"btc_uptime",0.98))))
        gpu_uptime=max(0.0,min(1.0,float(getattr(x,"gpu_uptime",0.98))))
        btc_capex=max(0.0,float(getattr(x,"btc_capex_eur",0.0)))
        gpu_capex=max(0.0,float(getattr(x,"gpu_capex_eur",0.0)))
        btc_life=max(0.01,float(getattr(x,"btc_lifetime_years",4.0)))
        gpu_life=max(0.01,float(getattr(x,"gpu_lifetime_years",5.0)))
        btc_maintenance=max(0.0,float(getattr(x,"btc_maintenance_eur_year",0.0)))
        gpu_maintenance=max(0.0,float(getattr(x,"gpu_maintenance_eur_year",0.0)))
        btc_fixed=(btc_capex/btc_life+btc_maintenance)/(btc_device_power*8760)
        gpu_fixed=(gpu_capex/gpu_life+gpu_maintenance)/(gpu_power*8760)
        btc_pre_tax=btc_uptime*(btc_active_value-energy_cost)-btc_fixed
        gpu_pre_tax=gpu_uptime*(gpu_active_value-energy_cost)-gpu_fixed
        btc_value=btc_pre_tax-max(0.0,btc_pre_tax)*tax_rate
        gpu_value=gpu_pre_tax-max(0.0,gpu_pre_tax)*tax_rate
        rte=max(0.01,min(1.0,float(getattr(x,"battery_round_trip_efficiency",0.90))))
        degradation=max(0.0,float(getattr(x,"battery_degradation_eur_kwh",0.015)))
        export_fee=max(0.0,float(getattr(x,"grid_export_fee_eur_kwh",0.0)))
        battery_net=float(x.battery_value_eur_kwh)*rte-degradation
        grid_net=float(x.grid_value_eur_kwh)-export_fee
        # Negative mining/compute margins remain negative; no artificial break-even clamp.
        # Fixed equipment costs are spread over calendar site-energy capacity, while
        # uptime reduces realized revenue and energy consumption. Tax is an operator input.
        battery_net=max(0.0,battery_net)
        grid_net=max(0.0,grid_net)
        return {"AI Compute":gpu_value,"BTC Mining":btc_value,
                "Battery":battery_net-max(0.0,battery_net)*tax_rate,
                "Grid":grid_net-max(0.0,grid_net)*tax_rate}

    def _economic_cost_model(x):
        return {"basis":"EUR per available site kWh; equipment costs amortized over calendar lifetime",
            "gpu":{"capex_eur":float(getattr(x,"gpu_capex_eur",0.0)),
                "lifetime_years":float(getattr(x,"gpu_lifetime_years",5.0)),
                "maintenance_eur_year":float(getattr(x,"gpu_maintenance_eur_year",0.0)),
                "uptime":float(getattr(x,"gpu_uptime",0.98)),
                "depreciation_and_maintenance_eur_kwh":round((float(getattr(x,"gpu_capex_eur",0.0))/float(getattr(x,"gpu_lifetime_years",5.0))+float(getattr(x,"gpu_maintenance_eur_year",0.0)))/(float(getattr(x,"gpu_power_kw",0.35))*8760),6)},
            "btc":{"capex_eur":float(getattr(x,"btc_capex_eur",0.0)),
                "lifetime_years":float(getattr(x,"btc_lifetime_years",4.0)),
                "maintenance_eur_year":float(getattr(x,"btc_maintenance_eur_year",0.0)),
                "uptime":float(getattr(x,"btc_uptime",0.98)),
                "pool_fee":float(getattr(x,"pool_fee",0.02)),
                "depreciation_and_maintenance_eur_kwh":round((float(getattr(x,"btc_capex_eur",0.0))/float(getattr(x,"btc_lifetime_years",4.0))+float(getattr(x,"btc_maintenance_eur_year",0.0)))/(float(getattr(x,"btc_device_power_kw",3.5))*8760),6)},
            "tax_rate":float(getattr(x,"tax_rate",0.0)),
            "warning":"Tax rate and cost assumptions are user supplied; not tax advice. Site/network limits and one-off installation costs are not modeled."}

    def _market_feed_host_allowed(feed_url):
        # Require an exact operator-supplied hostname allowlist; HTTPS alone is not enough.
        try:
            parsed=urlparse(feed_url)
            if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:
                return False
            if parsed.port not in (None,443):
                return False
            host=parsed.hostname.rstrip(".").lower()
            allowed={h.strip().rstrip(".").lower() for h in os.getenv("SYNPORA_MARKET_DATA_ALLOWED_HOSTS","").split(",") if h.strip()}
            if not allowed or host not in allowed:
                return False
            import ipaddress
            try:
                return ipaddress.ip_address(host).is_global
            except ValueError:
                return not (host=="localhost" or host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"))
        except Exception:
            return False

    def _open_market_feed(feed_url, timeout=5, headers=None):
        # Reject private DNS targets and redirects to prevent internal-service SSRF.
        import socket, ipaddress, urllib.request
        host=urlparse(feed_url).hostname
        if not host:
            raise ValueError("Market feed host is missing")
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("Market feed host resolves to a non-public address")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener=urllib.request.build_opener(NoRedirect)
        request=urllib.request.Request(feed_url,headers=headers or {"Accept":"application/json"})
        return opener.open(request,timeout=timeout)

    _provider_json_cache={}
    _provider_json_cache_at={}

    def _provider_json(url, headers=None, ttl_seconds=300):
        import urllib.request
        now=time.time()
        if url in _provider_json_cache and now-_provider_json_cache_at.get(url,0)<ttl_seconds:
            return _provider_json_cache[url]
        req=urllib.request.Request(url,headers=headers or {"User-Agent":"SYNPORA/1.2.0","Accept":"application/json"})
        with urllib.request.urlopen(req,timeout=5) as response:
            raw=json.loads(response.read(262145).decode())
        if len(json.dumps(raw))>262144:
            raise ValueError("Provider response is too large")
        _provider_json_cache[url]=raw
        _provider_json_cache_at[url]=now
        return raw

    def _parse_provider_timestamp(value):
        from datetime import datetime, timezone
        if isinstance(value,(int,float)):
            number=float(value)
            return number/1000.0 if number>1e11 else number
        if isinstance(value,str):
            parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
            if parsed.tzinfo is None:
                parsed=parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        raise ValueError("Unsupported provider timestamp")

    def _fetch_builtin_market_sources(out):
        # Source adapters use public APIs with bounded caching and explicit provenance.
        import math, urllib.parse
        now=time.time()
        meta=out.setdefault("_fieldMeta",{})
        if os.getenv("SYNPORA_DISABLE_BUILTIN_MARKET_SOURCES","").strip().lower() in ("1","true","yes"):
            return out
        # Frankfurter's daily EUR/USD rate; the rate's own date, not fetch time, controls freshness.
        try:
            raw=_provider_json("https://api.frankfurter.dev/v2/providers/ecb/rate/eur/usd",ttl_seconds=3600)
            rate=float(raw.get("rate"))
            rate_ts=_parse_provider_timestamp(raw.get("date"))
            age=now-rate_ts
            if math.isfinite(rate) and rate>0 and -86400<=age<=96*3600:
                out["eurUsd"]=rate
                meta["eurUsd"]={"source":"external","provider":"Frankfurter ECB daily EUR/USD",
                    "source_url":"https://api.frankfurter.dev/v2/providers/ecb/rate/eur/usd",
                    "observed_at":rate_ts,"age_seconds":round(age,1),"valid":True}
        except Exception:
            pass
        # Fraunhofer ISE Energy-Charts: current Austrian day-ahead spot interval.
        try:
            raw=_provider_json("https://api.energy-charts.info/v2/price_current?bzn=AT",ttl_seconds=300)
            series=raw.get("series",[]) if isinstance(raw,dict) else []
            data=raw.get("data",[]) if isinstance(raw,dict) else []
            price_series=next((s for s in series if "price" in str(s.get("id","")).lower() or "price" in str(s.get("name","")).lower()),None)
            price_value=None; interval_ts=None
            for point in data:
                values=point.get("values",{}) if isinstance(point,dict) else {}
                key=price_series.get("id") if price_series else None
                candidate=values.get(key) if key else None
                if candidate is None and len(values)==1:
                    candidate=next(iter(values.values()))
                if candidate is not None:
                    try:
                        number=float(candidate)
                        if math.isfinite(number):
                            price_value=number
                            interval_ts=_parse_provider_timestamp(point.get("timestamp"))
                    except Exception:
                        continue
            attrs=raw.get("attributes",{}) if isinstance(raw,dict) else {}
            valid_until_value=attrs.get("valid_until") or raw.get("valid_until") or raw.get("available_until")
            valid_until=_parse_provider_timestamp(valid_until_value) if valid_until_value else None
            unit=str((price_series or {}).get("unit") or raw.get("unit") or "EUR/MWh").lower()
            if price_value is not None and interval_ts is not None and valid_until is not None and interval_ts<=now+60 and now-interval_ts<=7200 and valid_until>now:
                if "mwh" in unit:
                    price_value/=1000.0
                elif "kwh" not in unit:
                    raise ValueError("Unknown electricity price unit")
                if -1.0<=price_value<=10.0:
                    out["austriaSpotEurKwh"]=price_value
                    meta["austriaSpotEurKwh"]={"source":"external","provider":"Energy-Charts.info (Fraunhofer ISE), Austria day-ahead",
                        "source_url":"https://api.energy-charts.info/v2/price_current?bzn=AT",
                        "license":"CC BY 4.0; attribute Energy-Charts.info",
                        "observed_at":interval_ts,"valid_until":valid_until,"age_seconds":round(now-interval_ts,1),"valid":True}
        except Exception:
            pass
        # Vast.ai offer sampling is optional and requires the operator's own API key.
        vast_key=os.getenv("SYNPORA_VAST_API_KEY","").strip()
        if vast_key:
            try:
                query=urllib.parse.urlencode({"q":json.dumps({"gpu_name":"L40S"})})
                url="https://cloud.vast.ai/api/v0/bundles/?"+query
                raw=_provider_json(url,headers={"User-Agent":"SYNPORA/1.2.0","Accept":"application/json","Authorization":"Bearer "+vast_key},ttl_seconds=300)
                offers=raw if isinstance(raw,list) else raw.get("offers",raw.get("bundles",raw.get("results",[])))
                prices=[]
                for offer in offers if isinstance(offers,list) else []:
                    if not isinstance(offer,dict):
                        continue
                    gpu_name=str(offer.get("gpu_name",offer.get("gpu_name_display",""))).upper()
                    if "L40S" not in gpu_name:
                        continue
                    try:
                        hourly=float(offer.get("dph_total",offer.get("dph")))
                        count=max(1,int(offer.get("num_gpus",offer.get("gpu_count",1)) or 1))
                        if math.isfinite(hourly) and hourly>0:
                            prices.append(hourly/count)
                    except (TypeError,ValueError):
                        continue
                if prices:
                    prices.sort()
                    median=prices[len(prices)//2] if len(prices)%2 else (prices[len(prices)//2-1]+prices[len(prices)//2])/2
                    out["gpuHourlyUsd"]=median
                    meta["gpuHourlyUsd"]={"source":"external","provider":"Vast.ai L40S marketplace median",
                        "source_url":"https://vast.ai/developers/api",
                        "observed_at":now,"age_seconds":0.0,"offer_count":len(prices),"valid":True}
            except Exception:
                pass
        return out

    def _external_market():
        import urllib.request
        out={}
        try:
            raw=_provider_json("https://pro.startmining.io/api/market-summary",headers={"User-Agent":"SYNPORA/1.2.0","Accept":"application/json"},ttl_seconds=60)
            if isinstance(raw,dict): out.update(raw)
        except Exception:
            pass
        out=_fetch_builtin_market_sources(out)
        # Optional operator-configured provider for the four fields needed by learning.
        # It must be HTTPS and include a recent Unix timestamp (seconds or milliseconds).
        feed_url=os.getenv("SYNPORA_MARKET_DATA_URL","").strip()
        if feed_url:
            out["_configuredFeedConfigured"]=True
            parsed=urlparse(feed_url)
            if _market_feed_host_allowed(feed_url):
                try:
                    headers={"User-Agent":"SYNPORA/1.0","Accept":"application/json"}
                    api_key=os.getenv("SYNPORA_MARKET_DATA_API_KEY","").strip()
                    if api_key:
                        headers["Authorization"]="Bearer "+api_key
                    req=urllib.request.Request(feed_url,headers=headers)
                    with _open_market_feed(feed_url,timeout=5,headers=headers) as r:
                        feed=json.loads(r.read(262145).decode())
                    if len(json.dumps(feed))>262144:
                        raise ValueError("Market feed response is too large")
                    if isinstance(feed,dict):
                        observed=feed.get("timestamp",feed.get("observed_at"))
                        observed=float(observed)
                        if observed>1e11: observed/=1000.0
                        age=time.time()-observed
                        if -60<=age<=900:
                            mapping={
                                "btc_hashprice_usd_ph_day":("hashpriceUsd",("btc_hashprice_usd_ph_day","hashprice_usd_ph_day")),
                                "gpu_l40s_usd_hour":("gpuHourlyUsd",("gpu_l40s_usd_hour","gpu_hourly_usd","gpuHourlyUsd")),
                                "eur_usd":("eurUsd",("eur_usd","eurUsd")),
                                "austria_spot_eur_kwh":("austriaSpotEurKwh",("austria_spot_eur_kwh","austriaSpotEurKwh")),
                                "gpu_l40s_power_kw":("gpuPowerKw",("gpu_l40s_power_kw","gpuPowerKw")),
                                "gpu_utilization":("gpuUtilization",("gpu_utilization","gpuUtilization")),
                                "gpu_platform_fee":("gpuPlatformFee",("gpu_platform_fee","gpuPlatformFee")),
                            }
                            accepted=0
                            feed_meta=out.setdefault("_fieldMeta",{})
                            feed_source=str(feed.get("source","configured_external_feed"))[:80]
                            for _field,(target,aliases) in mapping.items():
                                value=next((feed[k] for k in aliases if k in feed),None)
                                try:
                                    number=float(value)
                                    if not __import__("math").isfinite(number): continue
                                    if _field in ("gpu_utilization","gpu_platform_fee"):
                                        if not 0<=number<=1: continue
                                    elif _field=="austria_spot_eur_kwh":
                                        if not -1.0<=number<=10.0: continue
                                    elif number<=0: continue
                                    out[target]=number
                                    feed_meta[target]={"source":"external","provider":feed_source,
                                        "observed_at":observed,"age_seconds":round(age,1),"valid":True}
                                    accepted+=1
                                except (TypeError,ValueError):
                                    continue
                            if accepted:
                                out["_configuredFeedAccepted"]=True
                                out["_configuredFeedObservedAt"]=observed
                                out["_configuredFeedSource"]=feed_source
                except Exception:
                    # Fail closed: unavailable, malformed, oversized, unsafe or stale provider data is ignored.
                    pass
            else:
                out["_configuredFeedRejected"]=True
        return out

    def _market_quality(ext, now=None):
        # A field is external only when a validated provider returned a finite value.
        now=time.time() if now is None else float(now)
        observed={}
        for name,key in (("btc_price_usd","btcPrice"),("btc_hashprice_usd_ph_day","hashpriceUsd"),
                         ("btc_difficulty","difficulty"),("network_hashrate_eh","networkHashrate")):
            value=ext.get(key)
            try: ok=value is not None and __import__("math").isfinite(float(value)) and float(value)>0
            except (TypeError,ValueError): ok=False
            observed[name]={"available":bool(ok),"source":"external" if ok else "missing","value":float(value) if ok else None,
                **({"provider":"Startmining API","source_url":"https://pro.startmining.io/api/market-summary"} if ok else {})}
        configured=bool(ext.get("_configuredFeedConfigured") or ext.get("_configuredFeedAccepted"))
        accepted=bool(ext.get("_configuredFeedAccepted"))
        feed_age=(now-float(ext.get("_configuredFeedObservedAt",0))) if accepted else None
        feed_fresh=accepted and feed_age is not None and -60<=feed_age<=900
        refs={
            "eur_usd":("eurUsd",1.1205,lambda v:float(v)>0),
            "austria_spot_eur_kwh":("austriaSpotEurKwh",0.2055,lambda v:-1.0<=float(v)<=10.0),
            "gpu_l40s_usd_hour":("gpuHourlyUsd",1.09,lambda v:float(v)>0),
        }
        field_meta=ext.get("_fieldMeta",{}) if isinstance(ext.get("_fieldMeta",{}),dict) else {}
        for name,(key,fallback,valid) in refs.items():
            value=ext.get(key)
            meta=field_meta.get(key,{})
            try:
                age=now-float(meta.get("observed_at")) if meta.get("observed_at") is not None else None
                provider=str(meta.get("provider","unknown"))
                max_age=(96*3600 if key=="eurUsd" and provider.startswith("Frankfurter") else
                         7200 if key=="austriaSpotEurKwh" and "Energy-Charts" in provider else 900)
                fresh=(meta.get("source")=="external" and meta.get("valid",False) and age is not None
                       and -60<=age<=max_age and valid(value))
                if key=="austriaSpotEurKwh" and meta.get("valid_until") is not None:
                    fresh=fresh and float(meta["valid_until"])>now
                if ext.get("_configuredFeedAccepted") and provider==str(ext.get("_configuredFeedSource","")):
                    fresh=fresh and feed_fresh
            except (TypeError,ValueError):
                fresh=False
                age=None
                provider="unknown"
            if fresh:
                observed[name]={"available":True,"source":"external","value":float(value),
                    "provider":provider,"age_seconds":round(max(0.0,age),1) if age is not None else None}
                if meta.get("source_url"):
                    observed[name]["source_url"]=meta["source_url"]
                if meta.get("license"):
                    observed[name]["license"]=meta["license"]
                if meta.get("offer_count") is not None:
                    observed[name]["offer_count"]=meta["offer_count"]
                if meta.get("valid_until") is not None:
                    observed[name]["valid_until"]=meta.get("valid_until")
            else:
                observed[name]={"available":True,"source":"reference","value":fallback}
        live=sum(1 for v in observed.values() if v["source"]=="external")
        reference=sum(1 for v in observed.values() if v["source"]=="reference")
        missing=sum(1 for v in observed.values() if v["source"]=="missing")
        critical=("btc_hashprice_usd_ph_day","gpu_l40s_usd_hour","eur_usd","austria_spot_eur_kwh")
        all_critical_external=all(observed[k]["source"]=="external" for k in critical)
        warnings=[]
        if missing: warnings.append("External Bitcoin market feed is incomplete; missing values are not represented as live.")
        if reference: warnings.append("Some EUR/USD, Austrian spot energy or GPU pricing fields still contain reference values, not verified live quotes.")
        if observed["gpu_l40s_usd_hour"]["source"]!="external" and not os.getenv("SYNPORA_VAST_API_KEY","").strip():
            warnings.append("Set SYNPORA_VAST_API_KEY to sample live L40S rental offers from Vast.ai; otherwise GPU pricing remains a reference value unless supplied by the configured feed.")
        if configured and not feed_fresh:
            warnings.append("Configured market feed is stale, invalid, or not HTTPS/allowlisted; its values were not accepted.")
        if ext.get("_configuredFeedRejected"):
            warnings.append("Configured market feed host is not in SYNPORA_MARKET_DATA_ALLOWED_HOSTS or its URL is unsafe.")
        return {"fields":observed,"external_fields":live,"reference_fields":reference,"missing_fields":missing,
                "status":"live" if all_critical_external else ("partial" if live else "fallback"),
                "generated_at":now,"configured_feed_accepted":bool(feed_fresh),
                "configured_feed_source":ext.get("_configuredFeedSource") if feed_fresh else None,
                "configured_feed_age_seconds":round(feed_age,1) if feed_fresh else None,
                "warnings":warnings}

    def _market_snapshot_freshness(c, now=None):
        # Reference snapshots must not be mistaken for live observations.
        now=time.time() if now is None else float(now)
        try:
            row=c.execute("SELECT ts,payload FROM market_snapshots ORDER BY ts DESC LIMIT 1").fetchone()
        except Exception:
            row=None
        if not row:
            return {"available":False,"age_seconds":None,"status":"missing","source":None,
                    "warnings":["No stored market snapshot is available."]}
        try:
            ts=float(row[0])
            payload=json.loads(row[1] or "{}")
            age=max(0.0,now-ts)
        except Exception:
            return {"available":False,"age_seconds":None,"status":"invalid","source":None,
                    "warnings":["Latest market snapshot timestamp or payload is invalid."]}
        source=payload.get("source","unknown") if isinstance(payload,dict) else "unknown"
        quality=payload.get("data_quality",{}) if isinstance(payload,dict) else {}
        overall=quality.get("status") if isinstance(quality,dict) else None
        stale=age>900
        warnings=[]
        if stale:
            warnings.append("Latest stored market snapshot is older than 15 minutes.")
        if source in ("manual_reference_snapshot","startmining_external_plus_reference_prices") or overall in ("reference","fallback","partial"):
            warnings.append("Stored snapshot includes reference values or incomplete external market data.")
        return {"available":True,"timestamp":ts,"age_seconds":round(age,1),
                "status":"stale" if stale else ("reference_or_partial" if warnings else "fresh"),
                "source":source,"source_quality":overall,"warnings":warnings}

    def _forecast_quality(rows, models, confidence, hours):
        values=[float(row[k]) for row in rows for k in ("btc_hashprice_usd_ph_day","gpu_hourly_usd","forecast_energy_cost_eur_kwh")]
        finite=all(__import__("math").isfinite(v) for v in values)
        nonnegative=all(v>=0 for v in values)
        readiness={k:bool(v.get("ready")) for k,v in models.items()}
        ready_count=sum(readiness.values())
        warnings=[]
        if not finite: warnings.append("Forecast contains non-finite values.")
        if not nonnegative: warnings.append("Forecast contains negative market or energy values.")
        if ready_count<3: warnings.append("Insufficient historical samples for fully validated forecasts.")
        if any(float(v)<0.60 for v in confidence.values()): warnings.append("At least one forecast series has low confidence.")
        return {"valid":finite and nonnegative,"historical_models_ready":ready_count,"model_readiness":readiness,
                "confidence_by_series":confidence,"horizon_hours":hours,
                "status":"validated" if finite and nonnegative and ready_count==3 else ("degraded" if finite and nonnegative else "invalid"),
                "warnings":warnings}

    def _ensure_market_table(c):
        if c.is_postgres:
            c.execute("""CREATE TABLE IF NOT EXISTS market_snapshots(
                id TEXT PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, payload TEXT NOT NULL,
                btc_price_usd DOUBLE PRECISION, btc_hashprice_usd_ph_day DOUBLE PRECISION,
                btc_difficulty DOUBLE PRECISION, network_hashrate_eh DOUBLE PRECISION,
                eur_usd DOUBLE PRECISION, gpu_l40s_usd_hour DOUBLE PRECISION,
                gpu_l40s_power_kw DOUBLE PRECISION, gpu_utilization DOUBLE PRECISION,
                gpu_platform_fee DOUBLE PRECISION, austria_spot_eur_kwh DOUBLE PRECISION
            )""")
            # Safe to run on every startup/collection cycle and across concurrent replicas.
            # PostgreSQL handles already-present columns without raising duplicate-column errors.
            cols=["btc_price_usd","btc_hashprice_usd_ph_day","btc_difficulty","network_hashrate_eh","eur_usd","gpu_l40s_usd_hour","gpu_l40s_power_kw","gpu_utilization","gpu_platform_fee","austria_spot_eur_kwh"]
            for col in cols:
                c.execute(f"ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS {col} DOUBLE PRECISION")
        else:
            c.execute("""CREATE TABLE IF NOT EXISTS market_snapshots(
                id TEXT PRIMARY KEY, ts REAL NOT NULL, payload TEXT NOT NULL,
                btc_price_usd REAL, btc_hashprice_usd_ph_day REAL,
                btc_difficulty REAL, network_hashrate_eh REAL,
                eur_usd REAL, gpu_l40s_usd_hour REAL,
                gpu_l40s_power_kw REAL, gpu_utilization REAL,
                gpu_platform_fee REAL, austria_spot_eur_kwh REAL
            )""")
            existing={row[1] for row in c.execute("PRAGMA table_info(market_snapshots)").fetchall()}
            cols=["btc_price_usd","btc_hashprice_usd_ph_day","btc_difficulty","network_hashrate_eh","eur_usd","gpu_l40s_usd_hour","gpu_l40s_power_kw","gpu_utilization","gpu_platform_fee","austria_spot_eur_kwh"]
            for col in cols:
                if col not in existing:
                    try: c.execute(f"ALTER TABLE market_snapshots ADD COLUMN {col} REAL")
                    except Exception: pass
            try: c.commit()
            except Exception: pass

    def require_market_admin(token):
        expected=os.getenv("SYNPORA_MARKET_ADMIN_TOKEN","").strip()
        if not expected or not token or not hmac.compare_digest(token,expected):
            raise HTTPException(403,"Market maintenance endpoint disabled or unauthorized")

    @app.post("/api/v1/market/backfill")
    def backfill_market(limit:int=365,x_market_token:str|None=Header(default=None,alias="X-SYNPORA-MARKET-TOKEN")):
        require_market_admin(x_market_token)
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
        _ensure_market_table(c)
        inserted=0
        for row in rows:
            if isinstance(row,dict):
                ts=row.get("timestamp") or row.get("time") or row.get("ts")
            else:
                ts=row[0] if len(row)>1 else None
            if ts:
                payload=json.dumps(row)
                try:
                    ts_value=float(ts)/1000 if float(ts)>1e11 else float(ts)
                    rowd=row if isinstance(row,dict) else {}
                    vals=(secrets.token_hex(12),ts_value,payload,rowd.get("btcPrice") or rowd.get("btc_price_usd"),
                          rowd.get("hashpriceUsd") or rowd.get("btc_hashprice_usd_ph_day"),
                          rowd.get("difficulty") or rowd.get("btc_difficulty"),
                          rowd.get("networkHashrate") or rowd.get("network_hashrate_eh"),
                          rowd.get("eur_usd") or 1.1205,rowd.get("gpu_l40s_usd_hour") or rowd.get("gpuHourlyUsd") or 1.09,
                          rowd.get("gpu_l40s_power_kw") or 0.35,rowd.get("gpu_utilization") or 0.70,
                          rowd.get("gpu_platform_fee") or 0.15,rowd.get("austria_spot_eur_kwh") or 0.2055)
                    c.execute("""INSERT INTO market_snapshots
                        (id,ts,payload,btc_price_usd,btc_hashprice_usd_ph_day,btc_difficulty,network_hashrate_eh,eur_usd,
                         gpu_l40s_usd_hour,gpu_l40s_power_kw,gpu_utilization,gpu_platform_fee,austria_spot_eur_kwh)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",vals)
                    inserted+=1
                except Exception: pass
        try: c.commit()
        except Exception: pass
        return {"inserted":inserted,"requested":limit,"source":"Startmining price history"}

    def _ensure_learning_tables(c):
        c.execute("CREATE TABLE IF NOT EXISTS decision_ledger(id TEXT PRIMARY KEY,farm_id TEXT NOT NULL,ts REAL NOT NULL,chosen TEXT NOT NULL,predicted_value REAL NOT NULL,confidence REAL NOT NULL,status TEXT NOT NULL,actual_value REAL,settled_at REAL,actual_best_value REAL,regret_eur_kwh REAL)")
        for col in ("actual_best_value","regret_eur_kwh"):
            try:
                c.execute(f"ALTER TABLE decision_ledger ADD COLUMN {col} REAL")
            except Exception:
                pass
        try: c.commit()
        except Exception: pass


    class RiskScenarioIn(BaseModel):
        energy_kwh: float=100
        energy_cost_eur_kwh: float=0.05
        btc_hashprice_usd_ph_day: float=39.64
        eur_usd: float=1.1205
        gpu_hourly_usd: float=1.09
        gpu_power_kw: float=0.35
        gpu_utilization: float=0.70
        gpu_platform_fee: float=0.15
        asic_efficiency_j_th: float=20.0
        btc_device_power_kw: float=3.5
        btc_facility_overhead_kw: float=0.0
        gpu_facility_overhead_kw: float=0.0
        battery_round_trip_efficiency: float=0.90
        battery_degradation_eur_kwh: float=0.015
        grid_export_fee_eur_kwh: float=0.0
        battery_value_eur_kwh: float=0.071
        grid_value_eur_kwh: float=0.055
        facility_overhead_kw: float=0.0
        gpu_capex_eur: float=0.0
        gpu_lifetime_years: float=5.0
        gpu_maintenance_eur_year: float=0.0
        gpu_uptime: float=0.98
        btc_capex_eur: float=0.0
        btc_lifetime_years: float=4.0
        btc_maintenance_eur_year: float=0.0
        btc_uptime: float=0.98
        pool_fee: float=0.02
        tax_rate: float=0.0
        scenarios: int=200
        seed: int=42
        shock_pct: float=0.20
        risk_aversion: float=0.75

    def _risk_metrics(values, base):
        if not values:
            return {"mean":0.0,"p05":0.0,"p10":0.0,"p50":0.0,"p90":0.0,
                    "expected_shortfall_p05":0.0,"downside":0.0,"volatility":0.0,
                    "probability_positive":0.0,"probability_negative":0.0}
        vals=sorted(float(v) for v in values)
        n=len(vals)
        mean=sum(vals)/n
        p=lambda q: vals[min(n-1,max(0,int((n-1)*q)))]
        variance=sum((v-mean)**2 for v in vals)/n
        tail_count=max(1,int(__import__("math").ceil(n*0.05)))
        tail_mean=sum(vals[:tail_count])/tail_count
        return {
            "mean":round(mean,6),"p05":round(p(.05),6),"p10":round(p(.10),6),
            "p50":round(p(.50),6),"p90":round(p(.90),6),
            "expected_shortfall_p05":round(tail_mean,6),
            "downside":round(max(0.0,base-p(.10)),6),
            "volatility":round(variance**0.5,6),
            "probability_positive":round(sum(v>0 for v in vals)/n,3),
            "probability_negative":round(sum(v<0 for v in vals)/n,3)
        }

    @app.post("/api/v1/farms/{farm_id}/risk-analysis")
    def risk_analysis(farm_id:str,x:RiskScenarioIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone():
            raise HTTPException(404,"Farm not found")
        import random
        rng=random.Random(int(x.seed))
        n=max(20,min(2000,int(x.scenarios)))
        shock=max(0.0,min(0.80,float(x.shock_pct)))
        base=_economics(x)
        strategy_values={k:[] for k in base}
        for _ in range(n):
            btc=max(0.01,float(x.btc_hashprice_usd_ph_day)*rng.lognormvariate(0,shock))
            gpu=max(0.01,float(x.gpu_hourly_usd)*rng.lognormvariate(0,shock))
            energy=max(0.001,float(x.energy_cost_eur_kwh)*rng.lognormvariate(0,shock*0.65))
            s=RiskScenarioIn(energy_kwh=x.energy_kwh,energy_cost_eur_kwh=energy,btc_hashprice_usd_ph_day=btc,
                         eur_usd=x.eur_usd,gpu_hourly_usd=gpu,gpu_power_kw=x.gpu_power_kw,
                         gpu_utilization=x.gpu_utilization,gpu_platform_fee=x.gpu_platform_fee,
                         asic_efficiency_j_th=x.asic_efficiency_j_th,btc_device_power_kw=x.btc_device_power_kw,facility_overhead_kw=x.facility_overhead_kw,
                         btc_facility_overhead_kw=x.btc_facility_overhead_kw,
                         gpu_facility_overhead_kw=x.gpu_facility_overhead_kw,battery_round_trip_efficiency=x.battery_round_trip_efficiency,
                         battery_degradation_eur_kwh=x.battery_degradation_eur_kwh,grid_export_fee_eur_kwh=x.grid_export_fee_eur_kwh,
                         battery_value_eur_kwh=x.battery_value_eur_kwh,grid_value_eur_kwh=x.grid_value_eur_kwh)
            econ=_economics(s)
            for k,v in econ.items():
                strategy_values[k].append(float(v)*float(x.energy_kwh))
        base_net={k:float(v)*x.energy_kwh for k,v in base.items()}
        metrics={k:_risk_metrics(v,base_net[k]) for k,v in strategy_values.items()}
        av=max(0.0,min(1.0,float(x.risk_aversion)))
        scores={k:round((1-av)*metrics[k]["mean"]+av*metrics[k]["p05"],4) for k in metrics}
        ranked=sorted(metrics,key=lambda k:(scores[k],metrics[k]["p05"],metrics[k]["mean"]),reverse=True)
        robust=ranked[0] if ranked else None
        best_base=max(base,key=base.get)
        regret={k:round(max(0.0,base_net[best_base]-base_net[k]),4) for k in base}
        stress_cases=[]
        stress_updates=(
            ("btc_hashprice_down_35pct",{"btc_hashprice_usd_ph_day":x.btc_hashprice_usd_ph_day*0.65}),
            ("gpu_rate_down_30pct",{"gpu_hourly_usd":x.gpu_hourly_usd*0.70}),
            ("energy_price_up_50pct",{"energy_cost_eur_kwh":x.energy_cost_eur_kwh*1.50}),
            ("combined_downside",{"btc_hashprice_usd_ph_day":x.btc_hashprice_usd_ph_day*0.65,
                "gpu_hourly_usd":x.gpu_hourly_usd*0.70,"energy_cost_eur_kwh":x.energy_cost_eur_kwh*1.50,
                "eur_usd":x.eur_usd*1.10,"gpu_utilization":x.gpu_utilization*0.85})
        )
        for label,updates in stress_updates:
            stressed=x.model_copy(update=updates)
            values=_economics(stressed)
            winner=max(values,key=values.get)
            stress_cases.append({"scenario":label,"best_strategy":winner,
                "net_value_eur_kwh":{k:round(float(v),6) for k,v in values.items()},
                "best_value_eur_kwh":round(float(values[winner]),6)})
        risk_gate="loss_exposure" if robust and metrics[robust]["p05"]<0 else ("high_uncertainty" if robust and metrics[robust]["probability_positive"]<0.80 else "within_simulated_limits")
        return {
            "farm_id":farm_id,"samples":n,"seed":x.seed,"shock_pct":shock,
            "base_case":{"best":best_base,"net_eur":round(base_net[best_base],4),"values_eur_kwh":{k:round(v,6) for k,v in base.items()}},
            "strategies":metrics,"robust_strategy":robust,"base_case_strategy":best_base,
            "regret_vs_base_best_eur":regret,"risk_aversion":round(av,3),
            "risk_adjusted_score":scores,"stress_scenarios":stress_cases,
            "risk_gate":{"status":risk_gate,"basis":"simulated_p05_and_probability_positive",
                "note":"A scenario gate is a warning, not a guarantee; input assumptions and external data quality still matter."},
            "method":"deterministic_seeded_monte_carlo_lognormal_shocks_with_stress_tests",
            "cost_model":_economic_cost_model(x),
            "recommendation_only":True,"hardware_write":False
        }

    @app.post("/api/v1/farms/{farm_id}/decision-engine")
    def decision_engine(farm_id:str,x:RiskScenarioIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone():
            raise HTTPException(404,"Farm not found")
        # Unified decision layer: net economics + Monte-Carlo risk and regret.
        base=_economics(x)
        base_net={k:float(v)*x.energy_kwh for k,v in base.items()}
        risk_seed=int(x.seed); n=max(100,min(5000,int(x.scenarios))); rng=__import__("random").Random(risk_seed)
        samples=[]
        for _ in range(n):
            s=x.model_copy(update={
                "btc_hashprice_usd_ph_day":x.btc_hashprice_usd_ph_day*rng.lognormvariate(0,float(x.shock_pct)),
                "gpu_hourly_usd":x.gpu_hourly_usd*rng.lognormvariate(0,float(x.shock_pct)),
                "energy_cost_eur_kwh":x.energy_cost_eur_kwh*rng.lognormvariate(0,float(x.shock_pct)*0.65)})
            samples.append(_economics(s))
        av=max(0.0,min(1.0,float(x.risk_aversion)))
        ranked=[]
        for k in base:
            vals=sorted(float(v[k]) for v in samples); mean=sum(vals)/n; p05=vals[max(0,int(.05*n)-1)]
            wins=sum(1 for row in samples if max(row,key=row.get)==k)/n
            regret=sum(max(row.values())-row[k] for row in samples)/n
            score=(1-av)*mean+av*p05
            ranked.append({"strategy":k,"value_eur_kwh":round(base[k],6),"expected_eur_kwh":round(mean,6),"p05_eur_kwh":round(p05,6),"win_probability":round(wins,4),"expected_regret_eur_kwh":round(regret,6),"risk_score":round(score,6)})
        ranked.sort(key=lambda z:z["risk_score"],reverse=True)
        best=ranked[0]
        # Confidence is deliberately conservative until empirical decision history exists.
        empirical=0.55
        try:
            q=c.execute("SELECT confidence FROM decision_ledger WHERE farm_id=? AND status='settled' ORDER BY ts DESC LIMIT 24",(farm_id,)).fetchall()
            if q: empirical=max(0.50,min(0.95,sum(float(r[0]) for r in q)/len(q)))
        except Exception: pass
        confidence=max(0.50,min(0.95,0.55*best["win_probability"]+0.25*empirical+0.20*max(0.0,min(1.0,1/(1+best["expected_regret_eur_kwh"]*20)))))
        return {"farm_id":farm_id,"recommended":best["strategy"],"confidence":round(confidence,3),
                "decision":{"expected_value_eur_kwh":best["expected_eur_kwh"],"p05_eur_kwh":best["p05_eur_kwh"],"win_probability":best["win_probability"],"expected_regret_eur_kwh":best["expected_regret_eur_kwh"],"risk_score":best["risk_score"]},
                "ranking":ranked,"risk_aversion":round(av,3),"samples":n,"seed":risk_seed,
                "fallback":ranked[1]["strategy"] if len(ranked)>1 else None,
                "explanation":"Risk-adjusted choice from current economics plus seeded Monte-Carlo market shocks; no hardware action is executed.",
                "method":"unified_forecast_risk_regret_v1","cost_model":_economic_cost_model(x),
                "recommendation_only":True,"hardware_write":False}

    @app.post("/api/v1/farms/{farm_id}/decision")
    def record_decision(farm_id:str,x:ScenarioIn,authorization:str|None=Header(default=None)):
        _validate_numeric_inputs(x)
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        vals=_economics(x); chosen=max(vals,key=vals.get); did=secrets.token_hex(12)
        c.execute("INSERT INTO decision_ledger (id,farm_id,ts,chosen,predicted_value,confidence,status,actual_value,settled_at) VALUES(?,?,?,?,?,?,?,?,?)",(did,farm_id,time.time(),chosen,vals[chosen],0.80,"open",None,None))
        try: c.commit()
        except Exception: pass
        return {"decision_id":did,"chosen":chosen,"predicted_value_eur_kwh":round(vals[chosen],6),"confidence":0.80,"status":"open"}

    @app.post("/api/v1/market/snapshot")
    def market_snapshot(x_market_token:str|None=Header(default=None,alias="X-SYNPORA-MARKET-TOKEN")):
        require_market_admin(x_market_token)
        c=init_db()
        snap={"timestamp":time.time(),"btc_hashprice_usd_ph_day":39.6395,"eur_usd":1.1205,
              "gpu_l40s_usd_hour":1.09,"gpu_l40s_power_kw":0.35,
              "source":"manual_reference_snapshot",
              "data_quality":{"overall":"reference","settlement_basis":"reference_inputs",
                              "fields":{"btc_hashprice_usd_ph_day":"reference","eur_usd":"reference",
                                        "gpu_l40s_usd_hour":"reference","gpu_l40s_power_kw":"reference"}}}
        _ensure_market_table(c)
        vals=(secrets.token_hex(12),snap["timestamp"],json.dumps(snap),None,snap["btc_hashprice_usd_ph_day"],None,None,
              snap["eur_usd"],snap["gpu_l40s_usd_hour"],snap["gpu_l40s_power_kw"],0.70,0.15,None)
        c.execute("""INSERT INTO market_snapshots
            (id,ts,payload,btc_price_usd,btc_hashprice_usd_ph_day,btc_difficulty,network_hashrate_eh,eur_usd,
             gpu_l40s_usd_hour,gpu_l40s_power_kw,gpu_utilization,gpu_platform_fee,austria_spot_eur_kwh)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",vals)
        try: c.commit()
        except Exception: pass
        return snap

    @app.get("/api/v1/farms/{farm_id}/benchmark")
    def benchmark(farm_id:str,limit:int=500,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone():
            raise HTTPException(404,"Farm not found")
        _ensure_market_table(c)
        try:
            rows=c.execute("SELECT ts,payload FROM market_snapshots ORDER BY ts ASC LIMIT ?",(max(1,min(limit,1000)),)).fetchall()
        except Exception:
            rows=[]
        totals={k:0.0 for k in ("AI Compute","BTC Mining","Battery","Grid")}
        points=[]; excluded=0
        for row in rows:
            try:
                payload=json.loads(row[1] or "{}")
                if not isinstance(payload,dict) or not _snapshot_is_learning_eligible(payload):
                    excluded+=1
                    continue
                values={}
                valid=True
                for key in ("btc_hashprice_usd_ph_day","gpu_l40s_usd_hour","eur_usd","austria_spot_eur_kwh"):
                    try:
                        value=float(payload[key])
                        if not __import__("math").isfinite(value) or (value<=0 and key!="austria_spot_eur_kwh") or (key=="austria_spot_eur_kwh" and not -1.0<=value<=10.0):
                            valid=False; break
                        values[key]=value
                    except (KeyError,TypeError,ValueError):
                        valid=False; break
                if not valid:
                    excluded+=1
                    continue
                scenario=ScenarioIn(energy_kwh=100,
                    energy_cost_eur_kwh=values["austria_spot_eur_kwh"],
                    btc_hashprice_usd_ph_day=values["btc_hashprice_usd_ph_day"],
                    gpu_hourly_usd=values["gpu_l40s_usd_hour"],eur_usd=values["eur_usd"],
                    gpu_power_kw=float(payload.get("gpu_l40s_power_kw") or 0.35),
                    gpu_utilization=max(0.0,min(1.0,float(payload.get("gpu_utilization",0.70)))),
                    gpu_platform_fee=max(0.0,min(1.0,float(payload.get("gpu_platform_fee",0.15))))
                )
                economics=_economics(scenario)
                for name,value in economics.items():
                    totals[name]+=float(value)*scenario.energy_kwh
                winner=max(economics,key=economics.get)
                points.append({"timestamp":float(row[0]),"net_eur_per_100_kwh":{k:round(float(v)*100,2) for k,v in economics.items()},
                    "winner":winner,"source":payload.get("source","external_observation")})
            except (TypeError,ValueError,json.JSONDecodeError):
                excluded+=1
        count=len(points)
        return {"farm_id":farm_id,"points":points,"samples":count,"excluded_snapshots":excluded,
            "mean_net_eur_per_100_kwh":{k:round(v/count,2) if count else None for k,v in totals.items()},
            "ai_total_net_eur":round(totals["AI Compute"],2),"btc_total_net_eur":round(totals["BTC Mining"],2),
            "ai_delta_vs_btc_eur":round(totals["AI Compute"]-totals["BTC Mining"],2),
            "winner_share":round(sum(p["winner"]=="AI Compute" for p in points)/count,3) if count else None,
            "method":"provenance_gated_snapshot_benchmark",
            "warnings":["Only fully external snapshots are included; reference and mixed snapshots are excluded.",
                "This compares modeled economics at stored observations and is not a forward-looking backtest."],
            "recommendation_only":True,"hardware_write":False}

    @app.post("/api/v1/farms/{farm_id}/learning/settle")
    def settle_learning(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        settlement=_online_learning_update(c,farm_id)
        stats=_forecast_learning(c,farm_id)
        return {"farm_id":farm_id,**settlement,"learning":stats,"recommendation_only":True,"hardware_write":False}

    @app.get("/api/v1/farms/{farm_id}/learning/calibration")
    def learning_calibration(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        rows=c.execute("SELECT chosen,predicted_value,actual_value,confidence FROM decision_ledger WHERE farm_id=? AND status='settled' AND actual_value IS NOT NULL ORDER BY ts DESC LIMIT 500",(farm_id,)).fetchall()
        by={}
        for r in rows:
            name=str(r[0]); by.setdefault(name,[]).append(r)
        result={}
        for name,items in by.items():
            errors=[abs(float(r[2])-float(r[1])) for r in items]
            hits=[1 if abs(float(r[2])-float(r[1]))<=max(0.01,abs(float(r[1]))*0.10) else 0 for r in items]
            mae=sum(errors)/len(errors)
            hit=sum(hits)/len(hits)
            # Confidence combines forecast hit-rate and normalized error.
            calibrated=max(0.50,min(0.98,0.45+0.40*hit+0.15*(1/(1+mae*10))))
            result[name]={"samples":len(items),"hit_rate":round(hit,3),"mae_eur_kwh":round(mae,6),"calibrated_confidence":round(calibrated,3)}
        overall=max(result.values(),key=lambda x:x["calibrated_confidence"])["calibrated_confidence"] if result else 0.70
        return {"farm_id":farm_id,"strategies":result,"overall_confidence":overall,"calibration_ready":len(rows)>=5}

    @app.get("/api/v1/farms/{farm_id}/decision-quality")
    def decision_quality(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone():
            raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        rows=c.execute("SELECT chosen,predicted_value,actual_value,actual_best_value,regret_eur_kwh,confidence FROM decision_ledger WHERE farm_id=? AND status='settled' AND actual_value IS NOT NULL ORDER BY ts",(farm_id,)).fetchall()
        if not rows:
            return {"farm_id":farm_id,"samples":0,"winner_accuracy":None,"avg_regret_eur_kwh":None,"p90_regret_eur_kwh":None,"confidence_calibration_gap":None,"status":"cold_start"}
        winner=sum(1 for r in rows if r[3] is not None and abs(float(r[2])-float(r[3]))<=1e-9)/len(rows)
        regrets=[max(0.0,float(r[4])) for r in rows if r[4] is not None]
        if not regrets:
            regrets=[max(0.0,float(r[3])-float(r[2])) for r in rows if r[3] is not None]
        regrets.sort()
        p90=regrets[min(len(regrets)-1,int((len(regrets)-1)*.90))] if regrets else None
        confidence_gap=None
        pairs=[(float(r[5]),1.0 if (r[3] is not None and abs(float(r[2])-float(r[3]))<=1e-9) else 0.0) for r in rows if r[5] is not None and r[3] is not None]
        if pairs: confidence_gap=sum(a-b for a,b in pairs)/len(pairs)
        return {"farm_id":farm_id,"samples":len(rows),"winner_accuracy":round(winner,3),
                "avg_regret_eur_kwh":round(sum(regrets)/len(regrets),6) if regrets else 0.0,
                "p90_regret_eur_kwh":round(p90,6) if p90 is not None else 0.0,
                "confidence_calibration_gap":round(confidence_gap,3) if confidence_gap is not None else None,
                "status":"validated" if len(rows)>=24 else "learning",
                "recommendation_only":True}
    
    @app.get("/api/v1/farms/{farm_id}/model-score")
    def model_score(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        rows=c.execute("SELECT chosen,predicted_value,actual_value FROM decision_ledger WHERE farm_id=? AND status='settled' AND actual_value IS NOT NULL",(farm_id,)).fetchall()
        if not rows: return {"samples":0,"score":0.0,"status":"cold_start"}
        mae=sum(abs(float(r[2])-float(r[1])) for r in rows)/len(rows)
        directional=sum(1 for r in rows if abs(float(r[2])-float(r[1]))<=max(0.01,abs(float(r[1]))*0.10))/len(rows)
        score=max(0,min(100,50*directional+50*(1/(1+mae*10))))
        return {"samples":len(rows),"mae_eur_kwh":round(mae,6),"forecast_hit_rate":round(directional,3),"score":round(score,1),"status":"learning"}

    @app.get("/api/v1/farms/{farm_id}/learning")
    def learning_status(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        rows=c.execute("SELECT status,predicted_value,actual_value,confidence FROM decision_ledger WHERE farm_id=? ORDER BY ts DESC LIMIT 500",(farm_id,)).fetchall()
        settled=[r for r in rows if r[0]=="settled" and r[2] is not None]
        mae=sum(abs(float(r[2])-float(r[1])) for r in settled)/len(settled) if settled else None
        return {"farm_id":farm_id,"samples":len(rows),"settled":len(settled),"open":sum(1 for r in rows if r[0]=="open"),"mae_eur_kwh":round(mae,6) if mae is not None else None,"learning_ready":len(settled)>=5,"recommendation_only":True}

    @app.get("/api/v1/market/history")
    def market_history(limit:int=100):
        # Bound query cost and avoid a malformed historical payload taking down the
        # entire diagnostics endpoint. Negative SQLite LIMIT values mean "no limit".
        bounded_limit=max(1,min(int(limit),500))
        c=init_db()
        try:
            rows=c.execute("SELECT ts,payload FROM market_snapshots ORDER BY ts DESC LIMIT ?",(bounded_limit,)).fetchall()
        except Exception:
            rows=[]
        result=[]
        for row in rows:
            try:
                payload=json.loads(row[1]) if row[1] else None
            except (TypeError,ValueError,json.JSONDecodeError):
                payload=None
            result.append({"timestamp":row[0],"data":payload,"payload_valid":isinstance(payload,dict)})
        return result

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
        btc_device_power_kw: float=3.5

    @app.post("/api/v1/farms/{farm_id}/portfolio-optimize")
    def portfolio_optimize(farm_id:str,x:PortfolioIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        assets=[dict(r) for r in c.execute("SELECT id,name,kind,power_kw FROM synpora_saas_assets WHERE farm_id=? ORDER BY created_at",(farm_id,)).fetchall()]
        candidates=[]
        for a in assets:
            kind=a["kind"].upper(); power=max(float(a["power_kw"] or 0),0.01)
            if kind=="GPU":
                value=(x.gpu_hourly_usd/x.eur_usd)*x.gpu_utilization*(1-x.gpu_platform_fee)/power
                candidates.append({"asset_id":a["id"],"asset":a["name"],"kind":"GPU","value_eur_kwh":value,"capacity_kwh":power*x.horizon_hours})
            elif kind=="BTC":
                value=(x.btc_hashprice_usd_ph_day/x.eur_usd)/(x.asic_efficiency_j_th*24)*0.98*0.98
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
        battery_power_kw: float=100.0
        battery_charge_efficiency: float=0.95
        battery_discharge_efficiency: float=0.95
        battery_value_eur_kwh: float=0.071
        battery_degradation_eur_kwh: float=0.015
        battery_round_trip_efficiency: float=0.90
        grid_value_eur_kwh: float=0.055
        grid_export_fee_eur_kwh: float=0.0
        btc_hashprice_usd_ph_day: float=38.75
        eur_usd: float=1.1205
        gpu_hourly_usd: float=1.09
        gpu_utilization: float=0.70
        gpu_platform_fee: float=0.15
        asic_efficiency_j_th: float=20.0
        btc_device_power_kw: float=3.5
        btc_facility_overhead_kw: float=0.0
        gpu_facility_overhead_kw: float=0.0
        facility_overhead_kw: float=0.0

    def _eligible_market_rows(c, limit=168, include_ts=False):
        # Forecast fitting and weights must use the same provenance gate as learning labels.
        raw=c.execute("SELECT ts,payload,btc_hashprice_usd_ph_day,gpu_l40s_usd_hour,austria_spot_eur_kwh FROM market_snapshots ORDER BY ts DESC LIMIT ?",(int(limit),)).fetchall()
        rows=[]
        for row in raw:
            try:
                payload=json.loads(row[1])
            except Exception:
                continue
            if not _snapshot_is_learning_eligible(payload):
                continue
            values=(row[0],row[2],row[3],row[4])
            rows.append(values if include_ts else values[1:])
        return rows

    def _adaptive_model_weights(c, series_key):
        rows=_eligible_market_rows(c,168,include_ts=True)
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
        rows=_eligible_market_rows(c,48)
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
        rows=_eligible_market_rows(c,168,include_ts=True)
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
        rows=_eligible_market_rows(c,48)
        idx={"btc":0,"gpu":1,"energy":2}[series_key]
        y=[float(r[idx]) for r in rows if r[idx] is not None]
        if len(y)<8:
            return 0.55
        mean=sum(y)/len(y)
        volatility=(sum(abs(v-mean) for v in y)/len(y))/(abs(mean)+1e-9)
        stability=max(0.0,min(1.0,1.0-volatility))
        return round(max(.55,min(.95,.55+.25*concentration+.20*stability)),3)

    def _settled_learning_stats(c, farm_id, strategy):
        rows=c.execute("SELECT predicted_value,actual_value,actual_best_value,regret_eur_kwh FROM decision_ledger WHERE farm_id=? AND chosen=? AND status='settled' AND predicted_value IS NOT NULL AND actual_value IS NOT NULL ORDER BY ts DESC LIMIT 100",(farm_id,strategy)).fetchall()
        if not rows: return {"samples":0,"mae":None,"forecast_hit_rate":None,"winner_accuracy":None,"avg_regret_eur_kwh":None}
        pairs=[(float(r[0]),float(r[1])) for r in rows]
        mae=sum(abs(a-b) for a,b in pairs)/len(pairs)
        hits=sum(1 for a,b in pairs if abs(a-b)<=max(0.01,abs(a)*0.10))/len(pairs)
        winner=sum(1 for r in rows if r[2] is not None and abs(float(r[1])-float(r[2]))<1e-9)/len(rows)
        regrets=[float(r[3]) for r in rows if r[3] is not None]
        return {"samples":len(pairs),"mae":round(mae,8),"forecast_hit_rate":round(hits,3),
                "winner_accuracy":round(winner,3) if rows else None,
                "avg_regret_eur_kwh":round(sum(regrets)/len(regrets),8) if regrets else None}

    def _snapshot_is_learning_eligible(payload):
        # Learning targets must be based on independently sourced values, not defaults.
        if not isinstance(payload,dict):
            return False
        quality=payload.get("data_quality")
        fields=quality.get("fields") if isinstance(quality,dict) else None
        if not isinstance(fields,dict):
            return False
        required=("btc_hashprice_usd_ph_day","gpu_l40s_usd_hour","eur_usd","austria_spot_eur_kwh")
        for key in required:
            field=fields.get(key)
            if not isinstance(field,dict) or field.get("source")!="external" or not field.get("available",True):
                return False
        return True

    def _online_learning_update(c, farm_id):
        from datetime import datetime
        import math
        _ensure_market_table(c)
        # Settle only on a later snapshot whose hashprice, GPU price, FX and energy price
        # are all independently external. Reference/mixed/unknown snapshots are never training labels.
        snaps=c.execute("SELECT ts,payload FROM market_snapshots ORDER BY ts ASC").fetchall()
        decisions=c.execute("SELECT id,chosen,predicted_value,ts FROM decision_ledger WHERE farm_id=? AND status='open' ORDER BY ts ASC LIMIT 100",(farm_id,)).fetchall()
        settled=0
        skipped_invalid=0
        skipped_ineligible=0
        sources_used=set()
        for d in decisions:
            try:
                decision_ts=float(d[3])
                if not math.isfinite(decision_ts): continue
            except (TypeError,ValueError):
                try: decision_ts=datetime.fromisoformat(str(d[3]).replace("Z","+00:00")).timestamp()
                except Exception: continue
            for snap in snaps:
                try: snap_ts=float(snap[0])
                except (TypeError,ValueError): continue
                if not math.isfinite(snap_ts) or snap_ts<=decision_ts: continue
                try: payload=json.loads(snap[1])
                except Exception:
                    skipped_invalid+=1
                    continue
                if not _snapshot_is_learning_eligible(payload):
                    skipped_ineligible+=1
                    continue
                # Require compute-market inputs after provenance has been validated.
                required=("btc_hashprice_usd_ph_day","gpu_l40s_usd_hour")
                parsed={}
                valid=True
                for key in required:
                    try:
                        value=float(payload[key])
                        if not math.isfinite(value) or value<0: valid=False; break
                        parsed[key]=value
                    except (KeyError,TypeError,ValueError):
                        valid=False; break
                if not valid:
                    skipped_invalid+=1
                    continue
                def finite_or(key, default, minimum=0.000001):
                    try:
                        value=float(payload.get(key,default))
                        return value if math.isfinite(value) and value>=minimum else default
                    except (TypeError,ValueError):
                        return default
                scenario=ScenarioIn(
                    btc_hashprice_usd_ph_day=parsed["btc_hashprice_usd_ph_day"],
                    gpu_hourly_usd=parsed["gpu_l40s_usd_hour"],
                    eur_usd=finite_or("eur_usd",1.1205),
                    energy_cost_eur_kwh=finite_or("austria_spot_eur_kwh",0.2055,0.0),
                    gpu_power_kw=finite_or("gpu_l40s_power_kw",0.35),
                    gpu_utilization=max(0.0,min(1.0,finite_or("gpu_utilization",0.70,0.0))),
                    gpu_platform_fee=max(0.0,min(1.0,finite_or("gpu_platform_fee",0.15,0.0)))
                )
                economics=_economics(scenario)
                actual=economics.get(d[1])
                if actual is None or not math.isfinite(float(actual)): 
                    skipped_invalid+=1
                    continue
                actual_best=max(economics.values())
                regret=max(0.0,actual_best-float(actual))
                sources_used.add(str(payload.get("source") or payload.get("data_quality",{}).get("overall") or "unspecified"))
                c.execute("UPDATE decision_ledger SET actual_value=?,actual_best_value=?,regret_eur_kwh=?,status='settled',settled_at=? WHERE id=? AND status='open'",
                          (float(actual),float(actual_best),float(regret),time.time(),d[0]))
                settled+=1
                break
        try: c.commit()
        except Exception: pass
        return {"settled_now":settled,"open_remaining":max(0,len(decisions)-settled),
                "skipped_invalid_snapshots":skipped_invalid,"skipped_ineligible_snapshots":skipped_ineligible,
                "snapshot_sources_used":sorted(sources_used),
                "method":"first_later_fully_external_market_snapshot"}

    def _forecast_learning(c, farm_id):
        _ensure_learning_tables(c)
        rows=c.execute("SELECT status,predicted_value,actual_value,confidence FROM decision_ledger WHERE farm_id=? ORDER BY ts DESC LIMIT 500",(farm_id,)).fetchall()
        settled=[r for r in rows if r[0]=="settled" and r[2] is not None]
        mae=sum(abs(float(r[2])-float(r[1])) for r in settled)/len(settled) if settled else None
        open_count=sum(1 for r in rows if r[0]=="open")
        return {"samples":len(rows),"settled":len(settled),"open":open_count,
                "mae_eur_kwh":round(mae,6) if mae is not None else None,
                "learning_ready":len(settled)>=5,
                "status":"calibrating" if len(settled)<5 else "learning",
                "recommendation_only":True}

    @app.get("/api/v1/market/data-health")
    def market_data_health():
        c=init_db(); _ensure_market_table(c)
        rows=c.execute("SELECT ts,payload FROM market_snapshots ORDER BY ts DESC LIMIT ?",(500,)).fetchall()
        now=time.time(); eligible=[]; excluded=0; latest_quality=None; latest_source=None
        latest_snapshot_ts=float(rows[0][0]) if rows else None
        latest_snapshot_age=max(0.0,now-latest_snapshot_ts) if latest_snapshot_ts is not None else None
        latest_snapshot_payload={}
        if rows:
            try:
                parsed_latest=json.loads(rows[0][1] or "{}")
                if isinstance(parsed_latest,dict):
                    latest_snapshot_payload=parsed_latest
            except (TypeError,ValueError,json.JSONDecodeError):
                latest_snapshot_payload={}
        for row in rows:
            try:
                payload=json.loads(row[1] or "{}")
                if not isinstance(payload,dict):
                    excluded+=1; continue
                if latest_quality is None:
                    latest_quality=payload.get("data_quality"); latest_source=payload.get("source","unknown")
                if _snapshot_is_learning_eligible(payload):
                    eligible.append({"ts":float(row[0]),"payload":payload})
                else:
                    excluded+=1
            except (TypeError,ValueError,json.JSONDecodeError):
                excluded+=1
        newest=eligible[0] if eligible else None
        age=max(0.0,now-newest["ts"]) if newest else None
        status="missing" if not rows else ("no_eligible_data" if not newest else ("stale" if age>900 else "eligible_data_available"))
        warnings=[]
        if not rows: warnings.append("No market snapshots have been stored.")
        if not newest: warnings.append("No snapshot has verified external provenance for every learning-critical field.")
        elif age>900: warnings.append("The latest eligible external snapshot is older than 15 minutes.")
        if excluded: warnings.append("Reference, mixed, incomplete, and unknown-provenance snapshots are excluded from learning and backtesting.")
        collection_status=("never_collected" if latest_snapshot_ts is None else
            "recent_snapshot" if latest_snapshot_age is not None and latest_snapshot_age<=1200 else "stale_snapshot")
        latest_quality_fields=(latest_snapshot_payload.get("data_quality",{}).get("fields",{})
            if isinstance(latest_snapshot_payload.get("data_quality",{}),dict) else {})
        return {"status":status,"snapshots_scanned":len(rows),"eligible_snapshots":len(eligible),
            "excluded_or_invalid_snapshots":excluded,"latest_snapshot_source":latest_source,
            "latest_snapshot_quality":latest_quality,"latest_snapshot_timestamp":latest_snapshot_ts,
            "latest_snapshot_age_seconds":round(latest_snapshot_age,1) if latest_snapshot_age is not None else None,
            "collection_status":collection_status,
            "latest_snapshot_providers":latest_snapshot_payload.get("providers",[]),
            "latest_snapshot_fields":latest_quality_fields,
            "latest_eligible_timestamp":newest["ts"] if newest else None,
            "latest_eligible_age_seconds":round(age,1) if age is not None else None,
            "learning_fields_required":["btc_hashprice_usd_ph_day","gpu_l40s_usd_hour","eur_usd","austria_spot_eur_kwh"],
            "warnings":warnings,"recommendation_only":True,"hardware_write":False}

    @app.get("/api/v1/farms/{farm_id}/paper-trading")
    def paper_trading_status(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone():
            raise HTTPException(404,"Farm not found")
        _ensure_learning_tables(c)
        rows=c.execute("SELECT chosen,predicted_value,confidence,status,actual_value,actual_best_value,regret_eur_kwh,ts,settled_at FROM decision_ledger WHERE farm_id=? ORDER BY ts DESC LIMIT 500",(farm_id,)).fetchall()
        settled=[r for r in rows if r[3]=="settled" and r[4] is not None]
        open_rows=[r for r in rows if r[3]=="open"]
        now=time.time(); by_strategy={}
        for strategy in ("AI Compute","BTC Mining","Battery","Grid"):
            group=[r for r in settled if r[0]==strategy]
            by_strategy[strategy]={"settled":len(group),
                "mean_prediction_error_eur_kwh":round(sum(abs(float(r[4])-float(r[1])) for r in group)/len(group),6) if group else None,
                "mean_regret_eur_kwh":round(sum(float(r[6] or 0.0) for r in group)/len(group),6) if group else None}
        errors=[abs(float(r[4])-float(r[1])) for r in settled]
        regrets=[float(r[6] or 0.0) for r in settled]
        return {"farm_id":farm_id,"mode":"paper_trading_only",
            "status":"collecting" if len(settled)<24 else "evaluation_available",
            "decisions_tracked":len(rows),"settled_decisions":len(settled),"open_decisions":len(open_rows),
            "oldest_open_age_seconds":round(max(0.0,now-min(float(r[7]) for r in open_rows)),1) if open_rows else None,
            "mean_prediction_error_eur_kwh":round(sum(errors)/len(errors),6) if errors else None,
            "mean_regret_eur_kwh":round(sum(regrets)/len(regrets),6) if regrets else None,
            "strategy_metrics":by_strategy,
            "recent_decisions":[{"strategy":r[0],"predicted_value_eur_kwh":float(r[1]),"confidence":float(r[2]),
                "status":r[3],"actual_value_eur_kwh":float(r[4]) if r[4] is not None else None,
                "actual_best_value_eur_kwh":float(r[5]) if r[5] is not None else None,
                "regret_eur_kwh":float(r[6]) if r[6] is not None else None,"timestamp":float(r[7]),
                "settled_at":float(r[8]) if r[8] is not None else None} for r in rows[:50]],
            "recommendation_only":True,"hardware_write":False,
            "warnings":["This endpoint reports recommendations only; it does not execute trades or control hardware.",
                *(["Fewer than 24 settled decisions are available; performance conclusions are premature."] if len(settled)<24 else [])]}

    @app.get("/api/v1/system/release")
    def release_status():
        return {"product":"SYNPORA","release":"1.2.0","ai_core":"risk_aware_walk_forward_v3_economic_costs",
                "mode":"recommendation_only","hardware_write":False,"autonomous_control":False,
                "features":["market_intelligence","adaptive_forecast","model_selection","decision_ledger","self_learning","calibrated_confidence","provenance_gated_walk_forward_backtest","paper_trading_observability","tail_risk_metrics","deterministic_stress_tests","secure_allowlisted_market_feed","operations_dashboard","equipment_depreciation_uptime_maintenance_tax"],
                "status":"production_candidate"}

    @app.get("/api/v1/system/production-readiness")
    def production_readiness():
        db_configured=bool(os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL"))
        jwt_configured=bool(os.getenv("SYNPORA_JWT_SECRET","").strip())
        jwt_secret_strong=len(os.getenv("SYNPORA_JWT_SECRET","").strip())>=32
        market_token_configured=bool(os.getenv("SYNPORA_MARKET_ADMIN_TOKEN","").strip())
        market_token_strong=len(os.getenv("SYNPORA_MARKET_ADMIN_TOKEN","").strip())>=32
        db_reachable=False
        db_schema_ready=False
        db_backend="unavailable" if _database_required() else "sqlite_fallback"
        db_error=None
        c=None
        try:
            c=init_db()
            c.execute("SELECT 1").fetchone()
            db_reachable=bool(DB_URL and c.is_postgres)
            db_backend="postgresql" if db_reachable else "sqlite_fallback"
            c.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()
            db_schema_ready=True
        except Exception as e:
            db_error=type(e).__name__
            if _database_required() and not db_reachable:
                db_backend="unavailable"
        finally:
            if c is not None:
                try: c.close()
                except Exception: pass
        ready=bool(db_configured and db_reachable and db_schema_ready and jwt_configured and jwt_secret_strong and market_token_configured and market_token_strong)
        return {"database_configured":db_configured,"database_reachable":db_reachable,
                "database_schema_ready":db_schema_ready,"database_backend":db_backend,"database_error":db_error,
                "jwt_secret_configured":jwt_configured,"jwt_secret_strong":jwt_secret_strong,
                "market_admin_token_configured":market_token_configured,"market_admin_token_strong":market_token_strong,
                "market_data_feed_configured":bool(os.getenv("SYNPORA_MARKET_DATA_URL","").strip()),
                "market_data_feed_https":bool(urlparse(os.getenv("SYNPORA_MARKET_DATA_URL","").strip()).scheme=="https" and urlparse(os.getenv("SYNPORA_MARKET_DATA_URL","").strip()).hostname) if os.getenv("SYNPORA_MARKET_DATA_URL","").strip() else False,
                "market_data_feed_host_allowlisted":_market_feed_host_allowed(os.getenv("SYNPORA_MARKET_DATA_URL","").strip()) if os.getenv("SYNPORA_MARKET_DATA_URL","").strip() else False,
                "market_data_feed_api_key_configured":bool(os.getenv("SYNPORA_MARKET_DATA_API_KEY","").strip()),
                "market_data_allowed_hosts_configured":bool(os.getenv("SYNPORA_MARKET_DATA_ALLOWED_HOSTS","").strip()),
                "vast_api_key_configured":bool(os.getenv("SYNPORA_VAST_API_KEY","").strip()),
                "builtin_market_sources_enabled":os.getenv("SYNPORA_DISABLE_BUILTIN_MARKET_SOURCES","").strip().lower() not in ("1","true","yes"),
                "market_data_mode":"configured_feed_plus_builtin_sources" if os.getenv("SYNPORA_MARKET_DATA_URL","").strip() else "builtin_sources_with_reference_fallback",
                "hardware_write_enabled":False,"autonomous_control_enabled":False,
                "recommendation_only":True,"external_market_layer":True,
                "status":"ready" if ready else "configuration_required"}


    @app.get("/api/v1/farms/{farm_id}/ai-status")
    def ai_status(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        stats={s:_settled_learning_stats(c,farm_id,s) for s in ("AI Compute","BTC Mining","Battery","Grid")}
        total=sum(v["samples"] for v in stats.values())
        readiness="cold_start" if total<8 else ("learning" if total<24 else "calibrated")
        return {"farm_id":farm_id,"status":readiness,"total_settled_samples":total,"strategies":stats,
                "self_learning":True,"recommendation_only":True,"hardware_write":False}

    @app.get("/api/v1/farms/{farm_id}/forecast-health")
    def forecast_health(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        return {"farm_id":farm_id,"btc":_settled_learning_stats(c,farm_id,"BTC Mining"),"gpu":_settled_learning_stats(c,farm_id,"AI Compute"),"mode":"self_calibrating","recommendation_only":True}
    
    @app.post("/api/v1/farms/{farm_id}/learning/update")
    def learning_update(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        settlement=_online_learning_update(c,farm_id)
        stats={}
        for strategy in ("AI Compute","BTC Mining","Battery","Grid"):
            stats[strategy]=_settled_learning_stats(c,farm_id,strategy)
        return {"farm_id":farm_id,**settlement,"calibration":stats,"mode":"self_learning","recommendation_only":True,"hardware_write":False}

    @app.post("/api/v1/farms/{farm_id}/forecast-plan")
    def forecast_plan(farm_id:str,x:DispatchIn,authorization:str|None=Header(default=None)):
        _validate_numeric_inputs(x)
        uid=user(authorization); c=init_db()
        _ensure_market_table(c)
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        learn=_forecast_learning(c,farm_id)
        models=_model_select(c)
        conf={"btc":_ensemble_confidence(c,"btc"),"gpu":_ensemble_confidence(c,"gpu"),"energy":_ensemble_confidence(c,"energy")}
        hours=max(1,min(72,x.horizon_hours))
        btc_fc,btc_w=_ensemble_forecast(c,"btc",hours,x.btc_hashprice_usd_ph_day)
        gpu_fc,gpu_w=_ensemble_forecast(c,"gpu",hours,x.gpu_hourly_usd)
        energy_fc,energy_w=_ensemble_forecast(c,"energy",hours,x.energy_cost_eur_kwh)
        import math
        rows=[]
        for h in range(hours):
            # learned baseline: recent observed level + transparent cyclical prior
            recent=c.execute("SELECT btc_hashprice_usd_ph_day,gpu_l40s_usd_hour,austria_spot_eur_kwh FROM market_snapshots ORDER BY ts DESC LIMIT 24").fetchall()
            def avg(idx,default):
                a=[float(r[idx]) for r in recent if r[idx] is not None]
                return sum(a)/len(a) if a else default
            btc0=avg(0,x.btc_hashprice_usd_ph_day); gpu0=avg(1,x.gpu_hourly_usd)
            pv_shape=max(0.0,math.sin((h+1)/hours*math.pi))
            btc=btc_fc[h]; gpu=gpu_fc[h]; energy=energy_fc[h]
            pv=x.pv_kwh/hours*(0.35+1.3*pv_shape)
            gpu_v=(gpu/x.eur_usd)*x.gpu_utilization*(1-x.gpu_platform_fee)/0.35
            btc_v=(btc/x.eur_usd)/(x.asic_efficiency_j_th*24)*0.98*0.98
            rows.append({"hour":h,"pv_kwh":round(pv,3),"gpu_hourly_usd":round(gpu,4),
                         "btc_hashprice_usd_ph_day":round(btc,4),"gpu_value_eur_kwh":round(gpu_v,5),"forecast_energy_cost_eur_kwh":round(energy,5),
                         "btc_value_eur_kwh":round(btc_v,5),"best_option":"AI Compute" if gpu_v>=btc_v else "BTC Mining"})
        forecast_quality=_forecast_quality(rows,models,conf,hours)
        snapshot_freshness=_market_snapshot_freshness(c)
        forecast_quality["market_snapshot_freshness"]=snapshot_freshness
        forecast_quality["warnings"] += snapshot_freshness["warnings"]
        if snapshot_freshness["status"] in ("missing","invalid","stale","reference_or_partial"):
            forecast_quality["status"]="degraded" if forecast_quality["valid"] else "invalid"
        return {"farm_id":farm_id,"horizon_hours":hours,"forecast":rows,
                "method":"historical_adaptive_model_selection","learning":learn,"models":models,"ensemble_weights":{"btc":btc_w,"gpu":gpu_w,"energy":energy_w},"ensemble_confidence":conf,
                "forecast_quality":forecast_quality,
                "recommendation_only":True,"hardware_write":False}

    @app.post("/api/v1/farms/{farm_id}/dispatch-plan")
    def dispatch_plan(farm_id:str,x:DispatchIn,authorization:str|None=Header(default=None)):
        _validate_numeric_inputs(x)
        uid=user(authorization); c=init_db()
        _ensure_market_table(c)
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        assets=[dict(r) for r in c.execute("SELECT id,name,kind,power_kw FROM synpora_saas_assets WHERE farm_id=? ORDER BY created_at",(farm_id,)).fetchall()]
        hours=max(1,min(72,x.horizon_hours)); dt=max(0.25,x.interval_hours)
        try:
            btc_fc,_=_ensemble_forecast(c,"btc",hours,x.btc_hashprice_usd_ph_day); gpu_fc,_=_ensemble_forecast(c,"gpu",hours,x.gpu_hourly_usd); energy_fc,_=_ensemble_forecast(c,"energy",hours,x.energy_cost_eur_kwh)
            forecast_source="adaptive_ensemble"
        except Exception:
            btc_fc=[x.btc_hashprice_usd_ph_day]*hours; gpu_fc=[x.gpu_hourly_usd]*hours; energy_fc=[x.energy_cost_eur_kwh]*hours; forecast_source="input_fallback"
        reserve=max(0,min(100,x.battery_reserve_pct)); cap=max(0,x.battery_capacity_kwh); soc=max(cap*reserve/100,min(cap,cap*x.battery_soc_pct/100))
        power=max(0,x.battery_power_kw); ce=max(.01,min(1,x.battery_charge_efficiency)); de=max(.01,min(1,x.battery_discharge_efficiency)); soc_min=cap*reserve/100; initial_soc=soc
        import math
        shape=[max(.05,math.sin(math.pi*(i+.5)/hours)) for i in range(hours)]; pv_total=max(0,x.pv_kwh); scale=pv_total/sum(shape) if sum(shape) else 0; pv=[v*scale for v in shape]
        candidates=[{"name":a["name"],"kind":"AI Compute" if str(a["kind"]).upper()=="GPU" else "BTC Mining","power":float(a["power_kw"] or 0)} for a in assets if str(a["kind"]).upper() in ("GPU","BTC") and float(a["power_kw"] or 0)>0]
        plan=[]; trace=[]; summary={"btc_kwh":0.0,"gpu_kwh":0.0,"pv_generation_kwh":0.0,"pv_to_compute_kwh":0.0,"grid_export_kwh":0.0,"battery_charge_kwh":0.0,"battery_charge_stored_kwh":0.0,"battery_discharge_kwh":0.0,"battery_losses_kwh":0.0,"total_value_eur":0.0,"energy_cost_eur":0.0}
        for h in range(hours):
            eur_usd=max(0.01,float(x.eur_usd)); asic_eff=max(0.01,float(x.asic_efficiency_j_th)); gpu_util=max(0.0,min(1.0,float(x.gpu_utilization))); gpu_fee=max(0.0,min(0.99,float(x.gpu_platform_fee)))
            btc_gross=(float(btc_fc[h])/eur_usd)/(asic_eff*24)*.98*.98
            btc_over=max(0.0,x.btc_facility_overhead_kw)+max(0.0,x.facility_overhead_kw)
            btc_device_power=max(0.01,float(x.btc_device_power_kw))
            btc=(btc_gross*(btc_device_power/(btc_device_power+btc_over)) if btc_over else btc_gross)-float(energy_fc[h])
            gpu_gross=(float(gpu_fc[h])/eur_usd)*gpu_util*(1-gpu_fee)/(.35+max(0.0,x.gpu_facility_overhead_kw)+max(0.0,x.facility_overhead_kw))
            gpu=gpu_gross-float(energy_fc[h]); grid=max(0.0,x.grid_value_eur_kwh-max(0.0,x.grid_export_fee_eur_kwh)); price=max(0.0,float(energy_fc[h])); before=soc
            btc=max(0.0,btc); gpu=max(0.0,gpu)
            future=max([btc,gpu,grid]+[max((float(btc_fc[j])/x.eur_usd)/(x.asic_efficiency_j_th*24)*.98*.98,(float(gpu_fc[j])/x.eur_usd)*x.gpu_utilization*(1-x.gpu_platform_fee)/.35,grid) for j in range(h+1,hours)] or [max(btc,gpu,grid)])
            summary["pv_generation_kwh"]+=pv[h]
            charge=min(pv[h],power*dt,(cap-soc)/ce) if future>max(btc,gpu,grid)+.005 and cap>soc else 0
            charge=max(0.0,charge); soc+=charge*ce; rem=pv[h]-charge
            summary["battery_charge_kwh"]+=charge
            summary["battery_charge_stored_kwh"]+=charge*ce
            summary["battery_losses_kwh"]+=charge*(1-ce)
            ranked=sorted(candidates,key=lambda a:gpu if a["kind"]=="AI Compute" else btc,reverse=True)
            for a in ranked:
                value=gpu if a["kind"]=="AI Compute" else btc; take=min(rem,a["power"]*dt)
                if take<=0: continue
                rem-=take; summary["pv_to_compute_kwh"]+=take; summary["total_value_eur"]+=value*take; summary["energy_cost_eur"]+=price*take; summary["gpu_kwh" if a["kind"]=="AI Compute" else "btc_kwh"]+=take
                plan.append({"hour":h,"asset":a["name"],"action":a["kind"],"pv_kwh":round(take,3),"battery_discharge_kwh":0,"battery_charge_kwh":round(charge,3),"grid_export_kwh":0,"value_eur_kwh":round(value,5),"soc_before_pct":round(100*before/cap,2) if cap else 0,"soc_after_pct":round(100*soc/cap,2) if cap else 0,"reason":"forecast-ranked opportunity"})
            if rem>0:
                summary["total_value_eur"]+=grid*rem; summary["grid_export_kwh"]+=rem
                plan.append({"hour":h,"asset":"Grid","action":"Grid","pv_kwh":round(rem,3),"battery_discharge_kwh":0,"battery_charge_kwh":round(charge,3),"grid_export_kwh":round(rem,3),"value_eur_kwh":round(grid,5),"soc_before_pct":round(100*before/cap,2) if cap else 0,"soc_after_pct":round(100*soc/cap,2) if cap else 0,"reason":"PV surplus"})
            if charge<=1e-9 and future>max(btc,gpu,grid)+max(.005,x.battery_value_eur_kwh*max(0.01,min(1.0,x.battery_round_trip_efficiency))) and soc>soc_min:
                kind="AI Compute" if gpu>=btc else "BTC Mining"; target=next((a for a in candidates if a["kind"]==kind),None); value=max(gpu,btc)
                if target:
                    take=min(target["power"]*dt,(soc-soc_min)*de,power*dt)
                    if take>0:
                        soc-=take/de; summary["battery_discharge_kwh"]+=take; summary["battery_losses_kwh"]+=take*(1/de-1); summary["total_value_eur"]+=value*take; summary["energy_cost_eur"]+=price*take; summary["gpu_kwh" if kind=="AI Compute" else "btc_kwh"]+=take
                        plan.append({"hour":h,"asset":target["name"],"action":kind,"pv_kwh":0,"battery_discharge_kwh":round(take,3),"battery_charge_kwh":0,"grid_export_kwh":0,"value_eur_kwh":round(value,5),"soc_before_pct":round(100*before/cap,2) if cap else 0,"soc_after_pct":round(100*soc/cap,2) if cap else 0,"reason":"future opportunity value"})
            trace.append({"hour":h,"soc_kwh":round(soc,3),"soc_pct":round(100*soc/cap,2) if cap else 0,"btc_value_eur_kwh":round(btc,5),"gpu_value_eur_kwh":round(gpu,5),"energy_cost_eur_kwh":round(price,5)})
        summary["pv_balance_error_kwh"]=round(summary["pv_generation_kwh"]-summary["pv_to_compute_kwh"]-summary["battery_charge_kwh"]-summary["grid_export_kwh"],6)
        summary["battery_soc_initial_kwh"]=round(initial_soc,3)
        summary["battery_soc_final_kwh"]=round(soc,3)
        summary["battery_soc_reserve_kwh"]=round(soc_min,3)
        summary["battery_soc_balance_error_kwh"]=round(soc-(initial_soc+summary["battery_charge_stored_kwh"]-summary["battery_discharge_kwh"]/de),6)
        summary["net_value_eur"]=round(summary["total_value_eur"],2)
        summary["battery_degradation_cost_eur"]=round(summary["battery_discharge_kwh"]*max(0.0,x.battery_degradation_eur_kwh),3)
        summary["net_value_eur"]=round(summary["net_value_eur"]-summary["battery_degradation_cost_eur"],2)
        return {"farm_id":farm_id,"horizon_hours":hours,"interval_hours":dt,"plan":plan,"soc_trace":trace,"forecasts":{"btc_hashprice_usd_ph_day":btc_fc,"gpu_hourly_usd":gpu_fc,"energy_cost_eur_kwh":energy_fc},"summary":{k:round(v,3) if isinstance(v,float) else v for k,v in summary.items()},"constraints":{"battery_reserve_pct":reserve,"battery_capacity_kwh":cap,"battery_power_kw":power,"charge_efficiency":ce,"discharge_efficiency":de},"forecast_source":forecast_source,"optimizer":"forecast_aware_horizon_v1","objective":"maximize_expected_value_with_battery_opportunity_cost","recommendation_only":True,"hardware_write":False}

    @app.post("/api/v1/farms/{farm_id}/scenario")
    def scenario(farm_id:str,x:ScenarioIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        values=_economics(x)
        assets=[dict(r) for r in c.execute("SELECT id,name,kind,power_kw FROM synpora_saas_assets WHERE farm_id=? ORDER BY created_at",(farm_id,)).fetchall()]
        asset_cost_models=[]
        for a in assets:
            kind=a["kind"].upper()
            if kind=="GPU" and a["power_kw"]>0:
                asset_x=x.model_copy(update={"gpu_power_kw":float(a["power_kw"])})
                asset_values=_economics(asset_x)
                values["AI Compute"]=asset_values["AI Compute"]
                asset_cost_models.append({"asset_id":a["id"],"name":a["name"],"kind":kind,
                    "power_kw":float(a["power_kw"]),"cost_model":_economic_cost_model(asset_x)})
            if kind=="BTC" and a["power_kw"]>0:
                asset_x=x.model_copy(update={"btc_device_power_kw":float(a["power_kw"])})
                asset_values=_economics(asset_x)
                values["BTC Mining"]=asset_values["BTC Mining"]
                asset_cost_models.append({"asset_id":a["id"],"name":a["name"],"kind":kind,
                    "power_kw":float(a["power_kw"]),"cost_model":_economic_cost_model(asset_x)})
        rows=[{"option":k,"value_eur_kwh":round(v,5),"net_eur":round(v*x.energy_kwh,2)} for k,v in values.items()]
        rows.sort(key=lambda r:r["value_eur_kwh"],reverse=True)
        best=rows[0]
        return {"farm_id":farm_id,"inputs":x.model_dump(),"ranking":rows,"best":best,
                "spread_eur_kwh":round(best["value_eur_kwh"]-rows[1]["value_eur_kwh"],5),
                "cost_model":_economic_cost_model(x),"asset_cost_models":asset_cost_models,
                "warnings":["Hardware capex, maintenance and tax default to zero unless supplied; review inputs before relying on rankings.",
                    *([ "Asset-specific power values were used for registered hardware." ] if asset_cost_models else [])],
                "recommendation_only":True,"hardware_write":False}

    @app.post("/api/v1/farms/{farm_id}/regret-analysis")
    def regret_analysis(farm_id:str,x:ScenarioIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone(): raise HTTPException(404,"Farm not found")
        import random
        n=max(100,min(5000,int(x.samples))); rng=random.Random(int(x.seed)); strategies=list(_economics(x).keys()); regret={k:[] for k in strategies}; winners={k:0 for k in strategies}
        for _ in range(n):
            s=x.model_copy(update={"btc_hashprice_usd_ph_day":x.btc_hashprice_usd_ph_day*rng.lognormvariate(0,.18),"gpu_hourly_usd":x.gpu_hourly_usd*rng.lognormvariate(0,.15),"energy_cost_eur_kwh":x.energy_cost_eur_kwh*rng.lognormvariate(0,.20)})
            vals=_economics(s); optimum=max(vals.values())
            for k in strategies: regret[k].append(max(0,optimum-vals[k]))
            winners[max(vals,key=vals.get)]+=1
        out={}
        for k in strategies:
            rs=sorted(regret[k]); mean=sum(rs)/n; p95=rs[min(n-1,int(.95*n))]; out[k]={"mean_regret_eur_kwh":round(mean,5),"p95_regret_eur_kwh":round(p95,5),"max_regret_eur_kwh":round(rs[-1],5),"winner_probability":round(winners[k]/n,4)}
        safest=min(out,key=lambda k:out[k]["p95_regret_eur_kwh"]); return {"farm_id":farm_id,"samples":n,"seed":int(x.seed),"strategies":out,"lowest_tail_regret":safest,"method":"stochastic_regret_analysis","recommendation_only":True}

    class BacktestIn(BaseModel):
        min_samples: int=24
        max_snapshots: int=1000

    @app.post("/api/v1/farms/{farm_id}/backtest")
    def backtest(farm_id:str,x:BacktestIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        if not c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone():
            raise HTTPException(404,"Farm not found")
        _ensure_market_table(c)
        limit=max(2,min(5000,int(x.max_snapshots)))
        min_samples=max(1,min(500,int(x.min_samples)))
        raw=c.execute("SELECT ts,payload FROM market_snapshots ORDER BY ts DESC LIMIT ?",(limit,)).fetchall()
        observations=[]; excluded=0
        for row in reversed(raw):
            try:
                payload=json.loads(row[1] or "{}")
                if not isinstance(payload,dict) or not _snapshot_is_learning_eligible(payload):
                    excluded+=1
                    continue
                values={}
                valid=True
                for key in ("btc_hashprice_usd_ph_day","gpu_l40s_usd_hour","eur_usd","austria_spot_eur_kwh"):
                    try:
                        value=float(payload[key])
                        if not __import__("math").isfinite(value) or value<=0:
                            valid=False; break
                        values[key]=value
                    except (KeyError,TypeError,ValueError):
                        valid=False; break
                if not valid:
                    excluded+=1
                    continue
                def optional_number(key,default,minimum=0.000001,maximum=None):
                    try:
                        value=float(payload.get(key,default))
                        if not __import__("math").isfinite(value) or value<minimum or (maximum is not None and value>maximum):
                            return default
                        return value
                    except (TypeError,ValueError):
                        return default
                observations.append({"ts":float(row[0]),"values":values,
                    "gpu_power":optional_number("gpu_l40s_power_kw",0.35),
                    "gpu_utilization":optional_number("gpu_utilization",0.70,0.0,1.0),
                    "gpu_fee":optional_number("gpu_platform_fee",0.15,0.0,1.0)})
            except (TypeError,ValueError,json.JSONDecodeError):
                excluded+=1
        pairs=[]
        for i in range(len(observations)-1):
            current,nxt=observations[i],observations[i+1]
            if nxt["ts"]<=current["ts"]:
                continue
            def to_scenario(obs):
                v=obs["values"]
                return RiskScenarioIn(energy_cost_eur_kwh=v["austria_spot_eur_kwh"],
                    btc_hashprice_usd_ph_day=v["btc_hashprice_usd_ph_day"],
                    gpu_hourly_usd=v["gpu_l40s_usd_hour"],eur_usd=v["eur_usd"],
                    gpu_power_kw=obs["gpu_power"],gpu_utilization=obs["gpu_utilization"],
                    gpu_platform_fee=obs["gpu_fee"])
            predicted=_economics(to_scenario(current))
            actual=_economics(to_scenario(nxt))
            chosen=max(predicted,key=predicted.get)
            pairs.append({"chosen":chosen,"predicted":float(predicted[chosen]),"actual":float(actual[chosen]),
                "actual_best":float(max(actual.values())),"regret":float(max(0.0,max(actual.values())-actual[chosen])),
                "actual_best_strategy":max(actual,key=actual.get),"actual_values":actual,
                "from_ts":current["ts"],"to_ts":nxt["ts"]})
        n=len(pairs); strategies=("AI Compute","BTC Mining","Battery","Grid")
        benchmarks={}
        for name in strategies:
            vals=[p["actual_values"][name] for p in pairs]
            benchmarks[name]={"mean_next_period_value_eur_kwh":round(sum(vals)/len(vals),6) if vals else None,
                              "observations":len(vals)}
        mae=sum(abs(p["actual"]-p["predicted"]) for p in pairs)/n if n else None
        regret=sum(p["regret"] for p in pairs)/n if n else None
        accuracy=sum(1 for p in pairs if p["chosen"]==p["actual_best_strategy"])/n if n else None
        warnings=[]
        if n<min_samples:
            warnings.append(f"Need at least {min_samples} eligible walk-forward pairs; only {n} are available.")
        warnings.append("Strict walk-forward: strategy choice uses snapshot t; scoring uses the next eligible snapshot.")
        warnings.append("Reference, mixed, incomplete and unknown-provenance snapshots are excluded. Historical results do not guarantee future returns.")
        return {"farm_id":farm_id,"status":"insufficient_data" if n<min_samples else "evaluated",
            "method":"strict_walk_forward_next_snapshot_v1","eligible_snapshots":len(observations),
            "excluded_snapshots":excluded,"evaluated_pairs":n,"minimum_pairs_required":min_samples,
            "metrics":{"prediction_mae_eur_kwh":round(mae,6) if mae is not None else None,
                "mean_regret_eur_kwh":round(regret,6) if regret is not None else None,
                "strategy_selection_accuracy":round(accuracy,4) if accuracy is not None else None},
            "benchmark_by_strategy":benchmarks,
            "recent_evaluations":[{"from_timestamp":p["from_ts"],"to_timestamp":p["to_ts"],
                "chosen_strategy":p["chosen"],"predicted_value_eur_kwh":round(p["predicted"],6),
                "actual_next_value_eur_kwh":round(p["actual"],6),"actual_best_strategy":p["actual_best_strategy"],
                "regret_eur_kwh":round(p["regret"],6)} for p in pairs[-20:]],
            "warnings":warnings,"recommendation_only":True,"hardware_write":False}

    class AuthIn(BaseModel):
        email: str
        password: str

    # Perform a real PBKDF2 verification for unknown accounts as well, reducing
    # timing differences that could reveal whether an email is registered.
    _DUMMY_PASSWORD_HASH=_hash("synpora-invalid-login-dummy-password")
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
        email=x.email.strip().lower()
        if len(email)<3 or "@" not in email or len(email)>254 or email.startswith("@") or email.endswith("@") or email.count("@")!=1:
            raise HTTPException(400,"Valid email required")
        if len(x.password)<8: raise HTTPException(400,"Password must be at least 8 characters")
        if len(x.password)>1024: raise HTTPException(400,"Password must be at most 1024 characters")
        c=init_db(); uid=secrets.token_hex(12); farm_id=secrets.token_hex(12)
        try:
            # Account and initial farm must be created atomically; never leave a half-created account.
            with c.transaction():
                c.execute("INSERT INTO synpora_saas_users VALUES(?,?,?,?)",(uid,email,_hash(x.password),time.time()))
                c.execute("INSERT INTO synpora_saas_farms VALUES(?,?,?,?)",(farm_id,uid,"My first farm",time.time()))
        except Exception as e:
            message=str(e).lower()
            if "unique" in message or "duplicate key" in message or "synpora_saas_users.email" in message:
                raise HTTPException(409,"Email already registered")
            raise HTTPException(503,"Registration temporarily unavailable")
        return {"token":_token(uid),"user":{"id":uid,"email":email},"farm":{"id":farm_id,"name":"My first farm"}}

    @app.post("/api/v1/auth/login")
    def login(x:AuthIn):
        email=x.email.strip().lower()
        if len(x.password)>1024:
            raise HTTPException(400,"Password must be at most 1024 characters")
        # Hash the key so the limiter's in-memory index is not a list of raw email addresses.
        key=hashlib.sha256(email.encode("utf-8")).hexdigest()
        if not _login_allowed(key):
            raise HTTPException(429,"Too many login attempts. Try again in a few minutes.",headers={"Retry-After":str(_LOGIN_LOCKOUT_SECONDS)})
        c=init_db(); row=c.execute("SELECT * FROM synpora_saas_users WHERE email=?",(email,)).fetchone()
        stored_hash=row["password_hash"] if row else _DUMMY_PASSWORD_HASH
        password_ok=_verify(x.password,stored_hash)
        if not row or not password_ok:
            _login_failed(key)
            raise HTTPException(401,"Invalid credentials")
        with _login_limit_lock:
            _login_failures.pop(key,None)
        try: c.close()
        except Exception: pass
        return {"token":_token(row["id"]),"user":{"id":row["id"],"email":row["email"]}}

    @app.get("/api/v1/auth/me")
    def me(authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db(); row=c.execute("SELECT id,email,created_at FROM synpora_saas_users WHERE id=?",(uid,)).fetchone()
        if not row: raise HTTPException(404,"User not found")
        return dict(row)

    @app.get("/api/v1/farms")
    def farms(authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        return [dict(r) for r in c.execute("SELECT id,name,created_at FROM synpora_saas_farms WHERE user_id=? ORDER BY created_at",(uid,)).fetchall()]

    @app.post("/api/v1/farms")
    def create_farm(x:FarmIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db(); fid=secrets.token_hex(12)
        c.execute("INSERT INTO synpora_saas_farms VALUES(?,?,?,?)",(fid,uid,x.name.strip()[:120] or "Farm",time.time()))
        try: c.commit()
        except Exception: pass
        return {"id":fid,"name":x.name.strip()[:120] or "Farm"}

    @app.get("/api/v1/farms/{farm_id}/assets")
    def assets(farm_id:str,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        ok=c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone()
        if not ok: raise HTTPException(404,"Farm not found")
        return [dict(r) for r in c.execute("SELECT id,name,kind,power_kw,created_at FROM synpora_saas_assets WHERE farm_id=? ORDER BY created_at",(farm_id,)).fetchall()]

    @app.post("/api/v1/farms/{farm_id}/assets")
    def create_asset(farm_id:str,x:AssetIn,authorization:str|None=Header(default=None)):
        uid=user(authorization); c=init_db()
        ok=c.execute("SELECT 1 FROM synpora_saas_farms WHERE id=? AND user_id=?",(farm_id,uid)).fetchone()
        if not ok: raise HTTPException(404,"Farm not found")
        aid=secrets.token_hex(12); c.execute("INSERT INTO synpora_saas_assets VALUES(?,?,?,?,?,?)",(aid,farm_id,x.name.strip()[:120],x.kind.strip()[:50],x.power_kw,time.time()))
        try: c.commit()
        except Exception: pass
        return {"id":aid,"name":x.name.strip()[:120],"kind":x.kind.strip()[:50],"power_kw":x.power_kw}

    @app.get("/api/v1/system/database")
    def database():
        return {"persistent_database":"postgresql" if DB_URL else "sqlite_fallback","configured":bool(DB_URL)}

    # Prefer these explicit SaaS API handlers over legacy duplicate routes,
    # and keep the frontend root mount last so it cannot shadow API methods.
    try:
        from starlette.routing import Mount
        all_routes=list(app.router.routes)
        root_mounts=[r for r in all_routes if isinstance(r,Mount) and r.path in ("", "/")]
        new_routes=[r for r in all_routes if id(r) not in original_route_ids and r not in root_mounts]
        existing_routes=[r for r in all_routes if id(r) in original_route_ids and r not in root_mounts]
        app.router.routes=new_routes+existing_routes+root_mounts
    except Exception:
        pass

    # Ensure the market schema exists before the service accepts traffic. Existing
    # columns are preserved; PostgreSQL migration statements are idempotent.
    schema_connection=init_db()
    try:
        _ensure_market_table(schema_connection)
    finally:
        schema_connection.close()
    return app
