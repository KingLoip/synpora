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
    .sp-mini{font-size:13px;color:#9bb1c2}.sp-status{font-size:13px;min-height:18px;color:#c5d5e2;margin:8px 0}.sp-field{display:block;font-size:13px;color:#a9c6d9;margin-top:10px}.sp-field input{display:block;width:100%;box-sizing:border-box;background:#08111a;border:1px solid #294253;color:#eef8ff;border-radius:10px;padding:12px;margin-top:6px}.sp-demo-note{border:1px solid #66572c;background:#241f12;color:#f3dfa2;border-radius:10px;padding:12px;margin:0 0 14px}.sp-muted{color:#9bb1c2;font-size:13px}
    @media(max-width:700px){#synpora-nav{padding:0 12px}.sp-grid{grid-template-columns:1fr}#sp-account{width:100%;margin-left:0}}
  `;
  document.head.insertAdjacentHTML("beforeend","<style>"+css+"</style>");

  const toast = (msg) => {
    let t=document.getElementById("synpora-toast");
    if(!t){t=document.createElement("div");t.id="synpora-toast";document.body.appendChild(t)}
    t.textContent=msg;t.classList.add("show");clearTimeout(window.__spToast);
    window.__spToast=setTimeout(()=>t.classList.remove("show"),2600);
  };

  const escapeHtml=(value)=>String(value??"").replace(/[&<>"\']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","\'":"&#39;"}[ch]));

  const modal = document.createElement("div");
  modal.id="sp-modal";
  modal.innerHTML=`
    <div class="sp-modal-card">
      <div class="sp-label">SYNPORA ACCOUNT</div>
      <h2 style="margin:6px 0 8px">Workspace öffnen</h2>
      <p class="sp-mini">Create a workspace or sign in with an existing account. New accounts may require email verification. Your session is stored in this browser, so use a trusted device.</p>
      <label class="sp-mini" for="sp-email">Email address</label>
      <input id="sp-email" class="sp-input" type="email" autocomplete="email" placeholder="you@company.com" required>
      <label class="sp-mini" for="sp-farm">Workspace name (optional)</label>
      <input id="sp-farm" class="sp-input" autocomplete="organization" placeholder="My energy farm">
      <label class="sp-mini" for="sp-password">Password (at least 8 characters)</label>
      <input id="sp-password" class="sp-input" type="password" autocomplete="current-password" minlength="8" maxlength="1024" placeholder="Enter your password" required>
      <p id="sp-auth-status" class="sp-status" role="status" aria-live="polite"></p>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="sp-action" id="sp-cancel">Cancel</button>
        <button class="sp-action" id="sp-logout" style="display:none">Sign out</button><button class="sp-action" id="sp-enter">Continue</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  const panel = document.createElement("section");
  panel.id="synpora-panel";
  panel.innerHTML=`
    <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:14px">
      <div><div class="sp-label">SYNPORA · DECISION SUPPORT</div><h2 style="margin:4px 0">Demo workspace · sample values</h2></div>
      <button class="sp-action" id="sp-close">Close workspace</button>
    </div>
    <div class="sp-demo-note" role="note"><strong>Illustrative demo data — not live telemetry.</strong><div class="sp-muted">These figures demonstrate the workflow only. They are not a forecast, verified savings, or a recommendation for your site.</div></div>
    <div class="sp-grid">
      <div class="sp-card"><div class="sp-label">Solar production · example</div><div class="sp-value sp-good">18.4 kW</div><div>Illustrative forecast: 92%</div></div>
      <div class="sp-card"><div class="sp-label">Battery charge · example</div><div class="sp-value">74%</div><div>Example available energy: 12.1 kWh</div></div>
      <div class="sp-card"><div class="sp-label">Compute capacity · example</div><div class="sp-value">42.0 kW</div><div>Illustrative AI + BTC setup</div></div>
      <div class="sp-card"><div class="sp-label">Modeled uplift · example only</div><div class="sp-value sp-good">+€18.70</div><div>Illustrative comparison with BTC-only baseline; not a verified result</div></div>
    </div>
    <div style="margin-top:18px">
      <div class="sp-row"><span>AI compute · example</span><strong class="sp-good">0.135 €/kWh</strong></div>
      <div class="sp-row"><span>Bitcoin mining · example</span><strong>0.083 €/kWh</strong></div>
      <div class="sp-row"><span>Battery / grid export · example</span><strong>0.071 €/kWh</strong></div>
      <label class="sp-field" for="sp-energy-kwh">Energy to compare (kWh)<input id="sp-energy-kwh" type="number" min="0.1" max="1000000" step="0.1" value="100" inputmode="decimal"></label>
      <label class="sp-field" for="sp-energy-price">Your electricity tariff (€/kWh)<input id="sp-energy-price" type="number" min="0" max="10" step="0.001" placeholder="Enter your tariff for a workspace run" inputmode="decimal"></label>
      <p class="sp-muted">Without a workspace, the demo uses an explicitly illustrative €0.05/kWh tariff only when this field is left blank. Workspace scenarios require your own tariff.</p>
      <p class="sp-muted">Check your assumptions before acting. SYNPORA provides decision support and does not control hardware automatically.</p>
      <button class="sp-action" id="sp-sim">Run example simulation</button>
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
      if(name==="Economics") setTimeout(runOptimizer,80);
      if(name==="AI Decision"){setTimeout(runOptimizer,80);setTimeout(runDecisionRisk,300);}
      if(name==="Simulation") {setTimeout(runScenarioLab,80);setTimeout(runPortfolio,300);setTimeout(runDispatch,500);}
      if(name==="Reports"){setTimeout(loadBenchmark,120);setTimeout(loadLearningHealth,180);setTimeout(loadDecisionQuality,240);}
      const titles={Assets:"Asset Registry",Economics:"Economics & Value per kWh","AI Decision":"AI Decision Center",Simulation:"What-if Simulation Lab",Reports:"Reports & Audit Trail"};
      panel.querySelector("h2").textContent=titles[name]||"SYNPORA Workspace";
      toast(name+" workspace opened");
      panel.scrollIntoView({behavior:"smooth",block:"start"});
    };
    nav.appendChild(b);
  });



  async function loadLearningHealth(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); if(!acc?.token)return;
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()); if(!farms[0])return;
    const id=farms[0].id;
    const d=await fetch("/api/v1/farms/"+id+"/learning",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const fh=await fetch("/api/v1/farms/"+id+"/forecast-health",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()).catch(()=>({}));
    const ai=await fetch("/api/v1/farms/"+id+"/ai-status",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()).catch(()=>({}));
    const old=panel.querySelector(".sp-learning"); if(old)old.remove();
    const box=document.createElement("div");box.className="sp-learning sp-card";box.style.marginTop="18px";
    const btc=fh.btc||{},gpu=fh.gpu||{};
    box.innerHTML="<div class='sp-label'>SELF-LEARNING CORE</div><div class='sp-value'>"+escapeHtml(ai.status||"unknown")+"</div><div style='margin-top:8px'>Samples: "+d.samples+" · Settled: "+d.settled+" · AI Core: "+(ai.total_settled_samples||0)+" settled</div><div class='sp-mini'>MAE: "+(d.mae_eur_kwh==null?"–":d.mae_eur_kwh.toFixed(4)+" €/kWh")+"</div><div class='sp-row' style='margin-top:10px'><span>BTC learning</span><strong>"+(btc.samples||0)+" samples · "+(btc.forecast_hit_rate==null?"–":Math.round(btc.forecast_hit_rate*100)+"% hit")+"</strong></div><div class='sp-row'><span>GPU learning</span><strong>"+(gpu.samples||0)+" samples · "+(gpu.forecast_hit_rate==null?"–":Math.round(gpu.forecast_hit_rate*100)+"% hit")+"</strong></div><button class='sp-action' id='sp-learn-update' style='margin-top:10px'>Update Learning</button>";
    panel.appendChild(box);
    document.getElementById("sp-learn-update").onclick=async()=>{
      const r=await fetch("/api/v1/farms/"+id+"/learning/update",{method:"POST",headers:{Authorization:"Bearer "+acc.token}});
      const x=await r.json(); toast("Learning aktualisiert: "+(x.settlement?.settled_now||0)+" Decisions settled"); loadLearningHealth();
    };
  }

  async function loadDecisionQuality(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); if(!acc?.token)return;
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()); if(!farms[0])return;
    const id=farms[0].id;
    const d=await fetch("/api/v1/farms/"+id+"/decision-quality",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()).catch(()=>({}));
    const old=panel.querySelector(".sp-quality"); if(old)old.remove();
    const box=document.createElement("div");box.className="sp-quality sp-card";box.style.marginTop="18px";
    const hit=d.winner_accuracy==null?"–":Math.round(Number(d.winner_accuracy)*100)+"%";
    const regret=d.avg_regret_eur_kwh==null?"–":Number(d.avg_regret_eur_kwh).toFixed(4)+" €/kWh";
    box.innerHTML="<div class='sp-label'>DECISION QUALITY</div><div class='sp-grid'><div><div class='sp-label'>Winner accuracy</div><div class='sp-value'>"+hit+"</div></div><div><div class='sp-label'>Average regret</div><div class='sp-value'>"+regret+"</div></div><div><div class='sp-label'>Settled decisions</div><div class='sp-value'>"+Number(d.settled||0)+"</div></div></div><div class='sp-mini' style='margin-top:10px'>Regret misst den entgangenen Wert gegenüber der tatsächlich besten Option im Nachhinein.</div>";
    panel.appendChild(box);
  }

  async function loadBenchmark(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); if(!acc?.token){toast("Please sign in to open your workspace.");return}
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()); if(!farms[0])return;
    const d=await fetch("/api/v1/farms/"+farms[0].id+"/benchmark",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const old=panel.querySelector(".sp-benchmark"); if(old)old.remove();
    const box=document.createElement("div");box.className="sp-benchmark sp-card";box.style.marginTop="18px";
    box.innerHTML="<div class='sp-label'>HISTORICAL BENCHMARK</div><div class='sp-value'>"+d.samples+" snapshots</div><div style='margin-top:8px'>AI: €"+d.ai_total_net_eur.toFixed(2)+" · BTC: €"+d.btc_total_net_eur.toFixed(2)+"</div><div class='sp-good' style='margin-top:6px'>AI delta vs BTC: €"+d.ai_delta_vs_btc_eur.toFixed(2)+"</div><div class='sp-mini' style='margin-top:6px'>AI war in "+(d.winner_share==null?"–":Math.round(d.winner_share*100)+"%")+" der gespeicherten Zeitpunkte besser.</div>";
    panel.appendChild(box);
  }

  async function runDispatch(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); if(!acc?.token){toast("Bitte zuerst Workspace verbinden");return}
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()); if(!farms[0])return;
    const d=await fetch("/api/v1/farms/"+farms[0].id+"/dispatch-plan",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+acc.token},body:JSON.stringify({horizon_hours:24,interval_hours:1,energy_cost_eur_kwh:0.05,pv_kwh:100,battery_soc_pct:74,battery_capacity_kwh:50,battery_reserve_pct:20})}).then(r=>r.json());
    const old=panel.querySelector(".sp-dispatch");if(old)old.remove();
    const summary=d.summary||{};
    const box=document.createElement("div");box.className="sp-dispatch sp-card";box.style.marginTop="18px";
    box.innerHTML="<div class='sp-label'>24H MULTI-PERIOD DISPATCH</div><div class='sp-value sp-good'>€"+Number(summary.net_value_eur||0).toFixed(2)+" modeled net</div>"+(d.plan||[]).slice(0,16).map(r=>"<div class='sp-row'><span>"+String(r.hour).padStart(2,"0")+":00 · "+escapeHtml(r.asset)+" · "+escapeHtml(r.action)+"</span><strong>"+Number(r.pv_kwh||0).toFixed(1)+" kWh · "+Number(r.value_eur_kwh||0).toFixed(3)+" €/kWh</strong></div>").join("")+"<div class='sp-mini' style='margin-top:8px'>BTC: "+Number(summary.btc_kwh||0).toFixed(1)+" kWh · GPU: "+Number(summary.gpu_kwh||0).toFixed(1)+" kWh · Grid: "+Number(summary.grid_export_kwh||0).toFixed(1)+" kWh · Recommendation only</div>";
    panel.appendChild(box);
  }

  async function runDecisionRisk(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); if(!acc?.token){toast("Bitte zuerst Workspace verbinden");return}
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()); if(!farms[0])return;
    const riskInput=window.prompt("Risk Aversion 0–1","0.75");
    if(riskInput===null)return;
    const parsedRisk=Number(riskInput); const riskAversion=Number.isFinite(parsedRisk)?Math.max(0,Math.min(1,parsedRisk)):0.75;
    const d=await fetch("/api/v1/farms/"+farms[0].id+"/decision-engine",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+acc.token},body:JSON.stringify({energy_kwh:100,energy_cost_eur_kwh:0.05,risk_aversion:riskAversion,scenarios:500,seed:42})}).then(r=>r.json());
    const old=panel.querySelector(".sp-risk");if(old)old.remove();
    if(d.detail){toast(d.detail);return}
    const box=document.createElement("div");box.className="sp-risk sp-card";box.style.marginTop="18px";
    box.innerHTML="<div class='sp-label'>UNIFIED AI DECISION · RISK + REGRET</div><div class='sp-value sp-good'>"+escapeHtml(d.recommended)+"</div><div class='sp-mini'>Confidence "+Math.round(Number(d.confidence||0)*100)+"% · Risk Aversion "+Number(d.risk_aversion||0).toFixed(2)+" · "+d.samples+" Monte-Carlo scenarios</div><div class='sp-row'><span>Expected value</span><strong>"+Number(d.decision?.expected_value_eur_kwh||0).toFixed(4)+" €/kWh</strong></div><div class='sp-row'><span>P05</span><strong>"+Number(d.decision?.p05_eur_kwh||0).toFixed(4)+" €/kWh</strong></div><div class='sp-row'><span>Win probability</span><strong>"+Math.round(Number(d.decision?.win_probability||0)*100)+"%</strong></div><div class='sp-row'><span>Expected regret</span><strong>"+Number(d.decision?.expected_regret_eur_kwh||0).toFixed(4)+" €/kWh</strong></div><div class='sp-mini' style='margin-top:10px'>Fallback: "+escapeHtml(d.fallback||"—")+" · Recommendation only · No hardware action</div>";
    panel.appendChild(box);
  }

  async function runPortfolio(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); if(!acc?.token){toast("Bitte zuerst Workspace verbinden");return}
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json()); if(!farms[0])return;
    const energy=Number(window.prompt("Verfügbare Gesamtenergie in kWh","100")||100);
    const d=await fetch("/api/v1/farms/"+farms[0].id+"/portfolio-optimize",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+acc.token},body:JSON.stringify({energy_kwh:energy,horizon_hours:1,energy_cost_eur_kwh:0.05})}).then(r=>r.json());
    const old=panel.querySelector(".sp-portfolio");if(old)old.remove();
    const box=document.createElement("div");box.className="sp-portfolio sp-card";box.style.marginTop="18px";
    box.innerHTML="<div class='sp-label'>PORTFOLIO OPTIMIZER</div><div class='sp-value sp-good'>€"+d.total_net_eur.toFixed(2)+" net</div>"+d.allocation.map(a=>"<div class='sp-row'><span>"+escapeHtml(a.asset)+" · "+escapeHtml(a.kind)+"</span><strong>"+a.allocated_kwh.toFixed(1)+" kWh · €"+a.net_eur.toFixed(2)+"</strong></div>").join("")+"<div class='sp-mini' style='margin-top:8px'>Unallocated: "+d.unallocated_kwh.toFixed(1)+" kWh · Recommendation only</div>";
    panel.appendChild(box);
  }

  async function runScenarioLab(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null");
    const energyText=document.getElementById("sp-energy-kwh")?.value?.trim()||"";
    const energy=Number(energyText);
    const priceText=document.getElementById("sp-energy-price")?.value?.trim()||"";
    if(!energyText||!Number.isFinite(energy)||energy<0.1||energy>1000000){toast("Enter an energy amount between 0.1 and 1,000,000 kWh");return}
    const old=panel.querySelector(".sp-scenario");if(old)old.remove();
    let ranking=[],title="",note="",workspaceRun=false;
    if(!acc?.token){
      const tariff=priceText===""?0.05:Number(priceText);
      if(!Number.isFinite(tariff)||tariff<0||tariff>10){toast("Enter a tariff between €0 and €10 per kWh");return}
      ranking=[
        {option:"AI compute",value_eur_kwh:0.135,net_eur:energy*(0.135-tariff)},
        {option:"Bitcoin mining",value_eur_kwh:0.083,net_eur:energy*(0.083-tariff)},
        {option:"Battery / grid export",value_eur_kwh:0.071,net_eur:energy*(0.071-tariff)}
      ].sort((a,b)=>b.net_eur-a.net_eur);
      title="ILLUSTRATIVE DEMO SCENARIO · "+energy+" kWh";
      note="Example values only. Assumed electricity tariff: €"+tariff.toFixed(3)+"/kWh. Not live market data or a site-specific recommendation.";
    }else{
      if(priceText===""){toast("Enter your actual electricity tariff before running a workspace scenario.");return}
      const tariff=Number(priceText);
      if(!Number.isFinite(tariff)||tariff<0||tariff>10){toast("Enter a tariff between €0 and €10 per kWh");return}
      try{
        const farmsRes=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}});
        const farms=await farmsRes.json().catch(()=>[]);
        if(!farmsRes.ok||!farms[0]){toast("Could not load your workspace. Please sign in again.");return}
        const res=await fetch("/api/v1/farms/"+farms[0].id+"/scenario",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+acc.token},body:JSON.stringify({energy_kwh:energy,energy_cost_eur_kwh:tariff})});
        const d=await res.json().catch(()=>({detail:"Unexpected server response"}));
        if(!res.ok){toast(d.detail||"Scenario could not be calculated.");return}
        if(!Array.isArray(d.ranking)){toast("The scenario response was incomplete.");return}
        ranking=d.ranking;workspaceRun=true;
        title="WORKSPACE SCENARIO · "+energy+" kWh";
        note="Using your entered electricity tariff of €"+tariff.toFixed(3)+"/kWh. Results depend on the available market inputs and their freshness.";
      }catch(e){toast("Could not calculate the scenario. Please try again.");return}
    }
    const box=document.createElement("div");box.className="sp-scenario";box.style.marginTop="18px";
    const label=document.createElement("div");label.className="sp-label";label.textContent=title;box.appendChild(label);
    const disclaimer=document.createElement("p");disclaimer.className="sp-muted";disclaimer.textContent=note;box.appendChild(disclaimer);
    ranking.forEach((r,i)=>{
      const row=document.createElement("div");row.className="sp-row";
      const name=document.createElement("span");name.textContent=(i===0?"★ ":"")+String(r.option||"Option");
      const value=document.createElement("strong");
      const per=Number(r.value_eur_kwh),net=Number(r.net_eur);
      value.textContent=(Number.isFinite(per)?per.toFixed(3):"—")+" €/kWh · "+(Number.isFinite(net)?"€"+net.toFixed(2):"—")+" net";
      row.append(name,value);box.appendChild(row);
    });
    if(workspaceRun){
      const backtest=document.createElement("button");backtest.className="sp-action";backtest.id="sp-backtest";backtest.textContent="Run market sensitivity";box.appendChild(backtest);backtest.onclick=runBacktest;
    }
    panel.appendChild(box);
  }
  async function runBacktest(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null"); const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const d=await fetch("/api/v1/farms/"+farms[0].id+"/backtest",{method:"POST",headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const box=document.querySelector(".sp-scenario"); if(!box)return;
    const wins=Object.entries(d.wins).map(([k,v])=>"<div class='sp-row'><span>"+escapeHtml(k)+"</span><strong>"+v+"/7 scenarios</strong></div>").join("");
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
    box.innerHTML="<div class='sp-label'>AI VALUE OPTIMIZER</div><div class='sp-card'><div class='sp-label'>BEST OPTION</div><div class='sp-value sp-good'>"+escapeHtml(d.best.option)+"</div><div>"+d.best.value_eur_kwh.toFixed(3)+" €/kWh · Netto €"+d.net_value_eur.toFixed(2)+" · Confidence "+Math.round(d.confidence*100)+"%</div></div><div class='sp-row'><span>BTC Mining</span><strong>"+d.alternatives.find(x=>x.option==="BTC Mining").value_eur_kwh.toFixed(3)+" €/kWh</strong></div><div class='sp-row'><span>Battery</span><strong>"+d.alternatives.find(x=>x.option==="Battery").value_eur_kwh.toFixed(3)+" €/kWh</strong></div>";
    panel.appendChild(box);
    const intel=document.createElement("div");intel.className="sp-card";intel.style.marginTop="12px";intel.innerHTML="<div class='sp-label'>DECISION INTELLIGENCE</div><div class='sp-value'>"+(d.decision_score??"—")+"/100</div><div class='sp-mini'>"+escapeHtml(d.explanation?.primary_reason||"Model-driven recommendation")+" · margin "+(d.explanation?.value_margin_eur_kwh??0)+" €/kWh · confidence "+Math.round((d.confidence||0)*100)+"%</div>";panel.appendChild(intel);
  }

  async function loadAssets(){
    const acc=JSON.parse(localStorage.getItem("synpora_account")||"null");
    if(!acc?.token){toast("Bitte zuerst Workspace verbinden");return}
    const farms=await fetch("/api/v1/farms",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const farm=farms[0]; if(!farm){toast("Noch keine Farm vorhanden");return}
    const assets=await fetch("/api/v1/farms/"+farm.id+"/assets",{headers:{Authorization:"Bearer "+acc.token}}).then(r=>r.json());
    const box=panel.querySelector(".sp-assets")||document.createElement("div");
    box.className="sp-assets"; box.style.marginTop="18px";
    const rows=assets.map(a=>"<div class='sp-row'><span>"+escapeHtml(a.name)+" · "+escapeHtml(a.kind)+"</span><strong>"+Number(a.power_kw).toFixed(1)+" kW</strong></div>").join("");
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
    if(!res.ok){toast("Could not save the asset.");return}
    toast("Asset saved."); loadAssets();
  }

  function polishBaseUI(){
    document.documentElement.lang="en";
    const intro=[...document.querySelectorAll("p")].find(el=>(el.textContent||"").includes("Welche Verwendung deines Stroms"));
    if(intro) intro.textContent="Which use of your electricity creates the most economic value right now? SYNPORA compares Bitcoin mining, AI compute, batteries and grid export using transparent assumptions — without automatic hardware control.";
    const addHeadingNote=(pattern,cls,copy)=>{
      const heading=[...document.querySelectorAll("h2,h3")].find(el=>pattern.test(el.textContent||""));
      if(heading&&!heading.parentElement.querySelector("."+cls)){
        const note=document.createElement("p");note.className=cls+" sp-muted";note.textContent=copy;
        heading.insertAdjacentElement("afterend",note);
      }
    };
    addHeadingNote(/^Value per kWh$/i,"sp-value-help","Results appear when the required market inputs and assumptions are available. Missing or stale data is not treated as verified.");
    addHeadingNote(/^AI Decision Center$/i,"sp-decision-help","Recommendations are decision support only. Review the assumptions and confidence before acting.");
    addHeadingNote(/^What-if Lab$/i,"sp-whatif-help","Change the inputs and run a simulation to compare scenarios. Results depend on the values and data sources available.");
    const demo=[...document.querySelectorAll("button,a")].find(el=>/Demo Farm laden/i.test(el.textContent||""));
    if(demo) demo.textContent="Load demo farm";
    const bootstrap=[...document.querySelectorAll("input")].find(el=>/railway bootstrap/i.test(el.placeholder||""));
    if(bootstrap){
      const candidates=[...document.querySelectorAll("section,article,form,div")].filter(el=>el.contains(bootstrap)&&(el.textContent||"").includes("Account & Pilot Access")&&!el.querySelector("h1")&&!([...el.querySelectorAll("button")].some(b=>/Demo Farm laden|Load demo farm/i.test(b.textContent||""))));
      candidates.sort((a,b)=>a.querySelectorAll("*").length-b.querySelectorAll("*").length);
      const section=candidates[0];
      if(section){
        section.innerHTML='<h2>Account & pilot access</h2><p>Try the demo without an account. To create or open a workspace, use the Pilot Login button above. SYNPORA provides recommendations only and does not control hardware automatically.</p><button type="button" class="sp-action" id="sp-open-account-inline">Open workspace</button>';
      }else{
        bootstrap.placeholder="Password (at least 8 characters)";
        const label=bootstrap.labels&&bootstrap.labels[0];if(label)label.textContent="Password";
      }
    }
    const connectorHeading=[...document.querySelectorAll("h2,h3")].find(el=>/Connector Center/i.test(el.textContent||""));
    if(connectorHeading){
      let box=connectorHeading.parentElement;
      for(let i=0;i<3&&box&&box.parentElement;i++,box=box.parentElement){
        if((box.textContent||"").includes("Connector Center")&&box.querySelectorAll("*").length>1)break;
      }
      if(box&&!box.querySelector(".sp-connector-help")){
        const note=document.createElement("p");note.className="sp-connector-help sp-muted";note.textContent="No connector is required to explore the demo. Live farm data is only available after a supported source is configured; unknown or stale data is not treated as verified.";box.appendChild(note);
      }
    }
    const inline=document.getElementById("sp-open-account-inline");
    if(inline)inline.onclick=()=>modal.classList.add("show");
  }

  function mount(){
    if(!document.body || document.getElementById("synpora-nav")) return;
    const main=document.querySelector("main")||document.body.firstElementChild;
    const account=document.createElement("div");
    account.id="sp-account";
    const saved=JSON.parse(localStorage.getItem("synpora_account")||"null");
    const logoutBtn=document.getElementById("sp-logout"); if(logoutBtn) logoutBtn.style.display=saved?"inline-block":"none";
    account.innerHTML=saved
      ? '<span class="sp-mini">'+escapeHtml(saved.farm||"Demo Farm") +'</span><button id="sp-account-btn">Workspace</button>'
      : '<button id="sp-account-btn">Pilot Login</button>';
    nav.appendChild(account);
    if(main && main.parentNode) main.parentNode.insertBefore(nav,main);
    else document.body.insertBefore(nav,document.body.firstChild);
    if(main && main.parentNode) main.parentNode.insertBefore(panel,main);
    else document.body.insertBefore(panel,nav.nextSibling);

    polishBaseUI();
    const demo=[...document.querySelectorAll("button,a")].find(el=>/Demo Farm laden|Load demo farm/i.test(el.textContent||""));
    if(demo) demo.addEventListener("click",()=>{
      localStorage.setItem("synpora_demo_farm","1");
      toast("Demo workspace loaded. Sample values are illustrative, not live telemetry.");
      panel.classList.add("show");
      panel.scrollIntoView({behavior:"smooth",block:"start"});
    });
    document.getElementById("sp-close").onclick=()=>panel.classList.remove("show");
    document.getElementById("sp-account-btn").onclick=()=>{document.getElementById("sp-auth-status").textContent="";modal.classList.add("show");};
    document.getElementById("sp-cancel").onclick=()=>modal.classList.remove("show");
    document.getElementById("sp-logout").onclick=()=>{localStorage.removeItem("synpora_account");modal.classList.remove("show");location.reload()};
    document.getElementById("sp-enter").onclick=async()=>{
      const email=document.getElementById("sp-email").value.trim();
      const farm=document.getElementById("sp-farm").value.trim()||"Demo Farm";
      const password=document.getElementById("sp-password").value;
      const status=document.getElementById("sp-auth-status");
      const submit=document.getElementById("sp-enter");
      if(!email||password.length<8){status.textContent="Enter a valid email address and a password with at least 8 characters.";return}
      submit.disabled=true; submit.textContent="Please wait…"; status.textContent="Checking your account…";
      try{
        let res=await fetch("/api/v1/auth/register",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,password})});
        let data=await res.json().catch(()=>({detail:"The server returned an unexpected response."}));
        if(!res.ok && res.status===409){
          res=await fetch("/api/v1/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,password})});
          data=await res.json().catch(()=>({detail:"The server returned an unexpected response."}));
        }
        if(!res.ok){
          const detail=String(data.detail||"Could not open the workspace.");
          if(res.status===403 && /verification/i.test(detail)) throw new Error("Please verify your email using the link we sent, then sign in again.");
          if(res.status===503 && /email delivery/i.test(detail)) throw new Error("Sign-up is temporarily unavailable because email verification is not configured. Please contact the SYNPORA team.");
          if(res.status===429) throw new Error("Too many attempts. Please wait before trying again.");
          throw new Error(detail);
        }
        if(data.verification_required || !data.token){
          status.textContent="Account created. Check your email for the verification link, then return here and sign in. If you do not see it, check your spam folder.";
          toast("Check your email to verify your account");
          return;
        }
        localStorage.setItem("synpora_account",JSON.stringify({email,farm,token:data.token,user:data.user}));
        modal.classList.remove("show");
        toast("Workspace connected");
        const label=account.querySelector(".sp-mini");
        if(label) label.textContent=farm;
        else account.insertAdjacentHTML("afterbegin",'<span class="sp-mini">'+escapeHtml(farm)+'</span>');
        document.getElementById("sp-account-btn").textContent="Workspace";
        panel.classList.add("show");
        panel.scrollIntoView({behavior:"smooth",block:"start"});
      }catch(e){status.textContent=e.message;toast(e.message)}
      finally{submit.disabled=false;submit.textContent="Continue"}
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
