(() => {
 'use strict';
 const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let page=1,requestId=0;
 async function load(){
  const current=++requestId,p=new URLSearchParams({start_date:$('dsStart').value,end_date:$('dsEnd').value,keyword:$('dsKeyword').value,page});
  $('dsMessage').textContent='조회 중…';
  try{
   const data=await MesResponse.read(await fetch('/api/subcontract/outbound/external-sync/records?'+p));if(current!==requestId)return;
   $('dsRows').innerHTML=data.rows.map(r=>'<tr>'+['outbound_date','outbound_no','partner_name','process_name','part_no','previous_part_no','lot_no','lot_count','outbound_qty','source_status_name'].map(k=>`<td>${esc(r[k])}</td>`).join('')+'</tr>').join('')||'<tr><td colspan="10">자료가 없습니다. 위에서 외주출고를 선택하고 가져오기를 실행하세요.</td></tr>';
   $('dsMessage').textContent=`총 ${data.total}행`;$('dsPage').textContent=`${page} / ${Math.max(1,Math.ceil(data.total/50))}`;$('dsPrev').disabled=page<=1;$('dsNext').disabled=page*50>=data.total;
  }catch(error){if(current===requestId){$('dsMessage').textContent=error.message;$('dsRows').replaceChildren();}}
 }
 $('dsEnd').value=MesLocalDate.today();$('dsStart').value=$('dsEnd').value.slice(0,7)+'-01';
 $('dsSearch').onsubmit=e=>{e.preventDefault();page=1;load();};$('dsPrev').onclick=()=>{page--;load();};$('dsNext').onclick=()=>{page++;load();};load();
})();
