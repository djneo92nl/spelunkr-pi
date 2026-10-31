"""Web remote + JSON API for the TV client. Standard library only.

    GET  /                       phone-friendly remote page
    GET  /api/status             what is on now (channel, show, offset, next, volume ...)
    GET  /api/channels           channel list
    POST /api/channel/<dial|id>  tune, e.g. /api/channel/203 or /api/channel/ray-gun
    POST /api/key/<name>         up down last info guide fullscreen mute volup voldown left right enter captions diag help testcard
    POST /api/volume/<up|down|mute>

Safety: POSTs need a custom header (X-Remote: 1, or X-Token when a token is configured), which browsers will not send
cross-site, so another website cannot make your TV change channel. Set "web.token" in config.json to also require a secret
for everything (pass it as ?token=... in the page URL, or in the X-Token header). Anyone who can reach the port and has the
token can change the channel. Do not expose it to the internet.
"""
import json
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYS = {"up", "down", "last", "info", "guide", "fullscreen", "mute", "volup", "voldown", "left", "right", "enter",
        "captions", "diag", "help", "testcard"}

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1020">
<title>spelunkr.tv remote</title>
<style>
:root{--bg:#0b1020;--card:#151c33;--ink:#eef2ff;--dim:#9aa6c8;--line:#26304f;--accent:#ffd54a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.35 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  padding:max(16px,env(safe-area-inset-top)) 16px max(16px,env(safe-area-inset-bottom));max-width:640px;margin-inline:auto}
h1{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin:0 0 12px;font-weight:600}
.now{background:var(--card);border:1px solid var(--line);border-left:6px solid var(--ch,#4caf50);border-radius:12px;padding:14px 16px}
.now .ch{font-size:13px;color:var(--dim);letter-spacing:.08em}
.now .title{font-size:20px;font-weight:650;margin:2px 0 8px;overflow-wrap:anywhere}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--ch,#4caf50);width:0}
.meta{display:flex;justify-content:space-between;font-size:13px;color:var(--dim);margin-top:6px}
.next{font-size:13px;color:var(--dim);margin-top:8px}
.row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0}
button{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 8px;
  min-height:48px;cursor:pointer}
button:active{background:var(--line)}
button.big{font-size:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.grid button{display:flex;gap:8px;align-items:center;text-align:left;padding:10px 12px;min-height:44px;font-size:14px}
.grid b{min-width:2.4em;color:var(--c,#4caf50);font-variant-numeric:tabular-nums}
.grid button.on{border-color:var(--accent)}
#err{color:#ff8a80;font-size:13px;min-height:1.2em;margin-top:6px}
</style></head><body>
<h1>spelunkr.tv remote</h1>
<div class="now" id="now"><div class="ch" id="ch">connecting…</div><div class="title" id="title"></div>
  <div class="bar"><i id="fill"></i></div><div class="meta"><span id="off"></span><span id="rem"></span></div>
  <div class="next" id="next"></div></div>
<div class="row"><button class="big" data-k="down" aria-label="Channel down">CH −</button>
  <button data-k="last">Last</button><button class="big" data-k="up" aria-label="Channel up">CH +</button></div>
<div class="row"><button data-k="voldown">Vol −</button><button data-k="mute" id="mute">Mute</button><button data-k="volup">Vol +</button></div>
<div class="row"><button data-k="guide">Guide</button><button data-k="info">Info</button><button data-k="fullscreen">Fullscreen</button></div>
<div id="err"></div>
<h1 style="margin-top:18px">Channels</h1><div class="grid" id="chans"></div>
<script>
const TOKEN=__TOKEN__;
const H={'X-Remote':'1'};if(TOKEN)H['X-Token']=TOKEN;
const q=TOKEN?('?token='+encodeURIComponent(TOKEN)):'';
const $=id=>document.getElementById(id);
const lift=h=>{h=(h||'#4caf50').replace('#','');const c=[0,2,4].map(i=>parseInt(h.substr(i,2),16)),l=.299*c[0]+.587*c[1]+.114*c[2];
  if(l>=120)return '#'+h;const k=(120-l)/(255-l);return 'rgb('+c.map(v=>Math.round(v+(255-v)*k)).join(',')+')'};
const fmt=s=>{s=Math.max(0,Math.round(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),r=s%60;
  return (h?h+':'+String(m).padStart(2,'0'):m)+':'+String(r).padStart(2,'0')};
const clock=ms=>new Date(ms).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
async function post(p){try{const r=await fetch('/api/'+p,{method:'POST',headers:H});if(!r.ok)throw new Error(r.status);
  $('err').textContent='';setTimeout(poll,300)}catch(e){$('err').textContent='Could not reach the TV ('+e.message+')'}}
document.querySelectorAll('[data-k]').forEach(b=>b.onclick=()=>post('key/'+b.dataset.k));
async function poll(){try{const r=await fetch('/api/status'+q,{headers:H});if(!r.ok)throw new Error(r.status);const s=await r.json();
  $('err').textContent='';const c=s.channel||{};document.documentElement.style.setProperty('--ch',lift(c.color));
  $('ch').textContent='CH '+c.dial+' · '+(c.name||'')+(s.state&&s.state!=='playing'?' · '+s.state:'');
  $('title').textContent=s.title||(s.loading?'Loading…':s.state==='commercial'?'Commercial break':s.state==='break'?'Station break':s.error?'Trouble: '+s.error:'');
  const d=s.duration_s;$('fill').style.width=d?Math.min(100,100*s.offset_s/d)+'%':'0';
  $('off').textContent=d?fmt(s.offset_s):'';$('rem').textContent=d?'-'+fmt(s.remaining_s):'';
  $('next').textContent=s.next?('Next '+clock(s.next.start_ms)+' · '+s.next.title):'';
  $('mute').textContent=s.muted?'Unmute':'Mute';
  document.querySelectorAll('#chans button').forEach(b=>b.classList.toggle('on',b.dataset.id===c.id));
 }catch(e){$('err').textContent='Lost contact with the TV ('+e.message+')'}}
async function init(){try{const r=await fetch('/api/channels'+q,{headers:H});const list=await r.json();const g=$('chans');
  list.forEach(c=>{const b=document.createElement('button');b.dataset.id=c.id;b.style.setProperty('--c',lift(c.color));
    b.innerHTML='<b></b><span></span>';b.firstChild.textContent=c.dial;b.lastChild.textContent=c.name;
    b.onclick=()=>post('channel/'+c.id);g.appendChild(b)})}catch(e){$('err').textContent='Could not load channels'}
  poll();setInterval(poll,2000)}
init();
</script></body></html>
"""


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))        # no packet is sent; this just picks the outgoing interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def start(tv, host="0.0.0.0", port=8080, token=""):
    """Start the server in a background thread. `tv` needs .snapshot, .chans and .send(cmd)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "spelunkr-remote"

        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self, query):
            if not token:
                return True
            given = self.headers.get("X-Token") or query.get("token", [""])[0]
            return given == token

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(u.query)
            if not self._authorized(query):
                return self._json({"ok": False, "error": "token required"}, 401)
            if u.path == "/":
                body = PAGE.replace("__TOKEN__", json.dumps(token)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/status":
                self._json(dict(tv.snapshot))
            elif u.path == "/api/channels":
                self._json([{"dial": c["dial"], "id": c["id"], "name": c["name"], "color": c.get("color")}
                            for c in tv.chans])
            else:
                self._json({"ok": False, "error": "not found"}, 404)

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(u.query)
            if not self._authorized(query):
                return self._json({"ok": False, "error": "token required"}, 401)
            if not (self.headers.get("X-Token") or self.headers.get("X-Remote")):
                return self._json({"ok": False, "error": "send header X-Remote: 1"}, 403)
            parts = [urllib.parse.unquote(p) for p in u.path.strip("/").split("/")]
            if len(parts) == 3 and parts[:2] == ["api", "channel"]:
                if not any(parts[2] in (str(c["dial"]), c["id"]) for c in tv.chans):
                    return self._json({"ok": False, "error": "unknown channel"}, 404)
                tv.send("channel:" + parts[2])
            elif len(parts) == 3 and parts[:2] == ["api", "key"] and parts[2] in KEYS:
                tv.send(parts[2])
            elif len(parts) == 3 and parts[:2] == ["api", "volume"] and parts[2] in ("up", "down", "mute"):
                tv.send({"up": "volup", "down": "voldown", "mute": "mute"}[parts[2]])
            else:
                return self._json({"ok": False, "error": "not found"}, 404)
            self._json({"ok": True})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="web-remote").start()
    return server
