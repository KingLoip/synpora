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
        battery_power_kw: float=100.0
        battery_charge_efficiency: float=0.95
        battery_discharge_efficiency: float=0.95

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