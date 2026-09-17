#!/usr/bin/env python3
"""build-viz.py — render a wiki knowledge graph as a self-contained interactive
HTML map for navigation.

Reads the JSON-LD produced by the wiki-kg plugin (`graph.jsonld`), derives typed
nodes and directed typed edges, and writes one self-contained HTML file that
renders the graph with Cytoscape.js using the `cose` layout (computed once,
`animate:false`, so it settles at rest, no perpetual physics loop). Nodes are
colored by note type and sized by degree; edges are colored by predicate; a
type filter, edge-type legend, search box, and Re-layout button are provided;
click a node to open its GitHub wiki page.

Cytoscape.js is inlined from `vendor/cytoscape.min.js` so the output is fully
self-contained (offline, no CDN). Pure standard library on the Python side.

Usage:
  build-viz.py --graph PATH [--repo OWNER/REPO | --wiki-base URL]
               [--out FILE] [--title TITLE]
"""
import argparse
import html
import json
import subprocess
import sys
from pathlib import Path

NON_EDGE_KEYS = {"@id", "@type", "type", "title", "tags", "@context"}
HERE = Path(__file__).resolve().parent
CDN = "https://unpkg.com/cytoscape@3/dist/cytoscape.min.js"


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
        nodes[nid] = {"id": nid, "type": _short(n.get("type") or n.get("@type") or "Note"),
                      "title": n.get("title") or nid, "tags": [str(t) for t in tags]}
    for n in raw:
        nid = n.get("@id")
        if not nid:
            continue
        for k, v in n.items():
            if k in NON_EDGE_KEYS:
                continue
            for tgt in _refs(v):
                edges.append({"source": nid, "target": tgt, "pred": _short(k)})
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
    ap = argparse.ArgumentParser(description="Render a wiki graph as interactive HTML (Cytoscape.js).")
    ap.add_argument("--graph", required=True, help="path to wiki-kg graph.jsonld")
    ap.add_argument("--repo", default="", help="owner/repo, for GitHub wiki URLs")
    ap.add_argument("--wiki-base", default="", help="explicit wiki base URL (overrides --repo)")
    ap.add_argument("--out", default="graph.html", help="output HTML file")
    ap.add_argument("--title", default="Wiki graph", help="page title")
    ap.add_argument("--wiki", default="", help="wiki dir; enables Color-by-Recency from git commit dates")
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

    # optional: git last-commit date per page (Color-by-Recency)
    trange = None
    for n in nodes:
        n["ts"] = None
    if a.wiki:
        wdir = Path(a.wiki)
        stamps = []
        for n in nodes:
            f = wdir / (n["id"] + ".md")
            if not f.exists():
                continue
            try:
                r = subprocess.run(["git", "-C", str(wdir), "log", "-1", "--format=%ct",
                                    "--", n["id"] + ".md"],
                                   capture_output=True, text=True, timeout=10)
                v = r.stdout.strip()
                if v:
                    n["ts"] = int(v)
                    stamps.append(n["ts"])
            except Exception:
                pass
        if stamps:
            trange = [min(stamps), max(stamps)]
        else:
            print("note: --wiki given but no git dates found; Recency mode will be unavailable", file=sys.stderr)

    # inline cytoscape.min.js if vendored; else fall back to CDN
    vlib = HERE / "vendor" / "cytoscape.min.js"
    if vlib.exists():
        cyto = "<script>%s</script>" % vlib.read_text()
    else:
        cyto = '<script src="%s"></script>' % CDN
        print("note: vendor/cytoscape.min.js not found; referencing CDN (needs network)", file=sys.stderr)

    data_json = json.dumps({"nodes": nodes, "edges": edges, "trange": trange}).replace("</", "<\\/")
    out = (TEMPLATE
           .replace("__TITLE__", html.escape(a.title))
           .replace("__CYTOSCAPE__", cyto)
           .replace("__DATA__", data_json))
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
  #side h1{ font-size:15px; margin:0 0 2px; } #side .sub{ color:var(--muted); font-size:12px; margin:0 0 12px; }
  #side h2{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:14px 0 6px; }
  #search{ width:100%; box-sizing:border-box; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--ink); }
  .row{ display:flex; align-items:center; gap:6px; padding:2px 0; font-size:12.5px; cursor:pointer; user-select:none; }
  .row input{ margin:0; } .sw{ width:11px; height:11px; border-radius:3px; flex:0 0 11px; }
  #cy{ flex:1; height:100%; background:var(--bg); }
  #tip{ position:absolute; pointer-events:none; background:var(--panel); border:1px solid var(--line); border-radius:6px;
        padding:6px 8px; font-size:12px; max-width:240px; box-shadow:0 4px 14px rgba(0,0,0,.18); display:none; z-index:5; }
  #tip b{ display:block; } #tip .t{ color:var(--muted); }
  button{ font:inherit; padding:5px 9px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--ink); cursor:pointer; }
</style></head>
<body><div id="app">
  <div id="side">
    <h1>__TITLE__</h1>
    <p class="sub"><span id="ncount"></span> pages, <span id="ecount"></span> links</p>
    <input id="search" placeholder="Search pages...">
    <h2>Color by</h2>
    <label class="row"><input type="radio" name="cmode" value="type" checked> Type</label>
    <label class="row" id="recency-row"><input type="radio" name="cmode" value="recency" id="cmode-recency"> Recency (git date)</label>
    <div id="colorbar" style="display:none; margin:4px 0 2px;">
      <div id="cbar" style="height:12px; border-radius:3px; border:1px solid var(--line);"></div>
      <div style="display:flex; justify-content:space-between; color:var(--muted); font-size:11px; margin-top:2px;"><span id="cbold"></span><span id="cbnew"></span></div>
      <div style="color:var(--muted); font-size:10px;">darker = older</div>
    </div>
    <h2>Types</h2><div id="types"></div>
    <h2>Link types</h2><div id="preds"></div>
    <h2>Layout</h2><button id="relayout">Re-layout</button>
  </div>
  <div id="cy"></div><div id="tip"></div>
</div>
__CYTOSCAPE__
<script>
const DATA = __DATA__;
const PALETTE=["#3d7ff2","#12a594","#e8622a","#7b3fa0","#c9971b","#2c7a54","#c0447a","#4a5568","#2196f3","#8a6d3b","#aaaaaa"];
const EDGE_PAL=["#5a6576","#0d4a8b","#1b6e2a","#8b0d3f","#b8860b","#7b3fa0","#c0447a","#2c7a54"];
const nodes=DATA.nodes, edges=DATA.edges;
document.getElementById("ncount").textContent=nodes.length;
document.getElementById("ecount").textContent=edges.length;

const types=[...new Set(nodes.map(n=>n.type))].sort();
const preds=[...new Set(edges.map(e=>e.pred))].sort();
const typeColor={}; types.forEach((t,i)=>typeColor[t]=PALETTE[i%PALETTE.length]);
const predColor={}; preds.forEach((p,i)=>predColor[p]=EDGE_PAL[i%EDGE_PAL.length]);
const typeOn={}; types.forEach(t=>typeOn[t]=true);
const predOn={}; preds.forEach(p=>predOn[p]=true);

const elements=[];
nodes.forEach(n=>elements.push({data:{id:n.id,label:n.title,type:n.type,url:n.url,deg:n.degree,ts:n.ts}}));
edges.forEach((e,i)=>elements.push({data:{id:"e"+i,source:e.source,target:e.target,label:e.pred}}));

const edgeColorStyles = preds.map(p=>({
  selector:'edge[label = "'+p+'"]',
  style:{ 'line-color':predColor[p], 'target-arrow-color':predColor[p], 'color':predColor[p] }
}));

const cy = cytoscape({
  container: document.getElementById('cy'),
  elements,
  wheelSensitivity: 0.25,
  style: [
    { selector:'node', style:{
        'background-color': ele=>typeColor[ele.data('type')]||'#888',
        'label':'data(label)', 'color':getComputedStyle(document.body).getPropertyValue('--ink'),
        'text-outline-color':getComputedStyle(document.body).getPropertyValue('--panel'), 'text-outline-width':2,
        'font-size':10, 'font-family':'Helvetica Neue, Arial, sans-serif',
        'width': ele=>16+Math.sqrt(ele.data('deg'))*7, 'height': ele=>16+Math.sqrt(ele.data('deg'))*7,
        'text-wrap':'wrap', 'text-max-width':90 }},
    { selector:'edge', style:{
        'width':1, 'line-color':'#bbb', 'target-arrow-color':'#bbb', 'target-arrow-shape':'triangle',
        'curve-style':'bezier', 'arrow-scale':0.8, 'label':'data(label)', 'font-size':8,
        'color':'#888', 'text-background-color':getComputedStyle(document.body).getPropertyValue('--panel'),
        'text-background-opacity':0.85, 'text-background-padding':1, 'text-rotation':'autorotate' }},
    ...edgeColorStyles,
    { selector:'.hidden', style:{ 'display':'none' }},
    { selector:'.dim', style:{ 'opacity':0.12 }},
    { selector:'node:selected', style:{ 'border-width':3, 'border-color':'#0d4a8b' }}
  ],
  layout: { name:'cose', idealEdgeLength:110, nodeOverlap:14, nodeRepulsion:8000, animate:false, fit:true, padding:36 }
});

// click a node -> open its wiki page
cy.on('tap','node', evt=>{ const u=evt.target.data('url'); if(u) window.open(u,'_blank'); });

// tooltip
const tip=document.getElementById('tip');
cy.on('mouseover','node', evt=>{ const d=evt.target.data();
  tip.style.display='block';
  tip.innerHTML="<b>"+esc(d.label)+"</b><span class='t'>"+esc(d.type)+" &middot; deg "+d.deg+"</span>"+(d.url?"<br><span class='t'>click to open</span>":""); });
cy.on('mouseout','node', ()=> tip.style.display='none');
cy.on('mousemove', evt=>{ if(tip.style.display==='block'){ tip.style.left=(evt.renderedPosition.x+14)+'px'; tip.style.top=(evt.renderedPosition.y+14)+'px'; }});
function esc(s){ return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

// filters
let q="";
function apply(){
  cy.batch(()=>{
    cy.nodes().forEach(n=>{
      const vis=typeOn[n.data('type')];
      n.toggleClass('hidden', !vis);
      const match=!q || (n.data('label')||'').toLowerCase().includes(q) || n.data('id').toLowerCase().includes(q);
      n.toggleClass('dim', vis && !match);
    });
    cy.edges().forEach(e=>{
      const vis=predOn[e.data('label')] && !e.source().hasClass('hidden') && !e.target().hasClass('hidden');
      e.toggleClass('hidden', !vis);
    });
  });
}
document.getElementById('search').addEventListener('input', e=>{ q=e.target.value.toLowerCase(); apply(); });

function legend(el, items, colorMap, onMap){
  items.forEach(k=>{
    const row=document.createElement('label'); row.className='row';
    const cb=document.createElement('input'); cb.type='checkbox'; cb.checked=true;
    const sw=document.createElement('span'); sw.className='sw'; sw.style.background=colorMap[k];
    const tx=document.createElement('span'); tx.textContent=k;
    cb.onchange=()=>{ onMap[k]=cb.checked; apply(); };
    row.append(cb,sw,tx); el.appendChild(row);
  });
}
legend(document.getElementById('types'), types, typeColor, typeOn);
legend(document.getElementById('preds'), preds, predColor, predOn);

// color-by-recency (git dates)
const trange = DATA.trange;   // [minTs, maxTs] unix seconds, or null
function recencyColor(ts){
  if(ts==null || !trange) return '#9aa3af';
  const t0=trange[0], t1=trange[1], norm = t1>t0 ? (ts-t0)/(t1-t0) : 1;
  return 'hsl(210,60%,'+Math.round(26+norm*52)+'%)';   // old -> dark, recent -> light
}
function setMode(m){
  cy.batch(()=>{ cy.nodes().forEach(n=>{
    n.style('background-color', m==='recency' ? recencyColor(n.data('ts')) : (typeColor[n.data('type')]||'#888'));
  });});
  document.getElementById('colorbar').style.display = (m==='recency') ? '' : 'none';
}
if(trange){
  const stops=[]; for(let i=0;i<=10;i++){ stops.push('hsl(210,60%,'+(26+(i/10)*52)+'%) '+(i*10)+'%'); }
  document.getElementById('cbar').style.background='linear-gradient(to right,'+stops.join(',')+')';
  const fmt=t=> new Date(t*1000).toISOString().slice(0,10);
  document.getElementById('cbold').textContent=fmt(trange[0]);
  document.getElementById('cbnew').textContent=fmt(trange[1]);
} else {
  const rr=document.getElementById('cmode-recency');
  if(rr){ rr.disabled=true; document.getElementById('recency-row').style.opacity=0.4;
          document.getElementById('recency-row').title='rebuild with --wiki <path> to enable'; }
}
document.querySelectorAll('input[name="cmode"]').forEach(r=> r.addEventListener('change', e=>{ if(e.target.checked) setMode(e.target.value); }));

document.getElementById('relayout').onclick=()=>{
  cy.layout({ name:'cose', idealEdgeLength:110, nodeOverlap:14, nodeRepulsion:8000, animate:false, fit:true, padding:36 }).run();
};
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
