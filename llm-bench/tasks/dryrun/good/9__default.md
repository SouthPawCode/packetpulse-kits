```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Network map</title>
<style>
body{margin:0;background:#0f1519;color:#e3e9ed;font-family:system-ui,sans-serif}
svg{width:100%;height:80vh;display:block} .legend{padding:8px 16px;display:flex;gap:16px;flex-wrap:wrap;font-size:14px}
.dot{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:6px}
#tip{position:fixed;background:#172027;border:1px solid #4fc2ba;padding:8px 10px;border-radius:6px;font-size:13px;display:none;pointer-events:none}
text{fill:#e3e9ed;font-size:12px;text-anchor:middle} line{stroke:#4fc2ba;stroke-width:1.5;opacity:.7}
</style></head><body>
<div class="legend" id="legend"></div>
<svg id="map" viewBox="0 0 1200 560"></svg><div id="tip"></div>
<script>
const topo = {"gateway":{"name":"udm-pro","ip":"192.168.1.1"},"switches":[{"name":"sw-core","ip":"192.168.1.2","uplink":"udm-pro"},{"name":"sw-lab","ip":"192.168.1.3","uplink":"sw-core"}],
"vlans":[{"id":10,"name":"Trusted","cidr":"192.168.10.0/24"},{"id":20,"name":"IoT","cidr":"192.168.20.0/24"},{"id":40,"name":"Servers","cidr":"192.168.40.0/24"}],
"hosts":[{"name":"pve1","ip":"192.168.40.11","vlan":40,"switch":"sw-lab","port":1,"status":"up"},{"name":"pve2","ip":"192.168.40.12","vlan":40,"switch":"sw-lab","port":2,"status":"degraded"},{"name":"pve3","ip":"192.168.40.13","vlan":40,"switch":"sw-lab","port":3,"status":"up"},{"name":"ai470","ip":"192.168.40.30","vlan":40,"switch":"sw-lab","port":4,"status":"up"},{"name":"nas01","ip":"192.168.40.21","vlan":40,"switch":"sw-core","port":12,"status":"up"},{"name":"desktop-mike","ip":"192.168.10.21","vlan":10,"switch":"sw-core","port":3,"status":"up"},{"name":"laptop-sam","ip":"192.168.10.34","vlan":10,"switch":"sw-core","port":4,"status":"up"},{"name":"ha-green","ip":"192.168.20.5","vlan":20,"switch":"sw-core","port":8,"status":"up"},{"name":"cam-front","ip":"192.168.20.41","vlan":20,"switch":"sw-core","port":9,"status":"up"},{"name":"cam-garage","ip":"192.168.20.42","vlan":20,"switch":"sw-core","port":10,"status":"down"},{"name":"printer01","ip":"192.168.20.60","vlan":20,"switch":"sw-core","port":15,"status":"up"},{"name":"ap-living","ip":"192.168.10.2","vlan":10,"switch":"sw-core","port":6,"status":"up"}]};
const colors={10:"#4fc2ba",20:"#e0a93b",40:"#9b8cff"}, ring={up:"none",degraded:"#e0a93b",down:"#e2554a"};
const svg=document.getElementById("map"), ns="http://www.w3.org/2000/svg", tip=document.getElementById("tip");
const pos={}; pos[topo.gateway.name]=[600,50]; topo.switches.forEach((s,i)=>pos[s.name]=[300+i*600,190]);
const add=(t,a,p=svg)=>{const e=document.createElementNS(ns,t);for(const k in a)e.setAttribute(k,a[k]);p.appendChild(e);return e};
topo.switches.forEach(s=>{const a=pos[s.uplink],b=pos[s.name];add("line",{x1:a[0],y1:a[1],x2:b[0],y2:b[1]})});
topo.switches.forEach(sw=>{const hs=topo.hosts.filter(h=>h.switch===sw.name);hs.forEach((h,i)=>{
 const x=sw.name==="sw-core"?60+i*(520/Math.max(hs.length-1,1)):660+i*(480/Math.max(hs.length-1,1)),y=400;pos[h.name]=[x,y];
 add("line",{x1:pos[sw.name][0],y1:pos[sw.name][1],x2:x,y2:y});});});
add("rect",{x:pos["udm-pro"][0]-50,y:30,width:100,height:40,rx:6,fill:"#0e6b67"});add("text",{x:600,y:55},svg).textContent="udm-pro";
topo.switches.forEach(s=>{const p=pos[s.name];add("rect",{x:p[0]-45,y:p[1]-18,width:90,height:36,rx:6,fill:"#172027",stroke:"#4fc2ba"});add("text",{x:p[0],y:p[1]+4}).textContent=s.name});
topo.hosts.forEach(h=>{const p=pos[h.name];const g=add("g",{});
 add("circle",{cx:p[0],cy:p[1],r:14,fill:colors[h.vlan],stroke:ring[h.status],"stroke-width":4},g);add("text",{x:p[0],y:p[1]+34},g).textContent=h.name;
 if(h.status!=="up")add("text",{x:p[0],y:p[1]+48},g).textContent=h.status;
 const show=e=>{tip.style.display="block";tip.style.left=e.clientX+12+"px";tip.style.top=e.clientY+12+"px";tip.innerHTML=`<b>${h.name}</b><br>${h.ip}<br>VLAN ${h.vlan}<br>${h.switch} port ${h.port}<br>status: ${h.status}`};
 g.addEventListener("mousemove",show);g.addEventListener("click",show);g.addEventListener("mouseleave",()=>tip.style.display="none");});
document.getElementById("legend").innerHTML=topo.vlans.map(v=>`<span><span class="dot" style="background:${colors[v.id]}"></span>${v.name} (VLAN ${v.id}, ${v.cidr})</span>`).join("");
</script></body></html>
```
