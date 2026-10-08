from pathlib import Path
import re

ROOT = Path("/app/frontend")
MARKER = "/* SYNPORA_ENHANCEMENT_V1 */"

SCRIPT = r"""
<script>
/* SYNPORA_ENHANCEMENT_V1 */
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
    @media(max-width:700px){#synpora-nav{padding:0 12px}.sp-grid{grid-template-columns:1fr}}
  `;
  document.head.insertAdjacentHTML("beforeend","<style>"+css+"</style>");

  const toast = (msg) => {
    let t=document.getElementById("synpora-toast");
    if(!t){t=document.createElement("div");t.id="synpora-toast";document.body.appendChild(t)}
    t.textContent=msg;t.classList.add("show");clearTimeout(window.__spToast);
    window.__spToast=setTimeout(()=>t.classList.remove("show"),2600);
  };

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
      const titles={Assets:"Asset Registry",Economics:"Economics & Value per kWh","AI Decision":"AI Decision Center",Simulation:"What-if Simulation Lab",Reports:"Reports & Audit Trail"};
      panel.querySelector("h2").textContent=titles[name]||"SYNPORA Workspace";
      toast(name+" workspace geöffnet");
      panel.scrollIntoView({behavior:"smooth",block:"start"});
    };
    nav.appendChild(b);
  });

  function mount(){
    if(!document.body || document.getElementById("synpora-nav")) return;
    const main=document.querySelector("main")||document.body.firstElementChild;
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
    document.getElementById("sp-sim").onclick=()=>toast("Simulation vorbereitet · nächste Stufe: echte Tarif- und Asset-Daten");
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
