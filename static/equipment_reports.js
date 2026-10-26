(() => {
    'use strict';
    const $=id=>document.getElementById(id),root=$('eqReport'),view=root.dataset.view;
    const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const number=value=>value==null?'—':Number(value).toLocaleString('ko-KR',{maximumFractionDigits:2});
    const percent=value=>value==null?'—':number(value*100)+'%';
    const minutes=value=>value==null?'—':number(value/60);
    const current=window.MesLocalDate?.today()||new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date());
    let options,request=0;
    function message(value,error=false){$('eqMessage').textContent=value;$('eqMessage').classList.toggle('error',error);}
    async function api(path,params){
        const response=await fetch('/api/production/equipment/'+path+'?'+new URLSearchParams(params));
        const data=await response.json();
        if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'조회 조건을 확인해 주세요.');
        return data;
    }
    function notes(issues){return issues?.length?`<details class="eq-notes"><summary>확인사항 ${issues.length}건</summary>${issues.map(i=>`<div>${esc(i)}</div>`).join('')}</details>`:'';}
    function dailyLink(row,data){
        if(!row.equipment_id)return esc(row.equipment_name);
        const params=new URLSearchParams({month:`${data.year}-${String(data.month).padStart(2,'0')}`,equipment_id:row.equipment_id,process_code:row.process_code,exclude_unplanned:data.exclude_unplanned});
        return `<a href="/production/equipment/daily?${esc(params.toString())}">${esc(row.equipment_name)}</a>`;
    }
    function productionCells(row){return `<td><strong>${percent(row.oee)}</strong></td><td>${percent(row.availability)}</td><td>${percent(row.performance)}</td><td>${percent(row.quality)}</td><td>${number(row.planned_qty)}</td><td>${number(row.total_qty)}</td><td>${number(row.good_qty)}</td><td>${percent(row.attainment)}</td><td>${number(row.defect_qty)}</td><td>${number(row.setup_qty)}</td><td>${percent(row.defect_rate)}</td>`;}
    function annual(data){
        $('eqMonthTitle').textContent=`${data.year}년 ${data.month}월 종합현황`;
        $('eqAnnualTitle').textContent=`${data.year}년 월별 추이`;
        const cards=[['설비 종합 효율',data.summary.oee],...data.process_summaries.map(r=>[r.name.replace(' 소계',''),r.oee]),['목표 달성률',data.summary.attainment],['불량률',data.summary.defect_rate]];
        $('eqCards').innerHTML=cards.map(([label,value])=>`<div class="eq-card"><div class="eq-card-label">${esc(label)}</div><div class="eq-card-value">${percent(value)}</div></div>`).join('');
        const maximum=Math.max(1,...data.months.map(m=>m.summary.oee||0));
        $('eqChart').innerHTML=data.months.map(m=>`<div class="eq-chart-column ${m.month===data.month?'selected':''}"><span>${percent(m.summary.oee)}</span><div class="eq-chart-track"><div class="eq-chart-bar" style="height:${Math.max(0,(m.summary.oee||0)/maximum*100)}%"></div></div><button type="button" class="eq-chart-label" data-month="${m.month}" aria-label="${m.month}월 현황 조회">${m.month}월</button></div>`).join('');
        $('eqChart').querySelectorAll('[data-month]').forEach(button=>button.onclick=()=>{$('eqMonth').value=button.dataset.month;load();});
        $('eqAnnualRows').innerHTML=[['OEE','oee'],['가동률','availability'],['성능효율','performance'],['양품률','quality']].map(([label,key])=>`<tr><th scope="row">${label}</th><td><strong>${percent(data.annual[key])}</strong></td>${data.months.map(m=>`<td>${percent(m.summary[key])}</td>`).join('')}</tr>`).join('');
        let rows='';
        for(const subtotal of data.process_summaries){
            for(const row of data.equipment.filter(r=>r.process_code===subtotal.process_code))rows+=`<tr><td>${esc(row.process_name)}</td><th scope="row">${dailyLink(row,data)}</th>${productionCells(row)}<td>${notes(row.issues)}</td></tr>`;
            rows+=`<tr class="eq-subtotal"><th colspan="2" scope="row">${esc(subtotal.name)}</th>${productionCells(subtotal)}<td></td></tr>`;
        }
        rows+=`<tr class="eq-total"><th colspan="2" scope="row">종합</th>${productionCells(data.summary)}<td></td></tr>`;
        $('eqEquipmentRows').innerHTML=rows;
    }
    function cycles(entries){return entries.length?entries.map(p=>`<div>${esc(p.part_no)}<br><strong>${p.cycle_seconds==null?'미등록':number(p.cycle_seconds)+'초'}</strong></div>`).join(''):'—';}
    function daily(data){
        $('eqDailyTitle').textContent=`${data.month.replace('-','년 ')}월 · ${data.equipment_name}`;
        const week=['월','화','수','목','금','토','일'];
        const head=`<tr><th scope="col">구분</th><th scope="col">월 종합</th>${data.days.map(d=>`<th scope="col" class="${d.weekday>4?'weekend':''}">${Number(d.work_date.slice(-2))}일<br>${week[d.weekday]}</th>`).join('')}</tr>`;
        $('eqDailyHead').innerHTML=head;$('eqDailyValuesHead').innerHTML=head;
        for(const table of root.querySelectorAll('.eq-daily')){
            table.querySelector('colgroup')?.remove();
            const columns=document.createElement('colgroup');
            columns.innerHTML=[130,90,...data.days.map(()=>72)].map(width=>`<col style="width:${width}px">`).join('');
            table.prepend(columns);table.style.width=(220+data.days.length*72)+'px';
        }
        function row(label,key,format,cellClass=''){
            return `<tr><th scope="row">${label}</th><td class="${cellClass}">${format(data.total[key])}</td>${data.days.map(d=>`<td class="${cellClass} ${d.has_activity?'':'inactive'}">${d.has_activity?format(d[key]):''}</td>`).join('')}</tr>`;
        }
        $('eqDailyRates').innerHTML=[['OEE','oee'],['가동률','availability'],['성능효율','performance'],['양품률','quality']].map(([label,key])=>row(label,key,percent)).join('');
        const times=[['생산계획시간','schedule_seconds'],['계획정지','planned_stop_seconds'],['기본시간','planned_seconds'],['비가동','downtime_seconds'],['실제 가동시간','runtime_seconds'],['표준생산시간','standard_production_seconds'],['양품 표준시간','standard_good_seconds'],['품질손실 표준시간','quality_loss_seconds']];
        const quantities=[['생산계획','planned_qty'],['총생산','total_qty'],['양품','good_qty'],['불량','defect_qty'],['셋업','setup_qty']];
        let values=times.map(([label,key])=>row(label+' (분)',key,minutes)).join('')+quantities.map(([label,key])=>row(label+' (EA)',key,number)).join('');
        values+=row('계획 달성률','attainment',percent)+row('불량률','defect_rate',percent)+row('품번별 C/T','cycles',cycles,'cycle-cell');
        if(data.days.some(d=>d.issues.length))values+=row('확인사항','issues',notes,'notes-cell');
        $('eqDailyValues').innerHTML=values;
        const params=new URLSearchParams({year:data.month.slice(0,4),month:Number(data.month.slice(-2)),process_code:data.process_code,exclude_unplanned:data.exclude_unplanned});
        $('eqEfficiencyLink').href='/production/equipment/efficiency?'+params;
    }
    async function load(){
        if(view==='daily'&&!$('eqEquipment').value)return message('등록된 복합선반·탭핑 설비가 없습니다.');
        const id=++request;$('eqLoad').disabled=true;message('조회 중입니다.');
        const params=view==='efficiency'?{year:$('eqYear').value,month:$('eqMonth').value,process_code:$('eqProcess').value}:{month:$('eqMonthDate').value,equipment_id:$('eqEquipment').value};
        params.exclude_unplanned=$('eqExclude').checked;
        try{
            const data=await api(view,params);if(id!==request)return;
            if(view==='efficiency')annual(data);else daily(data);
            const url=new URL(window.location.href);url.search=new URLSearchParams({...params,process_code:$('eqProcess').value});window.history.replaceState(null,'',url);
            message('조회 완료');
        }catch(error){if(id===request)message(error.message,true);}
        finally{if(id===request)$('eqLoad').disabled=false;}
    }
    function equipmentChoices(preferred){
        const equipments=options.equipments.filter(e=>!$('eqProcess').value||e.process_code===$('eqProcess').value);
        $('eqEquipment').innerHTML=equipments.map(e=>`<option value="${e.id}">${esc(e.name+(e.active?'':' (중지)'))}</option>`).join('');
        if(equipments.some(e=>String(e.id)===String(preferred)))$('eqEquipment').value=preferred;
        $('eqEquipment').disabled=!equipments.length;$('eqLoad').disabled=!equipments.length;
    }
    $('eqFilters').onsubmit=event=>{event.preventDefault();load();};
    $('eqPrint').onclick=()=>window.print();
    $('eqExclude').onchange=load;
    $('eqProcess').onchange=()=>{if(view==='daily')equipmentChoices($('eqEquipment').value);load();};
    async function init(){
        $('eqLoad').disabled=true;
        try{
            options=await api('report-options',{view});
            const params=new URLSearchParams(window.location.search);
            $('eqProcess').innerHTML='<option value="">전체</option>'+options.processes.map(p=>`<option value="${esc(p.process_code)}">${esc(p.process_name)}</option>`).join('');
            if(options.processes.some(p=>p.process_code===params.get('process_code')))$('eqProcess').value=params.get('process_code');
            $('eqExclude').checked=params.get('exclude_unplanned')==='true';
            if(view==='efficiency'){
                const year=Number(params.get('year'))||Number(current.slice(0,4));
                const years=[...new Set([...options.years,year])].filter(y=>y>=2000&&y<=2100).sort((a,b)=>b-a);
                $('eqYear').innerHTML=years.map(y=>`<option value="${y}">${y}년</option>`).join('');
                $('eqYear').value=years.includes(year)?year:years[0];
                const month=Number(params.get('month'))||Number(current.slice(5,7));$('eqMonth').value=month>=1&&month<=12?month:1;
                $('eqYear').onchange=load;$('eqMonth').onchange=load;
            }else{
                const month=params.get('month');$('eqMonthDate').value=/^\d{4}-(0[1-9]|1[0-2])$/.test(month||'')?month:current.slice(0,7);
                equipmentChoices(params.get('equipment_id'));
                $('eqMonthDate').onchange=load;$('eqEquipment').onchange=load;
                const scrolls=Array.from(root.querySelectorAll('.eq-scroll'));
                scrolls.forEach(scroll=>scroll.addEventListener('scroll',()=>{scrolls.forEach(other=>{if(other!==scroll&&other.scrollLeft!==scroll.scrollLeft)other.scrollLeft=scroll.scrollLeft;});},{passive:true}));
            }
            await load();
        }catch(error){message(error.message,true);}
    }
    init();
})();
