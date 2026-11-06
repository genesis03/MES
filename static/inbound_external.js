(()=>{
 const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let page=1,version=0;
 const today=MesLocalDate.today();$('inStart').value=today.slice(0,8)+'01';$('inEnd').value=today;
 async function load(){const request=++version;try{const params=new URLSearchParams({start_date:$('inStart').value,end_date:$('inEnd').value,keyword:$('inKeyword').value,product_type:$('inType').value,include_missing:$('inMissing').checked,page});const r=await fetch('/api/inbound/external-sync/records?'+params);const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'조회 조건을 확인하세요.');if(request!==version)return;
 $('inRows').innerHTML=d.rows.map(row=>'<tr>'+[row.inbound_date,row.confirm_date,row.inbound_no,row.partner_name,row.part_no,row.part_name,row.product_type,row.material_type,Number(row.confirmed_qty).toLocaleString('ko-KR',{maximumFractionDigits:6}),row.unit||'—',!row.present?'원본 누락':row.linked?'연결':'품번 미연결'].map(v=>`<td>${esc(v)}</td>`).join('')+'</tr>').join('')||'<tr><td colspan="11">입고 자료가 없습니다.</td></tr>';
 $('inMessage').textContent=`${d.total}건`;$('inPage').textContent=`${page} / ${Math.max(1,Math.ceil(d.total/50))}`;$('inPrev').disabled=page===1;$('inNext').disabled=page*50>=d.total;
 }catch(e){if(request===version)$('inMessage').textContent=e.message;}}
 $('inSearch').onsubmit=e=>{e.preventDefault();page=1;load();};$('inPrev').onclick=()=>{page--;load();};$('inNext').onclick=()=>{page++;load();};load();setInterval(load,30000);
})();
