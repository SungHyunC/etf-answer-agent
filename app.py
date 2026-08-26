"""웹 데모 UI — 표준 라이브러리만 사용 (별도 설치 불필요).

    python app.py        → http://localhost:8000

처리 경로(5단계)와 근거를 함께 노출해, 답변이 어떤 창고에서 어떤 근거로
생성되었고 컴플라이언스 게이트를 통과했는지 확인할 수 있게 했다.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from src.graph import ask, backend_info

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF Answer Agent</title><style>
*{box-sizing:border-box}
body{margin:0;font-family:'Malgun Gothic',system-ui,sans-serif;background:#f4f6f8;color:#16212b}
header{background:#121e29;color:#fff;padding:18px 24px}
header h1{margin:0;font-size:17px}
header p{margin:4px 0 0;font-size:12px;color:#8fa8b4}
main{max-width:860px;margin:0 auto;padding:20px 16px 90px}
.msg{margin:14px 0;display:flex;gap:10px}
.msg .who{font-size:11px;color:#7c8a94;min-width:46px;padding-top:9px}
.bub{background:#fff;border:1px solid #dce2e6;border-radius:10px;padding:12px 14px;
     white-space:pre-wrap;line-height:1.65;font-size:14px;flex:1}
.me .bub{background:#e8f0f4;border-color:#cddbe3}
.cite{margin-top:9px;padding-top:8px;border-top:1px solid #eef2f4;font-size:11.5px;color:#63727f}
details{margin-top:9px}
summary{cursor:pointer;font-size:11.5px;color:#0f5c52;font-weight:700}
.tr{margin-top:7px;font-size:11.5px;color:#4a5a66;line-height:1.85;font-family:Consolas,monospace}
form{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #dce2e6;padding:12px}
.row{max-width:860px;margin:0 auto;display:flex;gap:8px}
input{flex:1;padding:11px 13px;border:1px solid #cfd8de;border-radius:8px;font-size:14px}
button{padding:11px 20px;background:#0f5c52;color:#fff;border:0;border-radius:8px;
       font-size:14px;font-weight:700;cursor:pointer}
.chips{margin:6px auto 0;max-width:860px;display:flex;gap:6px;flex-wrap:wrap}
.chip{font-size:11.5px;padding:5px 10px;background:#eef2f5;border:1px solid #dce2e6;
      border-radius:14px;cursor:pointer;color:#3c4b58}
</style></head><body>
<header><h1>ETF Answer Agent — 프로토타입</h1><p>백엔드: __BACKEND__ &nbsp;·&nbsp; 5단계 파이프라인 + 컴플라이언스 검증 게이트</p></header>
<main id="log"></main>
<form onsubmit="send(event)"><div class="row">
<input id="q" autocomplete="off" placeholder="ETF에 대해 물어보세요">
<button>전송</button></div>
<div class="chips">
<span class="chip" onclick="fill(this)">ETF가 뭔가요?</span>
<span class="chip" onclick="fill(this)">TIGER 미국나스닥100 총보스 얼마애요?</span>
<span class="chip" onclick="fill(this)">나스닥100 분배금 언제 지급되나요</span>
<span class="chip" onclick="fill(this)">ETF 세금은 어떻게 되나요</span>
<span class="chip" onclick="fill(this)">지금 어떤 ETF 사는게 좋을까요?</span>
</div></form>
<script>
const log=document.getElementById('log'), qi=document.getElementById('q');
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function fill(el){qi.value=el.textContent;qi.focus()}
function add(who,cls,html){const d=document.createElement('div');d.className='msg '+cls;
 d.innerHTML='<div class="who">'+who+'</div><div class="bub">'+html+'</div>';
 log.appendChild(d);window.scrollTo(0,document.body.scrollHeight);return d}
async function send(e){e.preventDefault();const q=qi.value.trim();if(!q)return;qi.value='';
 add('고객','me',esc(q));const w=add('에이전트','','...');
 const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({q})}); const d=await r.json();
 let h=esc(d.answer);
 if(d.citations&&d.citations.length) h+='<div class="cite">근거 · '+d.citations.map(esc).join(' / ')+'</div>';
 if(d.trace&&d.trace.length) h+='<details><summary>처리 경로 보기</summary><div class="tr">'+
   d.trace.map(t=>esc(t)).join('<br>')+'</div></details>';
 w.querySelector('.bub').innerHTML=h;window.scrollTo(0,document.body.scrollHeight)}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.replace("__BACKEND__", backend_info()),
                       "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/ask":
            return self._send(404, json.dumps({"error": "not found"}))
        n = int(self.headers.get("Content-Length", 0))
        q = json.loads(self.rfile.read(n) or b"{}").get("q", "")
        r = ask(q)
        self._send(200, json.dumps({
            "answer": r.get("answer", ""),
            "citations": r.get("citations", []),
            "trace": r.get("trace", []),
            "intent": r.get("intent", ""),
        }, ensure_ascii=False))

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"백엔드: {backend_info()}")
    print("데모 UI: http://localhost:8000  (Ctrl+C 로 종료)")
    HTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
