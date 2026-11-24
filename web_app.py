from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from agentscope.agent import ContextConfig
from agentscope.event import (
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
)
from agentscope.event import ConfirmResult
from agentscope.message import Msg, UserMsg
from agentscope.state import AgentState

from auth import (
    create_access_token,
    decode_access_token,
    dummy_password_check,
    validate_auth_config,
    verify_password,
)
from business import (
    audit_tool_confirmation,
    create_session,
    delete_session,
    get_session,
    get_tenant_membership,
    get_user_by_id,
    get_user_by_username,
    init_db,
    list_audit_logs,
    list_sessions,
    list_user_tenants,
    make_business_toolkit,
    save_session,
)
from demos.common import (
    build_agent,
    close_models,
    configure_console,
    enum_text,
    model_name,
    provider_name,
)
from meal_orchestration import run_meal_plan

AGENT_NAME = "业务助手"
SESSION_ID_PATTERN = r"^[a-zA-Z0-9_-]{20,100}$"
bearer = HTTPBearer(auto_error=False)
AGENTS: dict[tuple[str, str, str], Any] = {}
LOCKS: dict[tuple[str, str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
logger = logging.getLogger("agentscope.web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_auth_config()
    await init_db()
    AGENTS.clear()
    LOCKS.clear()
    yield
    await close_models()
    AGENTS.clear()
    LOCKS.clear()


app = FastAPI(title="AgentScope Business Lab", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    return response


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentScope Business Lab</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2e;--line:#26334f;--text:#e8eefc;--muted:#93a4c3;--accent:#7c9cff;--green:#55d6a7;--red:#ff7b86}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#18284b 0,transparent 42%),var(--bg);color:var(--text);font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif;height:100vh;display:flex;flex-direction:column}
header{display:flex;align-items:center;justify-content:space-between;padding:18px 28px;border-bottom:1px solid var(--line);background:#0b1020cc;backdrop-filter:blur(14px)}h1{font-size:19px;margin:0;letter-spacing:.3px}.badge{font-size:12px;color:var(--green);border:1px solid #28614f;background:#102a24;padding:4px 10px;border-radius:999px}.sub{color:var(--muted);font-size:12px;margin-top:3px}.actions{display:flex;gap:8px;align-items:center}
main{width:min(920px,calc(100% - 32px));margin:24px auto;display:flex;flex-direction:column;flex:1;min-height:0}.chat{flex:1;overflow:auto;padding:8px 4px 24px;display:flex;flex-direction:column;gap:16px}.msg{max-width:82%;padding:14px 17px;border-radius:16px;white-space:pre-wrap;overflow-wrap:anywhere}.user{align-self:flex-end;background:#284777;border-bottom-right-radius:5px}.assistant{align-self:flex-start;background:var(--panel);border:1px solid var(--line);border-bottom-left-radius:5px}.system{align-self:center;color:var(--muted);font-size:13px;background:transparent}.error{color:var(--red);border-color:#6e303b}.tools,.approval{align-self:flex-start;width:min(88%,720px);background:#0e1628;border:1px solid var(--line);border-radius:12px;padding:10px 14px;font-size:12px;color:var(--muted)}.tools summary{cursor:pointer;color:var(--accent)}pre{margin:7px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;color:#bdcbea}.suggestions{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 14px}.chip{background:#16223a;color:#c7d5f2;border:1px solid var(--line);border-radius:999px;padding:7px 12px;cursor:pointer}.chip:hover{border-color:var(--accent)}
.composer{display:flex;gap:10px;align-items:flex-end;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:10px}textarea{resize:none;flex:1;min-height:48px;max-height:150px;background:transparent;color:var(--text);border:0;outline:0;padding:10px;font:inherit}button{border:0;border-radius:11px;padding:11px 18px;cursor:pointer;background:var(--accent);color:#08102a;font-weight:650}button:disabled{opacity:.45;cursor:not-allowed}.new,.logout,.small{background:transparent;color:var(--muted);border:1px solid var(--line);padding:6px 11px;font-size:12px}.approve{background:#28614f;color:#dffff2}.reject{background:#542b36;color:#ffd9df}.status{height:25px;color:var(--muted);font-size:12px;padding:6px 4px 0}.sessionbar{display:flex;gap:10px;align-items:center;margin-bottom:12px;color:var(--muted);font-size:13px}select{flex:1;max-width:520px;background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:8px}.actions select{max-width:190px;padding:6px 9px;font-size:12px}.approval-row{border-top:1px solid var(--line);padding:9px 0}.approval-buttons{display:flex;gap:8px;margin-top:8px}
.login{width:min(420px,calc(100% - 32px));margin:auto;background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:28px}.login h2{margin:0 0 6px}.login p{color:var(--muted);margin:0 0 20px}.login label{display:block;color:var(--muted);font-size:13px;margin:12px 0 5px}.login input{width:100%;background:var(--bg);border:1px solid var(--line);border-radius:10px;color:var(--text);padding:11px}.login button{width:100%;margin-top:18px}.login-error{color:var(--red);min-height:22px;font-size:13px;margin-top:10px}[hidden]{display:none!important}@media(max-width:640px){header{padding:14px 16px}.msg,.tools,.approval{max-width:94%;width:94%}main{width:calc(100% - 20px)}.badge{display:none}}
</style>
</head>
<body>
<header><div><h1>AgentScope Business Lab</h1><div class="sub" id="model-info">正在读取配置…</div></div><div class="actions"><span class="badge">多租户 · 数据库工具 · SSE · HITL</span><select id="tenant-select" hidden aria-label="当前租户"></select><span id="user-name" hidden></span><button class="logout" id="logout" hidden>退出</button><button class="new" id="new-session" hidden>新会话</button></div></header>
<section class="login" id="auth-view">
<h2>登录业务工作台</h2><p>账号密码由本机 .env 中的引导配置初始化。</p>
<form id="login-form"><label for="username">用户名</label><input id="username" autocomplete="username" required maxlength="80"><label for="password">密码</label><input id="password" type="password" autocomplete="current-password" required maxlength="200"><div class="login-error" id="login-error"></div><button type="submit">登录</button></form>
</section>
<main id="app-view" hidden>
<div class="sessionbar"><span>历史会话</span><select id="sessions"></select><button class="small" id="delete-session">删除当前</button></div>
<div class="chat" id="chat"><div class="msg system">登录后可查询真实数据库中的订单、创建或关闭工单。写操作会暂停并请求确认。</div></div>
<div class="suggestions"><button class="chip">查询我的全部订单</button><button class="chip">为账号登录问题创建一个高优先级工单</button><button class="chip">查看我的未关闭工单</button><button class="chip" id="meal-plan">运行子 Agent 菜单编排</button></div>
<form class="composer" id="composer"><textarea id="input" placeholder="输入消息，Enter 发送，Shift+Enter 换行" maxlength="4000"></textarea><button id="send" type="submit">发送</button></form>
<div class="status" id="status">就绪</div>
</main>
<script>
const $=s=>document.querySelector(s),chat=$('#chat'),input=$('#input'),send=$('#send'),status=$('#status');
let token=sessionStorage.getItem('agentscope-token')||'',sessionId=localStorage.getItem('agentscope-session')||'',busy=false,pending=null,tenants=[];
function node(cls,text){const el=document.createElement('div');el.className='msg '+cls;el.textContent=text;chat.appendChild(el);chat.scrollTop=chat.scrollHeight;return el}
function toolsBox(){const el=document.createElement('details');el.className='tools';el.open=false;const summary=document.createElement('summary');summary.textContent='工具与模型事件';el.appendChild(summary);chat.appendChild(el);chat.scrollTop=chat.scrollHeight;return el}
function showAuth(){token='';sessionStorage.removeItem('agentscope-token');$('#auth-view').hidden=false;$('#app-view').hidden=true;$('#user-name').hidden=true;$('#tenant-select').hidden=true;$('#logout').hidden=true;$('#new-session').hidden=true}
function showApp(user,items=[]){tenants=items;$('#auth-view').hidden=true;$('#app-view').hidden=false;$('#user-name').hidden=false;$('#user-name').textContent=user.username+' · '+user.role;$('#logout').hidden=false;$('#new-session').hidden=false;const select=$('#tenant-select');select.replaceChildren();for(const item of tenants){const option=document.createElement('option');option.value=item.id;option.textContent=item.name+' · '+item.role;select.appendChild(option)}select.value=user.tenant_id;select.hidden=tenants.length<2}
async function request(path,options={},auth=true){const headers={'Content-Type':'application/json',...(options.headers||{})};if(auth&&token)headers.Authorization='Bearer '+token;const r=await fetch(path,{...options,headers});if(r.status===401&&auth){showAuth();throw new Error('登录已过期，请重新登录。')}if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(typeof d.detail==='string'?d.detail:'请求失败')}return r}
async function health(){try{const d=await(await request('/api/health',{},false)).json();$('#model-info').textContent=`${d.provider} / ${d.model} · ${d.api_key_configured?'百炼/模型已配置':'模型未配置'}`}catch(e){$('#model-info').textContent='无法读取配置'}}
async function loadSessions(){const items=await(await request('/api/sessions')).json(),select=$('#sessions');select.replaceChildren();for(const item of items){const option=document.createElement('option');option.value=item.id;option.textContent=item.title+' · '+new Date(item.updated_at).toLocaleString();select.appendChild(option)}if(sessionId)select.value=sessionId}
async function createSession(){const item=await(await request('/api/sessions',{method:'POST',body:JSON.stringify({title:'新会话'})})).json();sessionId=item.id;localStorage.setItem('agentscope-session',sessionId);chat.replaceChildren();node('system','已开始新会话。');await loadSessions();status.textContent='就绪'}
async function loadSession(id){if(busy)return;const item=await(await request('/api/sessions/'+encodeURIComponent(id))).json();sessionId=id;localStorage.setItem('agentscope-session',id);$('#sessions').value=id;chat.replaceChildren();for(const message of item.messages)node(message.role==='user'?'user':'assistant',message.text);if(!item.messages.length)node('system','这个会话还没有消息。');pending=item.pending.length?{reply_id:item.pending[0].reply_id,calls:item.pending,decisions:{}}:null;if(pending)showApproval(pending);status.textContent='已恢复持久化会话'}
async function confirmDecision(id,confirmed){if(!pending||busy)return;pending.decisions[id]=confirmed;if(Object.keys(pending.decisions).length===pending.calls.length)await submitConfirm()}
function showApproval(data){pending={reply_id:data.reply_id,calls:data.tool_calls,decisions:{}};const box=document.createElement('div');box.className='approval';const title=document.createElement('strong');title.textContent='写操作等待确认';box.appendChild(title);for(const call of data.tool_calls){const row=document.createElement('div');row.className='approval-row';const name=document.createElement('div');name.textContent=call.name;row.appendChild(name);const args=document.createElement('pre');args.textContent=JSON.stringify(call.input,null,2);row.appendChild(args);const buttons=document.createElement('div');buttons.className='approval-buttons';const yes=document.createElement('button');yes.className='approve';yes.textContent='批准';yes.onclick=()=>confirmDecision(call.id,true);const no=document.createElement('button');no.className='reject';no.textContent='拒绝';no.onclick=()=>confirmDecision(call.id,false);buttons.append(yes,no);row.appendChild(buttons);box.appendChild(row)}chat.appendChild(box);chat.scrollTop=chat.scrollHeight;status.textContent='等待人工确认'}
async function consume(response,answer){if(!response.ok){const d=await response.json().catch(()=>({}));throw new Error(d.detail||'请求失败')}status.textContent='模型运行中…';const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',streamDone=false;while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let split;while((split=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,split);buffer=buffer.slice(split+2);const eventLine=block.split('\n').find(x=>x.startsWith('event:'));const eventName=eventLine?eventLine.slice(6).trim():'message';const line=block.split('\n').find(x=>x.startsWith('data:'));if(!line)continue;const data=JSON.parse(line.slice(5).trim());const detail=document.createElement('pre');
if(eventName==='text_delta')answer.textContent+=data.delta;
else if(eventName==='tool_start'){const item=document.createElement('div');item.textContent='→ '+data.name;answer._tools?.appendChild(item)}
else if(eventName==='tool_delta'||eventName==='tool_result_delta'){const item=answer._tools?.lastElementChild;if(item){let args=item.querySelector('pre');if(!args){args=document.createElement('pre');item.appendChild(args)}args.textContent+=data.delta}}
else if(eventName==='tool_end'){const item=answer._tools?.lastElementChild;if(item)item.textContent+=' ['+data.state+']'}
else if(eventName==='model_start')status.textContent='调用模型 '+data.model;
else if(eventName==='model_end')status.textContent=`模型完成 · 输入 ${data.input_tokens} / 输出 ${data.output_tokens}`;
else if(eventName==='approval_required')showApproval(data);
else if(eventName==='paused')node('error','智能体正在等待外部执行，请在服务端完成外部工具。');
else if(eventName==='final'){answer.textContent=data.text;status.textContent='完成 · '+data.finished_reason}
else if(eventName==='error')node('error',data.message);
else if(eventName==='done')streamDone=true;
chat.scrollTop=chat.scrollHeight}if(streamDone)break}if(streamDone)await reader.cancel();answer.dataset.streaming='0';if(!answer.textContent&&!pending)answer.textContent='（没有文本输出）';await loadSessions()}
async function runMealPlan(){if(busy)return;const text='为 4 位成人规划 3 天菜单，其中一人对花生过敏，每天餐饮预算 120 元。';busy=true;send.disabled=true;node('user',text);status.textContent='子 Agent 编排中…';try{const data=await(await request('/api/meal-plan',{method:'POST',body:JSON.stringify({message:text})})).json();node('assistant',data.final);const box=toolsBox();for(const stage of data.stages){const item=document.createElement('div');item.textContent=`${stage.name} · ${stage.agent} · ${stage.status}`;const output=document.createElement('pre');output.textContent=stage.output;item.appendChild(output);box.appendChild(item)}status.textContent='子 Agent 编排完成'}catch(e){node('error',e.message);status.textContent='编排失败'}finally{busy=false;send.disabled=false;input.focus()}}
async function submitConfirm(){if(!pending||busy)return;const data=pending;pending=null;busy=true;send.disabled=true;const answer=node('assistant','');answer._tools=toolsBox();try{const response=await request('/api/confirm',{method:'POST',body:JSON.stringify({session_id:sessionId,reply_id:data.reply_id,decisions:data.calls.map(call=>({tool_call_id:call.id,confirmed:!!data.decisions[call.id]}))})});await consume(response,answer)}catch(e){node('error',e.message);status.textContent='确认失败'}finally{busy=false;send.disabled=false;input.focus()}}
async function submit(text){if(busy||!text.trim())return;if(!sessionId)await createSession();busy=true;send.disabled=true;node('user',text);input.value='';status.textContent='连接中…';const answer=node('assistant','');answer._tools=toolsBox();try{const response=await request('/api/chat',{method:'POST',body:JSON.stringify({session_id:sessionId,message:text})});await consume(response,answer)}catch(e){answer.dataset.streaming='0';node('error',e.message);status.textContent='请求失败'}finally{busy=false;send.disabled=false;input.focus()}}
$('#login-form').addEventListener('submit',async e=>{e.preventDefault();$('#login-error').textContent='';try{const r=await request('/api/auth/login',{method:'POST',body:JSON.stringify({username:$('#username').value.trim(),password:$('#password').value})},false);const d=await r.json();token=d.access_token;sessionStorage.setItem('agentscope-token',token);$('#password').value='';showApp(d.user,d.tenants);await health();await loadSessions();if(sessionId){try{await loadSession(sessionId)}catch(e){sessionId='';await createSession()}}else await createSession()}catch(e){$('#login-error').textContent=e.message}});
$('#logout').addEventListener('click',async()=>{try{await request('/api/auth/logout',{method:'POST'})}catch(e){}showAuth()});
$('#composer').addEventListener('submit',e=>{e.preventDefault();submit(input.value)});input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit(input.value)}});document.querySelectorAll('.chip').forEach(b=>b.addEventListener('click',()=>{if(b.id==='meal-plan')runMealPlan();else submit(b.textContent)}));$('#new-session').addEventListener('click',()=>{if(!busy)createSession()});$('#tenant-select').addEventListener('change',async e=>{if(busy||!e.target.value)return;try{const d=await(await request('/api/auth/tenant',{method:'POST',body:JSON.stringify({tenant_id:e.target.value})})).json();token=d.access_token;sessionStorage.setItem('agentscope-token',token);sessionId='';localStorage.removeItem('agentscope-session');showApp(d.user,d.tenants);await health();await createSession()}catch(e){node('error',e.message)}});
$('#sessions').addEventListener('change',e=>{if(!busy)loadSession(e.target.value)});$('#delete-session').addEventListener('click',async()=>{if(busy||!sessionId)return;try{await request('/api/sessions/'+encodeURIComponent(sessionId),{method:'DELETE'});sessionId='';localStorage.removeItem('agentscope-session');await createSession()}catch(e){node('error',e.message)}});
if(token){(async()=>{try{const user=await(await request('/api/auth/me')).json();showApp(user,user.tenants);await health();await loadSessions();if(sessionId){try{await loadSession(sessionId)}catch(e){sessionId='';await createSession()}}else await createSession()}catch(e){showAuth()}})()}else showAuth();
</script>
</body>
</html>"""


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class TenantSwitchRequest(BaseModel):
    tenant_id: str = Field(min_length=20, max_length=100)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        key: user[key]
        for key in ("id", "username", "role", "tenant_id", "tenant_name", "tenant_slug")
    }


def tenant_login_payload(
    user: dict[str, Any],
    membership: dict[str, Any],
    response: Response,
) -> dict[str, Any]:
    token_user = {
        **user,
        "role": membership["role"],
        "tenant_id": membership["id"],
        "tenant_name": membership["name"],
        "tenant_slug": membership["slug"],
    }
    token, expires_in = create_access_token(token_user)
    response.headers["Cache-Control"] = "no-store"
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": public_user(token_user),
    }


class SessionCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=80)


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    message: str = Field(min_length=1, max_length=4000)


class MealPlanRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ConfirmationDecision(BaseModel):
    tool_call_id: str = Field(min_length=1, max_length=200)
    confirmed: bool


class ConfirmationRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    reply_id: str = Field(min_length=1, max_length=200)
    decisions: list[ConfirmationDecision] = Field(min_length=1, max_length=20)


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="需要 Bearer 访问令牌。",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception as error:
        raise HTTPException(
            status_code=401,
            detail="访问令牌无效或已过期。",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    user = await get_user_by_id(payload["sub"])
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="用户不存在。",
            headers={"WWW-Authenticate": "Bearer"},
        )
    membership = await get_tenant_membership(user["id"], payload["tenant_id"])
    if membership is None or membership["role"] != payload["role"]:
        raise HTTPException(
            status_code=401,
            detail="租户成员资格或角色已变更，请重新登录。",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user.update(
        {
            "tenant_id": membership["id"],
            "tenant_name": membership["name"],
            "tenant_slug": membership["slug"],
            "role": membership["role"],
        },
    )
    return user


async def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限。")
    return user


def key_configured() -> bool:
    key = "DASHSCOPE_API_KEY" if provider_name() == "dashscope" else "OPENAI_API_KEY"
    return bool(os.getenv(key))


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def pending_calls(state: AgentState) -> list[dict[str, Any]]:
    return [
        {"id": call.id, "name": call.name, "input": call.input}
        for call in state.get_awaiting_tool_calls(AGENT_NAME)
        if enum_text(call.state).lower() == "asking"
    ]


def serialize_event(event: Any) -> str | None:
    if isinstance(event, ReplyStartEvent):
        return sse("reply_start", {"name": event.name, "reply_id": event.reply_id})
    if isinstance(event, TextBlockDeltaEvent):
        return sse("text_delta", {"delta": event.delta})
    if isinstance(event, ToolCallStartEvent):
        return sse("tool_start", {"id": event.tool_call_id, "name": event.tool_call_name})
    if isinstance(event, ToolCallDeltaEvent):
        return sse("tool_delta", {"id": event.tool_call_id, "delta": event.delta})
    if isinstance(event, ToolResultStartEvent):
        return sse("tool_result_start", {"id": event.tool_call_id, "name": event.tool_call_name})
    if isinstance(event, ToolResultTextDeltaEvent):
        return sse("tool_result_delta", {"id": event.tool_call_id, "delta": event.delta})
    if isinstance(event, ToolResultEndEvent):
        return sse("tool_end", {"id": event.tool_call_id, "state": enum_text(event.state)})
    if isinstance(event, ModelCallStartEvent):
        return sse("model_start", {"model": event.model_name})
    if isinstance(event, ModelCallEndEvent):
        return sse(
            "model_end",
            {"input_tokens": event.input_tokens, "output_tokens": event.output_tokens},
        )
    if isinstance(event, RequireUserConfirmEvent):
        return sse(
            "approval_required",
            {
                "reply_id": event.reply_id,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "input": call.input}
                    for call in event.tool_calls
                ],
            },
        )
    if isinstance(event, RequireExternalExecutionEvent):
        return sse("paused", {"reply_id": event.reply_id})
    if isinstance(event, ReplyEndEvent):
        return sse("reply_end", {"finished_reason": enum_text(event.finished_reason)})
    return None


async def owned_session(user: dict[str, Any], session_id: str) -> dict[str, Any]:
    session = await get_session(user["tenant_id"], user["id"], session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在。")
    return session


async def get_agent(user: dict[str, Any], session_id: str) -> tuple[Any, str]:
    session = await owned_session(user, session_id)
    key = (user["tenant_id"], user["id"], session_id)
    if key in AGENTS:
        return AGENTS[key], session["title"]
    state = AgentState.model_validate_json(session["state_json"])
    state.session_id = session_id
    agent = build_agent(
        name=AGENT_NAME,
        system_prompt=(
            "你是严谨的企业业务助手，只能使用提供的工具访问当前租户和当前登录用户的数据。"
            "不得访问或推测其他租户的数据。订单、状态和工单信息必须来自工具，不得编造。"
            "创建或关闭工单属于写操作，必须等待用户确认。用中文简洁回答，并说明关键订单号、金额或工单 ID。"
        ),
        tools=make_business_toolkit(user),
        stream=True,
        state=state,
        context_config=ContextConfig(tool_result_limit=2000),
    )
    AGENTS[key] = agent
    return agent, session["title"]


async def agent_events(
    agent: Any,
    user: dict[str, Any],
    session_id: str,
    title: str,
    inputs: Any,
):
    key = (user["tenant_id"], user["id"], session_id)
    async with LOCKS[key]:
        try:
            async for item in agent.reply_stream(inputs, yield_final_msg=True):
                if isinstance(item, Msg):
                    yield sse(
                        "final",
                        {
                            "text": item.get_text_content() or "",
                            "finished_reason": enum_text(item.finished_reason)
                            if item.finished_reason
                            else "paused",
                            "usage": item.usage.model_dump() if item.usage is not None else None,
                        },
                    )
                else:
                    payload = serialize_event(item)
                    if payload:
                        yield payload
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Agent reply failed")
            yield sse("error", {"message": "智能体执行失败，请检查模型配置、工具状态和服务日志。"})
        finally:
            try:
                await save_session(
                    user["tenant_id"],
                    user["id"],
                    session_id,
                    title,
                    agent.state.model_dump_json(),
                )
            except Exception:
                logger.exception("Failed to persist session")
                yield sse("error", {"message": "会话状态保存失败。"})
        yield sse("done", {})


def stream_response(events) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "agentscope": "2.0.12",
        "provider": provider_name(),
        "model": model_name(),
        "api_key_configured": key_configured(),
        "auth_required": True,
        "multi_tenant": True,
        "storage": "sqlite",
    }


@app.post("/api/auth/login")
async def login(request: LoginRequest, response: Response) -> dict[str, Any]:
    user = await get_user_by_username(request.username.strip())
    if user is None:
        dummy_password_check(request.password)
        raise HTTPException(status_code=401, detail="用户名或密码错误。")
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误。")
    tenants = await list_user_tenants(user["id"])
    if not tenants:
        raise HTTPException(status_code=403, detail="当前用户不属于任何租户。")
    membership = next((item for item in tenants if item["is_default"]), tenants[0])
    result = tenant_login_payload(user, membership, response)
    result["tenants"] = tenants
    return result


@app.post("/api/auth/tenant")
async def switch_tenant(
    request: TenantSwitchRequest,
    response: Response,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    membership = await get_tenant_membership(user["id"], request.tenant_id)
    if membership is None:
        raise HTTPException(status_code=403, detail="当前用户不属于该租户。")
    result = tenant_login_payload(user, membership, response)
    result["tenants"] = await list_user_tenants(user["id"])
    return result


@app.get("/api/tenants")
async def tenants(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return await list_user_tenants(user["id"])


@app.post("/api/auth/logout")
async def logout(response: Response, user: dict[str, Any] = Depends(current_user)) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    result = public_user(user)
    result["tenants"] = await list_user_tenants(user["id"])
    return result


@app.post("/api/sessions")
async def new_session(
    request: SessionCreate,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    state = AgentState(session_id="pending")
    session = await create_session(
        user["tenant_id"],
        user["id"],
        request.title,
        state.model_dump_json(),
    )
    state.session_id = session["id"]
    await save_session(
        user["tenant_id"],
        user["id"],
        session["id"],
        request.title,
        state.model_dump_json(),
    )
    return session


@app.get("/api/sessions")
async def sessions(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return await list_sessions(user["tenant_id"], user["id"])


@app.get("/api/sessions/{session_id}")
async def session_detail(
    session_id: str,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    session = await owned_session(user, session_id)
    state = AgentState.model_validate_json(session["state_json"])
    messages = [
        {"role": message.role, "name": message.name, "text": message.get_text_content()}
        for message in state.context
        if message.get_text_content()
    ]
    calls = pending_calls(state)
    return {
        "id": session["id"],
        "title": session["title"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "messages": messages,
        "pending": (
            [{"reply_id": state.reply_id, "tool_calls": calls}]
            if calls
            else []
        ),
    }


@app.delete("/api/sessions/{session_id}")
async def remove_session(
    session_id: str,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, bool]:
    deleted = await delete_session(user["tenant_id"], user["id"], session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在。")
    AGENTS.pop((user["tenant_id"], user["id"], session_id), None)
    return {"ok": True}


@app.post("/api/chat")
async def chat(
    request: ChatRequest,
    user: dict[str, Any] = Depends(current_user),
) -> StreamingResponse:
    if not key_configured():
        raise HTTPException(status_code=503, detail="请先在 .env 中配置模型 API Key。")
    try:
        agent, title = await get_agent(user, request.session_id)
    except SystemExit as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if title == "新会话":
        title = request.message.strip()[:40]
    return stream_response(
        agent_events(
            agent,
            user,
            request.session_id,
            title,
            UserMsg(name="user", content=request.message),
        ),
    )


@app.post("/api/confirm")
async def confirm(
    request: ConfirmationRequest,
    user: dict[str, Any] = Depends(current_user),
) -> StreamingResponse:
    if not key_configured():
        raise HTTPException(status_code=503, detail="请先在 .env 中配置模型 API Key。")
    try:
        agent, title = await get_agent(user, request.session_id)
    except SystemExit as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if request.reply_id != agent.state.reply_id:
        raise HTTPException(status_code=409, detail="确认请求对应的回复已失效。")
    awaiting = {
        call.id: call
        for call in agent.state.get_awaiting_tool_calls(AGENT_NAME)
        if enum_text(call.state).lower() == "asking"
    }
    decision_ids = {item.tool_call_id for item in request.decisions}
    if len(decision_ids) != len(request.decisions) or decision_ids != set(awaiting):
        raise HTTPException(status_code=400, detail="确认项与当前待审批工具调用不匹配。")
    confirm_results = [
        ConfirmResult(
            confirmed=item.confirmed,
            tool_call=awaiting[item.tool_call_id],
        )
        for item in request.decisions
    ]
    await audit_tool_confirmation(
        user["tenant_id"],
        user["id"],
        request.session_id,
        [
            {
                "tool_call_id": item.tool_call_id,
                "name": awaiting[item.tool_call_id].name,
                "confirmed": item.confirmed,
            }
            for item in request.decisions
        ],
    )
    event = UserConfirmResultEvent(
        reply_id=request.reply_id,
        confirm_results=confirm_results,
    )
    return stream_response(agent_events(agent, user, request.session_id, title, event))


@app.post("/api/meal-plan")
async def meal_plan(
    request: MealPlanRequest,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    if not key_configured():
        raise HTTPException(status_code=503, detail="请先在 .env 中配置模型 API Key。")
    try:
        result = await run_meal_plan(request.message, tenant_name=user["tenant_name"])
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail="子 Agent 编排失败，请查看服务日志。") from error
    return result.model_dump(mode="json")


@app.get("/api/admin/audit")
async def audit(
    limit: int = Query(default=50, ge=1, le=200),
    user: dict[str, Any] = Depends(require_admin),
) -> list[dict[str, Any]]:
    return await list_audit_logs(user["tenant_id"], limit)


if __name__ == "__main__":
    import uvicorn

    configure_console()
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        log_level="info",
        workers=1,
    )
