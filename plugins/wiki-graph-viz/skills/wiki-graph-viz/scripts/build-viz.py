#!/usr/bin/env python3
"""build-viz.py — render a wiki knowledge graph as a self-contained interactive
HTML map for navigation.

Reads the JSON-LD produced by the wiki-kg plugin (`graph.jsonld`), derives typed
nodes and directed typed edges, and writes one self-contained HTML file: an SVG
force-directed graph colored by note type, sized by degree, with a type filter,
an edge-predicate legend with toggles, a search box, drag, zoom/pan, and
click-a-node to open its GitHub wiki page.

Pure standard library (no venv, no third-party deps).

Usage:
  build-viz.py --graph PATH [--repo OWNER/REPO | --wiki-base URL]
               [--out FILE] [--title TITLE]
"""
import argparse
import html
import json
import sys
from pathlib import Path

# Node keys that are NOT graph edges.
NON_EDGE_KEYS = {"@id", "@type", "type", "title", "tags", "@context"}


def _short(t):
    if isinstance(t, list):
        t = t[0] if t else ""
    return str(t).split(":")[-1].split("/")[-1].split("#")[-1] or "Note"


def _refs(v):
    out = []
    if isinstance(v, dict) and "@id" in v:
        out.append(v["@id"])
    elif isinstance(v, list):
        for it in v:
            if isinstance(it, dict) and "@id" in it:
                out.append(it["@id"])
    return out


def load_graph(path):
    d = json.load(open(path))
    raw = d.get("@graph", d) if isinstance(d, dict) else d
    nodes, edges = {}, []
    for n in raw:
        nid = n.get("@id")
        if not nid:
            continue
        tags = n.get("tags")
        tags = tags if isinstance(tags, list) else ([tags] if tags else [])
        nodes[nid] = {
            "id": nid,
            "type": _short(n.get("type") or n.get("@type") or "Note"),
            "title": n.get("title") or nid,
            "tags": [str(t) for t in tags],
        }
    for n in raw:
        nid = n.get("@id")
        if not nid:
            continue
        for k, v in n.items():
            if k in NON_EDGE_KEYS:
                continue
            for tgt in _refs(v):
                edges.append({"source": nid, "target": tgt, "pred": _short(k)})
    # stub nodes for unresolved (dangling) targets
    for e in edges:
        if e["target"] not in nodes:
            nodes[e["target"]] = {"id": e["target"], "type": "Unresolved",
                                  "title": e["target"], "tags": []}
    return list(nodes.values()), edges


def build_url(page, repo, wiki_base):
    if wiki_base:
        return wiki_base.rstrip("/") + "/" + page
    if repo:
        return "https://github.com/%s/wiki/%s" % (repo, page)
    return ""


def main():
    ap = argparse.ArgumentParser(description="Render a wiki graph as interactive HTML.")
    ap.add_argument("--graph", required=True, help="path to wiki-kg graph.jsonld")
    ap.add_argument("--repo", default="", help="owner/repo, for GitHub wiki URLs")
    ap.add_argument("--wiki-base", default="", help="explicit wiki base URL (overrides --repo)")
    ap.add_argument("--out", default="graph.html", help="output HTML file")
    ap.add_argument("--title", default="Wiki graph", help="page title")
    a = ap.parse_args()

    gpath = Path(a.graph)
    if not gpath.exists():
        sys.exit("graph not found: %s (run wiki-kg build-graph.sh first)" % gpath)

    nodes, edges = load_graph(str(gpath))
    for n in nodes:
        n["url"] = build_url(n["id"], a.repo, a.wiki_base)
    deg = {n["id"]: 0 for n in nodes}
    for e in edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    for n in nodes:
        n["degree"] = deg.get(n["id"], 0)

    data = {"nodes": nodes, "edges": edges}
    out = (TEMPLATE
           .replace("__TITLE__", html.escape(a.title))
           .replace("__DATA__", json.dumps(data)))
    Path(a.out).write_text(out)
    print("wrote %s: %d nodes, %d edges" % (a.out, len(nodes), len(edges)), file=sys.stderr)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{ --bg:#f7f8fa; --ink:#1a2233; --muted:#5a6576; --line:#d5dbe6; --panel:#ffffff; }
  @media (prefers-color-scheme: dark){ :root{ --bg:#12151c; --ink:#e6ebf2; --muted:#98a3b3; --line:#2a3140; --panel:#1a1f29; } }
  html,body{ margin:0; height:100%; background:var(--bg); color:var(--ink);
             font:14px/1.4 "Helvetica Neue",Arial,system-ui,sans-serif; }
  #app{ display:flex; height:100vh; }
  #side{ width:230px; flex:0 0 230px; padding:14px; overflow:auto; border-right:1px solid var(--line); background:var(--panel); }
  #side h1{ font-size:15px; margin:0 0 2px; }
  #side .sub{ color:var(--muted); font-size:12px; margin:0 0 12px; }
  #side h2{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:14px 0 6px; }
  #search{ width:100%; box-sizing:border-box; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--ink); }
  .row{ display:flex; align-items:center; gap:6px; padding:2px 0; font-size:12.5px; cursor:pointer; user-select:none; }
  .row input{ margin:0; } .sw{ width:11px; height:11px; border-radius:3px; flex:0 0 11px; }
  #view{ flex:1; position:relative; overflow:hidden; }
  svg{ width:100%; height:100%; display:block; cursor:grab; }
  svg.pan{ cursor:grabbing; }
  .edge{ stroke-opacity:.55; }
  .node circle{ cursor:pointer; stroke:var(--panel); stroke-width:1.5; }
  .node text{ font-size:10px; fill:var(--ink); pointer-events:none; paint-order:stroke; stroke:var(--panel); stroke-width:3px; }
  .dim{ opacity:.12; }
  #tip{ position:absolute; pointer-events:none; background:var(--panel); border:1px solid var(--line); border-radius:6px;
        padding:6px 8px; font-size:12px; max-width:240px; box-shadow:0 4px 14px rgba(0,0,0,.18); display:none; }
  #tip b{ display:block; } #tip .t{ color:var(--muted); }
  button{ font:inherit; padding:5px 9px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--ink); cursor:pointer; }
</style></head>
<body><div id="app">
  <div id="side">
    <h1>__TITLE__</h1>
    <p class="sub"><span id="ncount"></span> pages, <span id="ecount"></span> links</p>
    <input id="search" placeholder="Search pages...">
    <h2>Types</h2><div id="types"></div>
    <h2>Link types</h2><div id="preds"></div>
    <h2>Layout</h2><button id="restart">Restart layout</button>
  </div>
  <div id="view"><svg></svg><div id="tip"></div></div>
</div>
<script>
const DATA = __DATA__;
const PALETTE = ["#3d7ff2","#12a594","#e8622a","#7b3fa0","#c9971b","#2c7a54","#c0447a","#4a5568","#2196f3","#8a6d3b"];
const EDGE_PAL = ["#5a6576","#3d7ff2","#12a594","#e8622a","#7b3fa0","#c9971b","#c0447a","#2c7a54"];
const svg = document.querySelector("svg"), view = document.getElementById("view"), tip = document.getElementById("tip");
const NS = "http://www.w3.org/2000/svg";
const nodes = DATA.nodes, edges = DATA.edges;
const byId = {}; nodes.forEach(n => byId[n.id] = n);
document.getElementById("ncount").textContent = nodes.length;
document.getElementById("ecount").textContent = edges.length;

// colour maps
const types = [...new Set(nodes.map(n => n.type))].sort();
const preds = [...new Set(edges.map(e => e.pred))].sort();
const typeColor = {}; types.forEach((t,i)=> typeColor[t]=PALETTE[i%PALETTE.length]);
const predColor = {}; preds.forEach((p,i)=> predColor[p]=EDGE_PAL[i%EDGE_PAL.length]);
const typeOn = {}; types.forEach(t=> typeOn[t]=true);
const predOn = {}; preds.forEach(p=> predOn[p]=true);

// arrow markers per predicate
const defs = document.createElementNS(NS,"defs");
preds.forEach(p=>{
  const m = document.createElementNS(NS,"marker");
  m.setAttribute("id","arw-"+p); m.setAttribute("viewBox","0 0 10 10"); m.setAttribute("refX","18");
  m.setAttribute("refY","5"); m.setAttribute("markerWidth","6"); m.setAttribute("markerHeight","6"); m.setAttribute("orient","auto-start-reverse");
  const pa = document.createElementNS(NS,"path"); pa.setAttribute("d","M0,0 L10,5 L0,10 z"); pa.setAttribute("fill",predColor[p]);
  m.appendChild(pa); defs.appendChild(m);
});
svg.appendChild(defs);
const root = document.createElementNS(NS,"g"); svg.appendChild(root);
const gEdges = document.createElementNS(NS,"g"); const gNodes = document.createElementNS(NS,"g");
root.appendChild(gEdges); root.appendChild(gNodes);

// build legend rows
function legend(el, items, colorMap, onMap){
  items.forEach(k=>{
    const row=document.createElement("label"); row.className="row";
    const cb=document.createElement("input"); cb.type="checkbox"; cb.checked=true;
    const sw=document.createElement("span"); sw.className="sw"; sw.style.background=colorMap[k];
    const tx=document.createElement("span"); tx.textContent=k;
    cb.onchange=()=>{ onMap[k]=cb.checked; refresh(); };
    row.append(cb,sw,tx); el.appendChild(row);
  });
}
legend(document.getElementById("types"), types, typeColor, typeOn);
legend(document.getElementById("preds"), preds, predColor, predOn);

// init positions
let W = view.clientWidth, H = view.clientHeight;
nodes.forEach((n,i)=>{ const a=i/nodes.length*6.283; n.x=W/2+Math.cos(a)*Math.min(W,H)*0.3; n.y=H/2+Math.sin(a)*Math.min(W,H)*0.3; n.vx=0; n.vy=0; });

// svg elements
const eEls = edges.map(e=>{
  const l=document.createElementNS(NS,"line"); l.setAttribute("class","edge");
  l.setAttribute("stroke",predColor[e.pred]); l.setAttribute("stroke-width","1.4");
  l.setAttribute("marker-end","url(#arw-"+e.pred+")"); gEdges.appendChild(l); return l;
});
const nEls = nodes.map(n=>{
  const g=document.createElementNS(NS,"g"); g.setAttribute("class","node");
  const c=document.createElementNS(NS,"circle"); c.setAttribute("r", 5+Math.sqrt(n.degree)*3);
  c.setAttribute("fill", typeColor[n.type]||"#888");
  const t=document.createElementNS(NS,"text"); t.setAttribute("x", 8+Math.sqrt(n.degree)*3); t.setAttribute("y",4); t.textContent=n.title;
  g.append(c,t); gNodes.appendChild(g);
  g.addEventListener("mousemove", ev=>showTip(ev,n));
  g.addEventListener("mouseleave", ()=> tip.style.display="none");
  c.addEventListener("click", ()=>{ if(n.url) window.open(n.url,"_blank"); });
  c.addEventListener("mousedown", ev=>startDrag(ev,n));
  return g;
});

function showTip(ev,n){
  tip.style.display="block";
  tip.innerHTML="<b>"+esc(n.title)+"</b><span class='t'>"+esc(n.type)+" &middot; deg "+n.degree+(n.tags.length?" &middot; "+n.tags.map(esc).join(", "):"")+"</span>"+(n.url?"<br><span class='t'>click to open</span>":"");
  const r=view.getBoundingClientRect();
  tip.style.left=(ev.clientX-r.left+12)+"px"; tip.style.top=(ev.clientY-r.top+12)+"px";
}
function esc(s){ return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

// visibility filter
let q="";
document.getElementById("search").addEventListener("input", e=>{ q=e.target.value.toLowerCase(); refresh(); });
function nodeVisible(n){ return typeOn[n.type]; }
function refresh(){
  nEls.forEach((g,i)=>{
    const n=nodes[i]; const vis=nodeVisible(n);
    g.style.display = vis?"":"none";
    const match = !q || n.title.toLowerCase().includes(q) || n.id.toLowerCase().includes(q);
    g.classList.toggle("dim", !match);
  });
  eEls.forEach((l,i)=>{
    const e=edges[i]; const vis=predOn[e.pred] && nodeVisible(byId[e.source]) && nodeVisible(byId[e.target]);
    l.style.display=vis?"":"none";
  });
}

// force simulation
let alpha=1, dragN=null;
function tick(){
  alpha*=0.992; if(alpha<0.02 && !dragN) alpha=0.02;
  const K=-2600, SPR=0.02, LEN=90, G=0.02;
  for(let i=0;i<nodes.length;i++){ const a=nodes[i]; if(!nodeVisible(a))continue;
    for(let j=i+1;j<nodes.length;j++){ const b=nodes[j]; if(!nodeVisible(b))continue;
      let dx=a.x-b.x, dy=a.y-b.y, d2=dx*dx+dy*dy+0.01, d=Math.sqrt(d2);
      const f=K/d2; const fx=dx/d*f, fy=dy/d*f;
      a.vx+=fx*alpha; a.vy+=fy*alpha; b.vx-=fx*alpha; b.vy-=fy*alpha;
    }
    a.vx += (W/2-a.x)*G*alpha; a.vy += (H/2-a.y)*G*alpha;
  }
  edges.forEach(e=>{ const a=byId[e.source], b=byId[e.target]; if(!a||!b)return;
    let dx=b.x-a.x, dy=b.y-a.y, d=Math.sqrt(dx*dx+dy*dy)+0.01, f=(d-LEN)*SPR;
    const fx=dx/d*f, fy=dy/d*f; a.vx+=fx*alpha; a.vy+=fy*alpha; b.vx-=fx*alpha; b.vy-=fy*alpha;
  });
  nodes.forEach(n=>{ if(n===dragN)return; n.x+=n.vx*=0.85; n.y+=n.vy*=0.85; });
  nEls.forEach((g,i)=>{ const n=nodes[i]; g.setAttribute("transform","translate("+n.x+","+n.y+")"); });
  eEls.forEach((l,i)=>{ const e=edges[i], a=byId[e.source], b=byId[e.target]; if(!a||!b)return;
    l.setAttribute("x1",a.x); l.setAttribute("y1",a.y); l.setAttribute("x2",b.x); l.setAttribute("y2",b.y); });
  requestAnimationFrame(tick);
}
document.getElementById("restart").onclick=()=>{ alpha=1; };

// drag nodes
function startDrag(ev,n){ ev.stopPropagation(); dragN=n; alpha=Math.max(alpha,0.3);
  const move=e=>{ const p=toGraph(e); n.x=p.x; n.y=p.y; n.vx=n.vy=0; };
  const up=()=>{ dragN=null; window.removeEventListener("mousemove",move); window.removeEventListener("mouseup",up); };
  window.addEventListener("mousemove",move); window.addEventListener("mouseup",up);
}
// zoom / pan
let tx=0, ty=0, sc=1;
function apply(){ root.setAttribute("transform","translate("+tx+","+ty+") scale("+sc+")"); }
function toGraph(e){ const r=svg.getBoundingClientRect(); return { x:(e.clientX-r.left-tx)/sc, y:(e.clientY-r.top-ty)/sc }; }
svg.addEventListener("wheel", e=>{ e.preventDefault(); const r=svg.getBoundingClientRect();
  const mx=e.clientX-r.left, my=e.clientY-r.top, k=e.deltaY<0?1.1:0.9;
  tx=mx-(mx-tx)*k; ty=my-(my-ty)*k; sc*=k; apply(); }, {passive:false});
let panning=false, px,py;
svg.addEventListener("mousedown", e=>{ if(e.target.closest(".node"))return; panning=true; px=e.clientX; py=e.clientY; svg.classList.add("pan"); });
window.addEventListener("mousemove", e=>{ if(!panning)return; tx+=e.clientX-px; ty+=e.clientY-py; px=e.clientX; py=e.clientY; apply(); });
window.addEventListener("mouseup", ()=>{ panning=false; svg.classList.remove("pan"); });
window.addEventListener("resize", ()=>{ W=view.clientWidth; H=view.clientHeight; });

refresh(); apply(); tick();
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
