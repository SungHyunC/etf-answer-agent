"""데모 UI 템플릿 — app.py 의 PAGE 상수를 대체하는 개선판 HTML.

3차 요구 'UI/UX 통합 및 개선'에 대응한다. 기존 화면 대비 달라진 점:

  · 처리 경로(trace)를 5단계 배지로 요약하고, 컴플라이언스 반려가 있었으면 붉게 강조
  · 근거(citations)를 접이식이 아니라 항상 보이는 칩으로 노출
  · 답변마다 👍/👎 피드백 버튼 → POST /feedback
  · 상단 지표 바에서 /metrics 를 5초마다 폴링 (총 요청수·차단율·p95 지연)
  · 응답 대기 중 스켈레톤, 모바일 반응형

외부 CDN 없이 인라인 CSS/JS 만 사용한다. 서버 응답에 필드가 빠져 있어도
화면이 깨지지 않도록 JS 쪽을 전부 방어적으로 작성했다.

사용법:
    from ui_template import render
    html = render(backend_info())
"""
from __future__ import annotations

import html as _html

# 백엔드 표기는 render() 에서 __BACKEND__ 자리에 끼워 넣는다.
# (CSS/JS 에 중괄호가 많아 format 대신 치환 방식을 쓴다 — 기존 app.py 와 동일한 방식)
PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF Answer Agent</title><style>
:root{--ink:#16212b;--teal:#0f5c52;--red:#a32e22;--bg:#f4f6f8;--card:#fff;
      --line:#dce2e6;--mute:#63727f;--head:#121e29}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);-webkit-text-size-adjust:100%;
     font-family:'Malgun Gothic','Apple SD Gothic Neo',system-ui,sans-serif}

/* 헤더 + 실시간 지표 바 */
header{background:var(--head);color:#fff;padding:14px 20px 0;position:sticky;top:0;z-index:5}
.hd{max-width:900px;margin:0 auto}
.hd h1{margin:0;font-size:17px;letter-spacing:-.2px}
.hd p{margin:4px 0 0;font-size:12px;color:#8fa8b4}
.mbar{max-width:900px;margin:11px auto 0;padding-bottom:12px;display:flex;gap:8px;flex-wrap:wrap}
.m{flex:1 1 150px;min-width:0;padding:7px 10px;border-radius:8px;
   background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12)}
.m .k{display:block;font-size:10.5px;color:#8fa8b4;white-space:nowrap}
.m .v{font-size:16px;font-weight:700;line-height:1.35;font-variant-numeric:tabular-nums}
.m .v.hot{color:#ffb4a8}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#3fae8f;
     margin-right:5px;vertical-align:middle}
.dot.off{background:#7c8a94}

/* 대화 이력 */
main{max-width:900px;margin:0 auto;padding:18px 16px 124px}
.hint{background:var(--card);border:1px dashed var(--line);border-radius:12px;
      padding:14px 16px;font-size:12.5px;color:var(--mute);line-height:1.75}
.msg{margin:16px 0;display:flex;gap:10px}
.who{flex:0 0 48px;min-width:48px;padding-top:10px;font-size:11px;color:var(--mute)}
.bub{flex:1;min-width:0;background:var(--card);border:1px solid var(--line);
     border-radius:12px;padding:13px 15px;box-shadow:0 1px 2px rgba(22,33,43,.04)}
.me .bub{background:#e8f0f4;border-color:#cddbe3}
.txt{white-space:pre-wrap;line-height:1.7;font-size:14px;word-break:break-word}
.txt.err{color:var(--red)}
.meta{margin-top:9px;font-size:10.5px;color:var(--mute)}

/* 처리 경로 5단계 배지 */
.stages{display:flex;gap:5px;flex-wrap:wrap;margin-top:8px}
.st{padding:3px 8px;border-radius:12px;font-size:10.5px;white-space:nowrap;
    border:1px solid var(--line);background:#f7f9fa;color:var(--mute)}
.st.ok{border-color:#bfd8d3;background:#eaf4f2;color:var(--teal);font-weight:700}
.st.bad{border-color:#e6bcb6;background:#fbeeec;color:var(--red);font-weight:700}
.st.skip{opacity:.5}
.st .n{margin-left:3px;font-weight:400;opacity:.75}
.rej{margin-top:9px;padding:7px 10px;border-left:3px solid var(--red);border-radius:0 6px 6px 0;
     background:#fbeeec;color:var(--red);font-size:11.5px;font-weight:700;line-height:1.6}

/* 근거 칩 — 항상 노출 */
.cites{display:flex;gap:5px;flex-wrap:wrap;align-items:center;margin-top:9px}
.cl{font-size:10.5px;color:var(--mute)}
.cc{max-width:100%;padding:3px 8px;border-radius:5px;font-size:10.5px;color:#3c4b58;
    background:#eef2f5;border:1px solid var(--line);
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cc.none{background:#fff;color:var(--mute);border-style:dashed}

/* 피드백 + 처리 상세 */
.foot{display:flex;gap:6px;align-items:center;flex-wrap:wrap;
      margin-top:11px;padding-top:9px;border-top:1px solid #eef2f4}
.fb{padding:4px 10px;border:1px solid var(--line);background:#fff;border-radius:14px;
    font-size:12px;color:#3c4b58;line-height:1.4;cursor:pointer;font-family:inherit}
.fb:hover{border-color:#b9c5cd}
.fb.on{border-color:var(--teal);background:#eaf4f2;color:var(--teal);font-weight:700}
.fb.on.no{border-color:var(--red);background:#fbeeec;color:var(--red)}
.fb[disabled]{cursor:default;opacity:.95}
.fmsg{font-size:11px;color:var(--mute)}
.tgl{margin-left:auto;padding:4px 2px;border:0;background:none;font-family:inherit;
     font-size:11px;font-weight:700;color:var(--teal);cursor:pointer}
.tr{display:none;margin-top:8px;padding:9px 11px;border:1px solid #eef2f4;border-radius:8px;
    background:#f7f9fa;font-size:11.5px;color:#4a5a66;line-height:1.85;overflow-x:auto;
    font-family:Consolas,'D2Coding',monospace}
.tr.open{display:block}

/* 대기 스켈레톤 */
.sk{height:11px;margin:7px 0;border-radius:6px;background-size:400% 100%;
    background:linear-gradient(90deg,#eef2f4 25%,#e2e8ec 37%,#eef2f4 63%);
    animation:sh 1.3s ease-in-out infinite}
.sk.w1{width:92%}.sk.w2{width:78%}.sk.w3{width:54%}
@keyframes sh{0%{background-position:100% 50%}100%{background-position:0 50%}}
.wait{margin-top:9px;font-size:11px;color:var(--mute)}

/* 입력 영역 */
form{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);
     padding:10px 12px calc(10px + env(safe-area-inset-bottom))}
.row{max-width:900px;margin:0 auto;display:flex;gap:8px}
input{flex:1;min-width:0;padding:11px 13px;border:1px solid #cfd8de;border-radius:9px;
      font-size:15px;color:var(--ink);font-family:inherit}
input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 2px rgba(15,92,82,.12)}
.send{padding:11px 20px;border:0;border-radius:9px;background:var(--teal);color:#fff;
      font-size:14px;font-weight:700;cursor:pointer;font-family:inherit}
.send[disabled]{background:#7f9b96;cursor:default}
.chips{max-width:900px;margin:8px auto 0;display:flex;gap:6px;flex-wrap:wrap}
.chip{padding:5px 10px;border:1px solid var(--line);border-radius:14px;background:#eef2f5;
      font-size:11.5px;color:#3c4b58;cursor:pointer;font-family:inherit}
.chip:hover{border-color:#b9c5cd}
.chip.warn{background:#fbeeec;border-color:#e6bcb6;color:var(--red);font-weight:700}
.chip.warn::before{content:"규제 차단 예시 · ";opacity:.7}

/* 모바일 */
@media(max-width:640px){
 header{padding:12px 14px 0}
 .hd h1{font-size:15.5px}
 .m{flex:1 1 calc(50% - 4px);padding:6px 9px}
 .m .v{font-size:14.5px}
 main{padding:14px 12px 138px}
 .msg{flex-direction:column;gap:5px}
 .who{flex:none;min-width:0;padding-top:0}
 .bub{padding:11px 12px}
 .tgl{margin-left:0}
 .chips{max-height:74px;overflow-y:auto}
}
@media(prefers-reduced-motion:reduce){.sk{animation:none}}
</style></head><body>

<header>
  <div class="hd">
    <h1>ETF Answer Agent — 프로토타입</h1>
    <p>백엔드: __BACKEND__ &nbsp;·&nbsp; 5단계 파이프라인 + 컴플라이언스 검증 게이트</p>
  </div>
  <div class="mbar" id="mbar">
    <div class="m"><span class="k"><span class="dot off" id="mdot"></span>총 요청</span>
      <span class="v" id="m-total">–</span></div>
    <div class="m"><span class="k">규제 차단율</span><span class="v" id="m-block">–</span></div>
    <div class="m"><span class="k">응답 지연 p95</span><span class="v" id="m-p95">–</span></div>
  </div>
</header>

<main id="log" aria-live="polite">
  <div class="hint" id="hint">아래 예시 칩을 누르거나 질문을 입력해 보세요.
답변마다 처리 경로 5단계 배지와 사용된 근거를 함께 표시하며, 컴플라이언스 게이트에서 반려가
발생하면 붉게 강조합니다. 👍/👎 는 서버로 전송되어 개선 데이터로 쌓입니다.</div>
</main>

<form id="f">
  <div class="row">
    <input id="q" autocomplete="off" placeholder="ETF에 대해 물어보세요">
    <button class="send" id="sb" type="submit">전송</button>
  </div>
  <div class="chips" id="chips">
    <button type="button" class="chip">ETF가 뭔가요?</button>
    <button type="button" class="chip">TIGER 미국나스닥100 총보스 얼마애요?</button>
    <button type="button" class="chip">나스닥100 분배금 언제 지급되나요</button>
    <button type="button" class="chip">ETF 세금은 어떻게 되나요</button>
    <button type="button" class="chip warn">지금 어떤 ETF 사는게 좋을까요?</button>
    <button type="button" class="chip warn">원금 보장되는 ETF 추천해주세요</button>
  </div>
</form>

<script>
var log=document.getElementById('log'), qi=document.getElementById('q'),
    sb=document.getElementById('sb'), busy=false;

// 5단계 정의 — trace 문자열의 머리글자(①~⑤)로 어느 단계 로그인지 판별한다.
var STAGES=[['①','전처리'],['②','의도분류'],['③','검색'],['④','생성'],['⑤','검증']];

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function el(id){return document.getElementById(id);}
function bottom(){window.scrollTo(0,document.body.scrollHeight);}

/* ── 예시 칩 ─────────────────────────────────────────── */
var chips=el('chips').getElementsByClassName('chip');
for(var ci=0;ci<chips.length;ci++){
  chips[ci].addEventListener('click',function(){qi.value=this.textContent.trim();qi.focus();});
}

/* ── trace 파싱 ──────────────────────────────────────── */
// 한 줄에 여러 단계 기호가 섞여도(예: "⑤ … → ④로 재생성") 가장 앞선 기호를 그 줄의 단계로 본다.
function parseTrace(trace){
  var info=[],etc=[],rej=[],i;
  for(i=0;i<STAGES.length;i++) info.push({n:0,rej:false,lines:[]});
  (trace&&trace.length?trace:[]).forEach(function(raw){
    var s=String(raw==null?'':raw), hit=-1, pos=-1;
    for(var j=0;j<STAGES.length;j++){
      var p=s.indexOf(STAGES[j][0]);
      if(p>=0&&(hit<0||p<pos)){hit=j;pos=p;}
    }
    if(s.indexOf('반려')>=0) rej.push(s);
    if(hit<0){etc.push(s);return;}
    info[hit].n++; info[hit].lines.push(s);
    if(s.indexOf('반려')>=0) info[hit].rej=true;
  });
  return {info:info,etc:etc,rej:rej};
}

function badgesHtml(t){
  var out='<div class="stages">',i;
  for(i=0;i<STAGES.length;i++){
    var c=t.info[i], cls=c.n===0?'st skip':(c.rej?'st bad':'st ok'),
        n=c.n>1?'<span class="n">×'+c.n+'</span>':'',
        tip=c.lines.length?c.lines.join(' / '):'실행 기록 없음';
    out+='<span class="'+cls+'" title="'+esc(tip)+'">'+STAGES[i][0]+' '+STAGES[i][1]+n+'</span>';
  }
  return out+'</div>';
}

// 반려가 한 번이라도 있었으면 사유를 뽑아 눈에 띄게 띄운다.
function rejectHtml(t){
  if(!t.rej.length) return '';
  var reasons=[];
  t.rej.forEach(function(s){
    var m=s.match(/반려\\(([^)]*)\\)/);
    if(m&&reasons.indexOf(m[1])<0) reasons.push(m[1]);
  });
  var over=t.rej.join(' ').indexOf('한도 초과')>=0;
  return '<div class="rej">⚠ 컴플라이언스 반려 '+t.rej.length+'회'+
         (reasons.length?' — '+esc(reasons.join(' / ')):'')+
         (over?'<br>재생성 한도 초과 → 안전 응답으로 대체':'<br>재생성 후 게이트를 통과한 답변만 전달됩니다')+
         '</div>';
}

function citesHtml(cs){
  var arr=[];
  (cs&&cs.length?cs:[]).forEach(function(c){
    var s=String(c==null?'':c).trim(); if(s) arr.push(s);
  });
  var body=arr.length
    ? arr.map(function(s){return '<span class="cc" title="'+esc(s)+'">'+esc(s)+'</span>';}).join('')
    : '<span class="cc none">사용된 근거 없음</span>';
  return '<div class="cites"><span class="cl">근거</span>'+body+'</div>';
}

/* ── 메시지 렌더 ─────────────────────────────────────── */
function addMsg(who,cls,inner){
  var d=document.createElement('div');
  d.className='msg '+cls;
  d.innerHTML='<div class="who">'+who+'</div><div class="bub">'+inner+'</div>';
  log.appendChild(d); bottom(); return d;
}

function skeleton(){
  return '<div class="sk w1"></div><div class="sk w2"></div><div class="sk w3"></div>'+
         '<div class="stages">'+STAGES.map(function(s){
           return '<span class="st skip">'+s[0]+' '+s[1]+'</span>';}).join('')+'</div>'+
         '<div class="wait">5단계 파이프라인 처리 중…</div>';
}

function renderAnswer(box,d,ms){
  var t=parseTrace(d.trace),
      raw=(d.trace&&d.trace.length?d.trace:[]).map(function(s){return esc(s);})
          .filter(function(s){return s!=='';}).join('<br>'),
      id=(d.request_id!=null?d.request_id:(d.id!=null?d.id:'')),
      meta=[];
  if(d.intent) meta.push('의도 '+esc(d.intent));
  if(d.store_used) meta.push('창고 '+esc(d.store_used));
  meta.push('응답 '+ms+'ms');
  if(id) meta.push('ID '+esc(String(id).slice(0,12)));

  var h='<div class="txt">'+esc(d.answer!=null&&String(d.answer)!==''?d.answer:'(빈 응답)')+'</div>'+
        '<div class="meta">'+meta.join(' · ')+'</div>'+
        badgesHtml(t)+rejectHtml(t)+citesHtml(d.citations)+
        '<div class="foot">'+
          '<button class="fb" data-fb="up" type="button">👍 도움됐어요</button>'+
          '<button class="fb" data-fb="down" type="button">👎 아쉬워요</button>'+
          '<span class="fmsg"></span>'+
          (raw?'<button class="tgl" type="button">처리 상세 보기</button>':'')+
        '</div>'+
        (raw?'<div class="tr">'+raw+'</div>':'');

  var bub=box.querySelector('.bub');
  bub.innerHTML=h;

  var up=bub.querySelector('[data-fb="up"]'), dn=bub.querySelector('[data-fb="down"]'),
      tg=bub.querySelector('.tgl'), tr=bub.querySelector('.tr');
  up.addEventListener('click',function(){sendFeedback(bub,id,true);});
  dn.addEventListener('click',function(){sendFeedback(bub,id,false);});
  if(tg&&tr) tg.addEventListener('click',function(){
    var open=tr.classList.toggle('open');
    tg.textContent=open?'처리 상세 숨기기':'처리 상세 보기';
  });
}

/* ── 피드백 전송 ─────────────────────────────────────── */
function sendFeedback(bub,id,useful){
  var btns=bub.getElementsByClassName('fb'), msg=bub.querySelector('.fmsg'), i;
  for(i=0;i<btns.length;i++) btns[i].disabled=true;
  var hit=bub.querySelector(useful?'[data-fb="up"]':'[data-fb="down"]');
  hit.classList.add('on'); if(!useful) hit.classList.add('no');
  msg.textContent='전송 중…';
  fetch('/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({request_id:id,useful:useful})})
    .then(function(r){
      msg.textContent=r.ok?'피드백 감사합니다.':'피드백 저장 실패 (HTTP '+r.status+')';
      if(r.ok) poll();
    })
    .catch(function(){msg.textContent='피드백 전송 실패 — 서버 연결을 확인해 주세요.';});
}

/* ── 질문 전송 ───────────────────────────────────────── */
function setBusy(v){busy=v;sb.disabled=v;sb.textContent=v?'처리 중':'전송';}

el('f').addEventListener('submit',function(e){
  e.preventDefault();
  if(busy) return;
  var q=qi.value.trim(); if(!q) return;
  var hint=el('hint'); if(hint) hint.remove();
  qi.value=''; setBusy(true);
  addMsg('고객','me',esc(q));
  var box=addMsg('에이전트','',skeleton()), t0=Date.now();

  fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({q:q})})
    .then(function(r){
      return r.json().catch(function(){return null;}).then(function(d){
        if(!d||typeof d!=='object') d={};
        if(!r.ok&&!d.answer) throw new Error('HTTP '+r.status);
        return d;
      });
    })
    .then(function(d){renderAnswer(box,d,Date.now()-t0);})
    .catch(function(err){
      box.querySelector('.bub').innerHTML=
        '<div class="txt err">응답을 받지 못했습니다. ('+esc(err&&err.message?err.message:err)+')<br>'+
        '서버가 실행 중인지 확인해 주세요.</div>';
    })
    .then(function(){setBusy(false);poll();bottom();});
});

/* ── 실시간 지표 폴링 (5초) ──────────────────────────── */
function pick(d,keys){
  for(var i=0;i<keys.length;i++){
    var v=d[keys[i]];
    if(v!==undefined&&v!==null&&v!=='') return v;
  }
  return null;
}
function toNum(v){var n=Number(v);return (v===null||v===undefined||v===''||!isFinite(n))?NaN:n;}
function fInt(v){var n=toNum(v);return isNaN(n)?'–':Math.round(n).toLocaleString('ko-KR');}
// 비율(0~1)로 와도, 퍼센트(0~100)로 와도 읽히도록 처리한다.
function fPct(v){var n=toNum(v);if(isNaN(n))return '–';if(n<=1)n*=100;return (Math.round(n*10)/10)+'%';}
function fMs(v){var n=toNum(v);if(isNaN(n))return '–';
  return n>=1000?(Math.round(n/100)/10)+'s':Math.round(n)+'ms';}

function poll(){
  fetch('/metrics',{cache:'no-store'})
    .then(function(r){if(!r.ok) throw new Error('HTTP '+r.status);return r.json();})
    .then(function(d){
      if(!d||typeof d!=='object') d={};
      var br=pick(d,['block_rate','blocked_rate','reject_rate']);
      el('m-total').textContent=fInt(pick(d,['total_requests','requests','total']));
      el('m-block').textContent=fPct(br);
      el('m-p95').textContent=fMs(pick(d,['latency_p95_ms','p95_ms','latency_p95']));
      // 차단이 실제로 발생하면 눈에 띄게 — 게이트가 동작 중이라는 신호
      var n=toNum(br); if(!isNaN(n)&&n<=1) n*=100;
      el('m-block').className='v'+((!isNaN(n)&&n>0)?' hot':'');
      el('mdot').className='dot';
      el('mbar').title='마지막 갱신 '+new Date().toLocaleTimeString('ko-KR');
    })
    .catch(function(){
      // /metrics 가 없거나 실패해도 UI 는 그대로 — 표시등만 회색으로 내린다.
      el('mdot').className='dot off';
      el('mbar').title='지표를 가져오지 못했습니다 (/metrics 미제공)';
    });
}
poll(); setInterval(poll,5000);
</script></body></html>"""


def render(backend: str) -> str:
    """백엔드 표기를 끼워 넣은 완성 HTML 을 돌려준다."""
    label = _html.escape(str(backend) if backend else "unknown")
    return PAGE.replace("__BACKEND__", label)
