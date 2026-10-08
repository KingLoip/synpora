from pathlib import Path
import re

ROOT = Path("/app/frontend")
MARKER = "/* SYNPORA_ENHANCEMENT_V2 */"

SCRIPT = r"""
<script>
/* SYNPORA_ENHANCEMENT_V2 */
(function () {
  if (window.__synporaEnhanced) return;
  window.__synporaEnhanced = true;

  const css = `
    #synpora-nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 auto 18px;max-width:1558px;padding:0 2px}
    #synpora-nav button{background:#0d1822;border:1px solid #203747;color:#9dc3df;border-radius:10px;padding:9px 14px;font-weight:600;cursor:pointer}
    #synpora-nav button:hover,#synpora-nav button.active{border-color:#4de0b1;color:#4de0b1;background:#10231f}
    #synpora-toast{position:fixed;right:24px;bottom:24px;z-index:9999;background:#0d1a24;border:1px solid #2d5c60;color:#e9fbf5;border-radius:12px;padding:13px 16px;box-shadow:0 12px 40px #0008;opacity:0;transform:translateY(12px);transition:.2s}
    #synpora-toast.show{opacity:1;transform:none}
    #synpora-panel{display:none;max-width:1558px;margin:0 auto 22px;padding:20px;border:1px solid #203747;border-radius:16px;background:#0b151e}
    #synpora-panel.show{display:block}
    .sp-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
    .sp-card{padding:16px;border:1px solid #203747;border-radius:12px;background:#0d1923}
    .sp-label{font-size:12px;color:#82a9c5;text-transform:uppercase;letter-spacing:.08em}
    .sp-value{font-size:24px;font-weight:700;margin-top:6px;color:#f3f8fb}
    .sp-good{color:#4de0b1}.sp-warn{color:#ffd166}
    .sp-row{display:flex;justify-content:space-between;gap:16px;padding:11px 0;border-bottom:1px solid #1b2d39}
    .sp-action{margin-top:16px;background:#4de0b1;color:#06120e;border:0;border-radius:10px;padding:10px 15px;font-weight:800;cursor:pointer}
    #sp-account{margin-left:auto;display:flex;gap:8px;align-items:center}
    #sp-account button{background:transparent;border:1px solid #294253;color:#a9c6d9;border-radius:10px;padding:8px 12px;font-weight:700;cursor:pointer}
    #sp-modal{position:fixed;inset:0;background:#02070bcc;z-index:10000;display:none;align-items:center;justify-content:center;padding:20px}
    #sp-modal.show{display:flex}
    .sp-modal-card{width:min(520px,100%);background:#0b151e;border:1px solid #294253;border-radius:18px;padding:24px;box-shadow:0 30px 100px #000b}
    .sp-input{width:100%;box-sizing:border-box;background:#08111a;border:1px solid #294253;color:#eef8ff;border-radius:10px;padding:12px;margin:7px 0 12px}
    .sp-mini{font-size:13px;color:#7f9db2}
    @media(max-width:700px){#synpora-nav{padding:0 12px}.sp-grid{grid-template-columns:1fr}#sp-account{width:100%;margin-left:0}}
  `;
  document.head.insertAdjacentHTML("beforeend","<style>"+css+"</style>");

  const toast = (msg) => {
    let t=document.getElementById("synpora-toast");
    if(!t){t=document.createElement("div");t.id="synpora-toast";document.body.appendChild(t)}
    t.textContent=msg;t.classList.add("show");clearTimeout(window.__spToast);
    window.__spToast=setTimeout(()=>t.classList.remove("show"),2600);
  };

  const modal = document.createElement("div");
  modal.id="sp-modal";
  modal.innerHTML=`
    <div class="sp-modal-card">
      <div class="sp-label">SYNPORA ACCOUNT</div>
      <h2 style="margin:6px 0 8px">Workspace öffnen</h2>
      <p class="sp-mini">Pilot-Modus: Zugang wird lokal im Browser gespeichert. Die echte Benutzerverwaltung folgt mit PostgreSQL.</p>
      <label class="sp-mini">E-Mail</label>
      <input id="sp-email" class="sp-input" type="email" placeholder="you@company.com">
      <label class="sp-mini">Farm / Workspace</label>
      <input id="sp-farm" class="sp-input" placeholder="Meine Energy Farm">
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="sp-action" id="sp-cancel">Abbrechen</button>
        <button class="sp-action" id="sp-enter">Workspace erstellen</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  const panel = document.createElement("section");
  panel.id="synpora-panel";
  panel.innerHTML=`
    <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:14px">
      <div><div class="sp-label">SYNPORA Workspace</div><h2 style="margin:4px 0">Demo Farm Control Center</h2></div>
      <button class="sp-action" id="sp-close">Close</button>
    </div>
    <div class="sp-grid">
      <div class="sp-card"><div class="sp-label">PV production</div><div class="sp-value sp-good">18.4 kW</div><div>Solar forecast: 92%</div></div>
      <div class="sp-card"><div class="sp-label">Battery</div><div class="sp-value">74%</div><div>12.1 kWh available</div></div>
      <div class="sp-card"><div class="sp-label">Compute capacity</div><div class="sp-value">42.0 kW</div><div>AI + BTC pool</div></div>
      <div class="sp-card"><div class="sp-label">Today's modeled uplift</div><div class="sp-value sp-good">+€18.70</div><div>vs. BTC-only baseline</div></div>
    </div>
    <div style="margin-top:18px">
      <div class="sp-row"><span>AI Compute</span><strong class="sp-good">0.135 €/kWh</strong></div>
      <div class="sp-row"><span>BTC Mining</span><strong>0.083 €/kWh</strong></div>
      <div class="sp-row"><span>Battery / Grid</span><strong>0.071 €/kWh</strong></div>
      <button class="sp-action" id="sp-sim">Open What-if Simulation</button>
    </div>`;
  
  const nav = document.createElement("nav");
  nav.id="synpora-nav";
  ["Overview","Assets","Economics","AI Decision","Simulation","Reports"].forEach((name,i)=>{
    const b=document.createElement("button");b.textContent=name;if(i===0)b.classList.add("active");
    b.onclick=()=>{
      nav.querySelectorAll("button").forEach(x=>x.classList.remove("active"));b.classList.add("active");
      if(name==="Overview"){panel.classList.remove("show");window.scrollTo({top:0,behavior:"smooth"});return}
      panel.classList.add("show");
      if(name==="Assets") setTimeout(loadAssets,50);
      if(name==="Economics" || name==="AI Decision") setTimeout(runOptimizer,80);\n      if(name==="Simulation") setTimeout(runScenarioLab,80);\n      if(name==="Reports") setTimeout(loadBenchmark,120);
      const titles={Assets:"Asset Registry",Economics:"Economics & Value per kWh","AI Decision":"AI Decision Center",Simulation:"What-if Simulation Lab",Reports:"Reports & Audit Trail"};
      panel.querySelector("h2").textContent=titles[name]||"SYNPORA Workspace";
      toast(name+" workspace geöffnet");
      panel.scrollIntoView({behavior:"smooth",block:"start"});
    };
    nav.appendChild(b);
  });



  async function loadBenchmark(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); if(!acc?.token){toast("Bitte zuerst Workspace verbinden");return}
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()); if(!farms[0])return;
    const d=await fetch("/api/v1/farms/"+farms[0].id+"/benchmark",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const old=panel.querySelector(".sp-benchmark"); if(old)old.remove();
    const box=document.createElement("div");box.className="sp-benchmark sp-card";box.style.marginTop="18px";
    box.innerHTML="<div class='sp-label'>HISTORICAL BENCHMARK</div><div class='sp-value'>"+d.samples+" snapshots</div><div style='margin-top:8px'>AI: €"+d.ai_total_net_eur.toFixed(2)+" · BTC: €"+d.btc_total_net_eur.toFixed(2)+"</div><div class='sp-good' style='margin-top:6px'>AI delta vs BTC: €"+d.ai_delta_vs_btc_eur.toFixed(2)+"</div><div class='sp-mini' style='margin-top:6px'>AI war in "+(d.winner_share==null?"–":Math.round(d.winner_share*100)+"%")+" der gespeicherten Zeitpunkte besser.</div>";
    panel.appendChild(box);
  }

  async function runScenarioLab(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null");
    if(!acc?.token){toast("Bitte zuerst Workspace verbinden");return}
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    if(!farms[0]) return;
    const energy=Number(window.prompt("Szenario-Energie in kWh","100")||100);
    const base={energy_kwh:energy,energy_cost_eur_kwh:0.05};
    const res=await fetch("/api/v1/farms/"+farms[0].id+"/scenario",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+acc.token},body:JSON.stringify(base)});
    const d=await res.json(); if(!res.ok){toast(d.detail||"Szenario fehlgeschlagen");return}
    const old=panel.querySelector(".sp-scenario"); if(old) old.remove();
    const box=document.createElement("div"); box.className="sp-scenario"; box.style.marginTop="18px";
    box.innerHTML="<div class='sp-label'>WHAT-IF SCENARIO · "+energy+" kWh</div>"+d.ranking.map((r,i)=>"<div class='sp-row'><span>"+(i===0?"🏆 ":"")+r.option+"</span><strong>"+r.value_eur_kwh.toFixed(3)+" €/kWh · €"+r.net_eur.toFixed(2)+" net</strong></div>").join("")+"<button class='sp-action' id='sp-backtest'>Run Market Sensitivity</button>";
    panel.appendChild(box); document.getElementById("sp-backtest").onclick=runBacktest;
  }
  async function runBacktest(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const d=await fetch("/api/v1/farms/"+farms[0].id+"/backtest",{method:"POST",headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const box=document.querySelector(".sp-scenario"); if(!box)return;
    const wins=Object.entries(d.wins).map(([k,v])=>"<div class='sp-row'><span>"+k+"</span><strong>"+v+"/7 Szenarien</strong></div>").join("");
    box.insertAdjacentHTML("beforeend","<div style='margin-top:16px'><div class='sp-label'>MARKET SENSITIVITY</div>"+wins+"<div class='sp-card' style='margin-top:10px'>AI cumulative: €"+d.cumulative_net_eur.ai.toFixed(2)+" · BTC cumulative: €"+d.cumulative_net_eur.btc.toFixed(2)+" · AI delta: €"+d.cumulative_net_eur.delta_ai_vs_btc.toFixed(2)+"</div></div>");
    toast("Sensitivitäts-Backtest abgeschlossen");
  }

  async function runOptimizer(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null");
    if(!acc?.token){toast("Bitte zuerst Workspace verbinden");return}
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    if(!farms[0]){toast("Keine Farm vorhanden");return}
    const energy=Number(window.prompt("Verfügbare Energie in kWh","10")||10);
    const market=await fetch("/api/v1/market/live").then(r=>r.json()); const r=await fetch("/api/v1/farms/"+farms[0].id+"/optimize",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+acc.token},body:JSON.stringify({energy_kwh:energy,btc_hashprice_usd_ph_day:market.btc_hashprice_usd_ph_day,eur_usd:market.eur_usd,gpu_hourly_usd:(market.gpu||{}).hourly_usd||1.09,gpu_power_kw:(market.gpu||{}).power_kw||0.35,gpu_utilization:(market.gpu||{}).utilization||0.70,gpu_platform_fee:(market.gpu||{}).platform_fee||0.15})});
    const d=await r.json(); if(!r.ok){toast(d.detail||"Optimierung fehlgeschlagen");return}
    panel.querySelector(".sp-opt")?.remove();
    const box=document.createElement("div");box.className="sp-opt";box.style.marginTop="18px";
    box.innerHTML="<div class='sp-label'>AI VALUE OPTIMIZER</div><div class='sp-card'><div class='sp-label'>BEST OPTION</div><div class='sp-value sp-good'>"+d.best.option+"</div><div>"+d.best.value_eur_kwh.toFixed(3)+" €/kWh · Netto €"+d.net_value_eur.toFixed(2)+" · Confidence "+Math.round(d.confidence*100)+"%</div></div><div class='sp-row'><span>BTC Mining</span><strong>"+d.alternatives.find(x=>x.option==="BTC Mining").value_eur_kwh.toFixed(3)+" €/kWh</strong></div><div class='sp-row'><span>Battery</span><strong>"+d.alternatives.find(x=>x.option==="Battery").value_eur_kwh.toFixed(3)+" €/kWh</strong></div>";
    panel.appendChild(box);
  }

  async function loadAssets(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null");
    if(!acc?.token){toast("Bitte zuerst Workspace verbinden");return}
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const farm=farms[0]; if(!farm){toast("Noch keine Farm vorhanden");return}
    const assets=await fetch("/api/v1/farms/"+farm.id+"/assets",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const box=panel.querySelector(".sp-assets")||document.createElement("div");
    box.className="sp-assets"; box.style.marginTop="18px";
    const rows=assets.map(a=>"<div class='sp-row'><span>"+a.name+" · "+a.kind+"</span><strong>"+Number(a.power_kw).toFixed(1)+" kW</strong></div>").join("");
    box.innerHTML="<div class='sp-label'>CONNECTED ASSETS</div>"+(rows||"<div class='sp-mini'>Noch keine Assets.</div>")+"<button class='sp-action' id='sp-add-asset'>+ Asset hinzufügen</button>";
    panel.appendChild(box); document.getElementById("sp-add-asset").onclick=addAsset;
  }
  async function addAsset(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); if(!acc?.token)return;
    const name=window.prompt("Asset-Name, z.B. GPU Server"); if(!name)return;
    const kind=window.prompt("Typ: BTC / GPU / PV / BATTERY / GRID / OTHER","GPU"); if(!kind)return;
    const power=Number(window.prompt("Leistung in kW","10")||0);
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const res=await fetch("/api/v1/farms/"+farms[0].id+"/assets",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+acc.token},body:JSON.stringify({name,kind,power_kw:power})});
    if(!res.ok){toast("Asset konnte nicht gespeichert werden");return}
    toast("Asset gespeichert"); loadAssets();
  }

  function mount(){
    if(!document.body || document.getElementById("synpora-nav")) return;
    const main=document.querySelector("main")||document.body.firstElementChild;
    const account=document.createElement("div");
    account.id="sp-account";
    const saved=JSON.parse(localStorage.getItem("synpora_account")||"null");
    account.innerHTML=saved
      ? '<span class="sp-mini">'+(saved.farm||"Demo Farm")+'</span><button id="sp-account-btn">Workspace</button>'
      : '<button id="sp-account-btn">Pilot Login</button>';
    nav.appendChild(account);
    if(main && main.parentNode) main.parentNode.insertBefore(nav,main);
    else document.body.insertBefore(nav,document.body.firstChild);
    if(main && main.parentNode) main.parentNode.insertBefore(panel,main);
    else document.body.insertBefore(panel,nav.nextSibling);

    const demo=[...document.querySelectorAll("button,a")].find(el=>/Demo Farm laden/i.test(el.textContent||""));
    if(demo) demo.addEventListener("click",()=>{
      localStorage.setItem("synpora_demo_farm","1");
      toast("Demo Farm geladen · SYNPORA ist bereit für Simulationen");
      panel.classList.add("show");
      panel.scrollIntoView({behavior:"smooth",block:"start"});
    });
    document.getElementById("sp-close").onclick=()=>panel.classList.remove("show");
    document.getElementById("sp-account-btn").onclick=()=>modal.classList.add("show");
    document.getElementById("sp-cancel").onclick=()=>modal.classList.remove("show");
    document.getElementById("sp-enter").onclick=async()=>{
      const email=document.getElementById("sp-email").value.trim();
      const farm=document.getElementById("sp-farm").value.trim()||"Demo Farm";
      const password=window.prompt("Passwort für den Pilot-Account (mind. 8 Zeichen):")||"";
      if(!email||password.length<8){toast("E-Mail und Passwort (mind. 8 Zeichen) erforderlich");return}
      try{
        let res=await fetch("/api/v1/auth/register",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,password})});
        let data=await res.json();
        if(!res.ok && res.status===409){
          res=await fetch("/api/v1/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,password})});
          data=await res.json();
        }
        if(!res.ok) throw new Error(data.detail||"Account konnte nicht erstellt werden");
        localStorage.setItem("synpora_account",JSON.stringify({email,farm,token:data.token,user:data.user}));
        modal.classList.remove("show");
        toast("Workspace "+farm+" ist verbunden");
        const label=account.querySelector(".sp-mini");
        if(label) label.textContent=farm;
        else account.insertAdjacentHTML("afterbegin",'<span class="sp-mini">'+farm+'</span>');
        document.getElementById("sp-account-btn").textContent="Workspace";
        panel.classList.add("show");
        panel.scrollIntoView({behavior:"smooth",block:"start"});
      }catch(e){toast(e.message)}
    };
    document.getElementById("sp-sim").onclick=()=>runScenarioLab();
  }
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",mount); else mount();
  setTimeout(mount,800);
  setTimeout(mount,2000);
})();
</script>
"""

files = list(ROOT.rglob("*.html")) if ROOT.exists() else []
patched = 0
for p in files:
    s = p.read_text(encoding="utf-8", errors="ignore")
    if MARKER in s:
        continue
    if "</body>" in s:
        s = s.replace("</body>", SCRIPT + "\n</body>")
    else:
        s += SCRIPT
    p.write_text(s, encoding="utf-8")
    patched += 1

print(f"SYNPORA enhancement injected into {patched} HTML file(s)")
