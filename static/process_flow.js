(() => {
'use strict';
const root=document.getElementById('flowApp');if(!root)return;
const el=id=>document.getElementById(id),write=root.dataset.canWrite==='true';
const label={DRAFT:'초안',CURRENT:'현재 사용',SUPERSEDED:'구버전',RETIRED:'폐기'};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let selected=null,steps=[],records=[],busy=false,dirty=false,ready=false;
function message(t,error=false){el('flowMessage').textContent=t;el('flowMessage').className=error?'flow-error':'';}
async function request(url,method='GET',body){const r=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(Array.isArray(d.detail)?d.detail.map(x=>x.loc.join('.')+': '+x.msg).join('\n'):d.detail||'요청 실패');return d;}
function editable(){return write&&ready&&(!selected||selected.status==='DRAFT')&&(!selected||selected.item_selectable===true);}
function controls(){
 const edit=editable()&&!busy;
 root.querySelectorAll('.flow-fields input,.flow-fields select,.flow-fields textarea,#flowSteps input,#flowSteps textarea,[data-step-op]').forEach(x=>x.disabled=!edit);
 el('flowItem').disabled=!!selected||!edit;el('flowCode').disabled=!!selected||!edit;
 ['flowSave','flowAdd'].forEach(id=>{if(el(id))el(id).disabled=!edit;});
 if(el('flowNew'))el('flowNew').disabled=busy||!ready;
 if(el('flowActivate'))el('flowActivate').disabled=!selected||!edit;
 if(el('flowRevise'))el('flowRevise').disabled=busy||!write||!selected||selected.status==='DRAFT'||selected.item_selectable!==true;
 if(el('flowRetire'))el('flowRetire').disabled=busy||!write||!selected||selected.status==='RETIRED';
 el('flowPrint').disabled=busy||!selected||dirty;
 root.querySelectorAll('[data-flow-id]').forEach(x=>x.disabled=busy);
 ['flowSearch','flowReset'].forEach(id=>el(id).disabled=busy||!ready);
}
function abandon(){return !dirty||confirm('저장하지 않은 입력을 버리고 이동할까요?');}
async function task(fn){if(busy)return;busy=true;controls();try{await fn();}catch(e){message(e.message,true);}finally{busy=false;controls();}}
function diagram(){el('flowDiagram').innerHTML=steps.length?steps.map((s,i)=>(i?'<div class="flow-arrow" aria-hidden="true">↓</div>':'')+'<div class="flow-node"><strong>'+esc(s.step_no||'공정번호 입력')+'</strong>'+esc(s.step_name||'공정명 입력')+'</div>').join(''):'<p class="flow-note">공정을 등록해 주세요.</p>';}
function renderSteps(){
 el('flowSteps').innerHTML=steps.map((s,i)=>'<tr><td><input data-index="'+i+'" data-field="step_no" aria-label="공정번호" placeholder="공정번호" maxlength="50" value="'+esc(s.step_no)+'"><input data-index="'+i+'" data-field="step_name" aria-label="공정명" placeholder="공정명" maxlength="200" value="'+esc(s.step_name)+'"></td><td><textarea data-index="'+i+'" data-field="note" aria-label="공정 비고" maxlength="4000">'+esc(s.note)+'</textarea></td><td><button class="flow-btn light" data-step-op="up" data-index="'+i+'">위로</button> <button class="flow-btn light" data-step-op="down" data-index="'+i+'">아래로</button> <button class="flow-btn light" data-step-op="remove" data-index="'+i+'">제외</button></td></tr>').join('');
 diagram();controls();
}
function renderList(){
 el('flowList').innerHTML=records.length?records.map(x=>'<tr><td>'+esc(x.part_no)+'</td><td>'+esc(x.part_name)+'</td><td>'+esc(x.revision_code)+'</td><td>'+esc(label[x.status])+'</td><td>'+esc(x.change_reason||'최초 등록')+'</td><td>'+esc(x.created_by)+' / '+esc(x.created_at)+'</td><td><button class="flow-btn light" data-flow-id="'+x.id+'">선택</button></td></tr>').join(''):'<tr><td colspan="7">등록된 공정흐름도가 없습니다.</td></tr>';controls();
}
async function list(){records=await request('/api/process-flows?keyword='+encodeURIComponent(el('flowKeyword').value));renderList();}
async function history(itemId){
 const all=await request('/api/process-flows');const revs=all.filter(x=>x.item_id===itemId);
 el('flowHistory').innerHTML=revs.map(x=>'<tr><td>'+esc(x.revision_code)+'</td><td>'+esc(x.created_at)+'<br>'+esc(x.activated_at||'미적용')+'</td><td>'+esc(x.change_reason||'최초 등록')+'</td><td>'+esc(x.created_by)+'</td><td>'+esc(label[x.status])+'</td><td class="no-print"><button class="flow-btn light" data-flow-id="'+x.id+'">조회</button></td></tr>').join('');
}
function fill(x){el('flowItem').querySelectorAll('[data-historical-item]').forEach(option=>option.remove());if(!Array.from(el('flowItem').options).some(option=>option.value===String(x.item_id))){el('flowItem').insertAdjacentHTML('beforeend','<option data-historical-item disabled value="'+Number(x.item_id)+'">'+esc(x.part_no+' · '+x.part_name+' · 기존 이력 조회 전용')+'</option>');}selected=x;steps=x.steps;dirty=false;el('flowEditor').hidden=false;el('flowTitle').textContent=x.part_no+' · '+x.part_name+' · '+x.revision_code+' · '+label[x.status];el('flowItem').value=x.item_id;el('flowCode').value=x.revision_code;el('flowNote').value=x.note;el('flowMeta').textContent='당시 품목: '+x.part_no_snapshot+' · '+x.part_name_snapshot+' | 등록: '+x.created_by+' · '+x.created_at+(x.retire_reason?' | 폐기 사유: '+x.retire_reason:'')+(x.item_selectable?'':' | 완제품 선택 대상이 아닙니다. 기존 이력은 조회 전용이며 폐기만 가능합니다.');renderSteps();}
async function select(id){const x=await request('/api/process-flows/'+id);fill(x);await history(x.item_id);}
if(el('flowNew'))el('flowNew').onclick=()=>{if(!abandon())return;task(async()=>{el('flowItem').querySelectorAll('[data-historical-item]').forEach(option=>option.remove());selected=null;steps=[];dirty=false;el('flowEditor').hidden=false;el('flowTitle').textContent='신규 공정흐름도';['flowItem','flowCode','flowNote'].forEach(id=>el(id).value='');el('flowMeta').textContent='';el('flowHistory').innerHTML='';renderSteps();message('공정번호와 공정명을 한 칸에 입력하고 순서를 지정해 주세요.');});};
el('flowSearch').onclick=()=>task(list);el('flowReset').onclick=()=>{el('flowKeyword').value='';task(list);};
el('flowKeyword').onkeydown=e=>{if(e.key==='Enter')task(list);};
root.addEventListener('click',e=>{
 const b=e.target.closest('[data-flow-id]');if(b&&!busy&&abandon())task(()=>select(Number(b.dataset.flowId)));
 const op=e.target.closest('[data-step-op]');if(!op||!editable()||busy)return;
 const i=Number(op.dataset.index);
 if(op.dataset.stepOp==='remove'){if(steps[i].id&&!confirm('이 초안에서 공정을 제외할까요? 기존 이력은 보존됩니다.'))return;steps.splice(i,1);}
 else{const j=i+(op.dataset.stepOp==='up'?-1:1);if(j<0||j>=steps.length)return;[steps[i],steps[j]]=[steps[j],steps[i]];}
 dirty=true;renderSteps();
});
el('flowSteps').oninput=e=>{const f=e.target.dataset.field;if(!f||!editable()||busy)return;steps[Number(e.target.dataset.index)][f]=e.target.value;dirty=true;diagram();controls();};
root.querySelector('.flow-fields').oninput=()=>{if(editable()){dirty=true;controls();}};
if(el('flowAdd'))el('flowAdd').onclick=()=>{if(steps.length>=500){message('공정은 최대 500개입니다.',true);return;}steps.push({id:null,step_no:'',step_name:'',note:''});dirty=true;renderSteps();};
if(el('flowSave'))el('flowSave').onclick=()=>task(async()=>{
 const body={note:el('flowNote').value,steps:steps.map(s=>({id:s.id||null,step_no:s.step_no,step_name:s.step_name,note:s.note}))};
 if(selected)body.version=selected.version;else{body.item_id=Number(el('flowItem').value);body.revision_code=el('flowCode').value;}
 const result=await request(selected?'/api/process-flows/'+selected.id:'/api/process-flows',selected?'PUT':'POST',body);
 fill(result);await history(result.item_id);await list();message('초안을 저장했습니다. 확인 후 현재 사용 적용해 주세요.');
});
if(el('flowActivate'))el('flowActivate').onclick=()=>{if(dirty){message('초안을 먼저 저장해 주세요.',true);return;}if(!confirm('현재 사용 공정흐름도로 적용할까요? 기존 FMEA는 자동 변경하지 않고 기존 기준을 보존합니다.'))return;task(async()=>{const x=await request('/api/process-flows/'+selected.id+'/activate','POST',{version:selected.version});fill(x);await history(x.item_id);await list();message('현재 사용으로 적용했습니다. 기존 FMEA는 새 흐름도와 일치 여부를 검토해 개정해 주세요.');});};
if(el('flowRevise'))el('flowRevise').onclick=()=>{const code=prompt('새 공정흐름도 개정번호');if(!code?.trim())return;const reason=prompt('주요 개정 내용');if(!reason?.trim())return;task(async()=>{const x=await request('/api/process-flows/'+selected.id+'/revise','POST',{version:selected.version,revision_code:code.trim(),change_reason:reason.trim()});fill(x);await history(x.item_id);await list();message('기존 공정의 고유 연결을 유지한 새 초안을 만들었습니다.');});};
if(el('flowRetire'))el('flowRetire').onclick=()=>{if(dirty){message('초안을 저장하거나 다시 조회한 뒤 폐기해 주세요.',true);return;}const reason=prompt('폐기 사유. 기존 공정과 연결 문서 이력은 보존됩니다.');if(!reason?.trim())return;task(async()=>{const x=await request('/api/process-flows/'+selected.id+'/retire','POST',{version:selected.version,reason:reason.trim()});fill(x);await history(x.item_id);await list();message('폐기 처리했습니다. 구버전을 자동 적용하지 않습니다.');});};
el('flowPrint').onclick=()=>{if(selected&&!dirty)window.print();};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
task(async()=>{const items=await request('/api/process-flows/options');el('flowItem').innerHTML='<option value="">완제품 품목 선택</option>'+items.map(x=>'<option value="'+x.id+'"'+(x.is_active!=='Y'?' disabled':'')+'>'+esc(x.part_no+' · '+x.part_name)+'</option>').join('');ready=true;await list();message('품목별 공정흐름도를 선택하거나 신규 등록해 주세요.');});
})();