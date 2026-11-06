(() => {
    'use strict';
    const $=id=>document.getElementById(id);
    let sheets=[],active=null,fit=true,payload=null,generation=0;
    const base='/api/quality/performance';
    const colors=['#bfbfbf','#ffc000','#ff0000','#5b9bd5','#70ad47','#8064a2','#ed7d31'];
    const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const numeric=value=>typeof value==='number'&&Number.isFinite(value);
    const label=(value,percent=false)=>numeric(value)?(percent?(value*100).toLocaleString('ko-KR',{maximumFractionDigits:2})+'%':value.toLocaleString('ko-KR',{maximumFractionDigits:2})):'';
    function style(node,values){for(const [key,value] of Object.entries(values))node.style[key]=typeof value==='number'?`${value}px`:value;}
    function resize(){if(!active)return;const scale=fit?Math.min(1,Math.max(0.2,($('qualityFormViewport').clientWidth-42)/active.width)):1;$('qualityFormSheet').style.transform=`scale(${scale})`;style($('qualityFormSizer'),{width:active.width*scale,height:active.height*scale});}
    function chartSvg(chart,data){
        const series=data?.series||[];
        const title=`<text x="300" y="24" text-anchor="middle" font-size="16">${esc(chart.title)}</text>`;
        const pie=/pie|doughnut/i.test(chart.type);
        let drawing=title;
        if(pie){
            const item=series[0],values=item?.values||[],total=values.filter(numeric).reduce((sum,v)=>sum+Math.max(0,v),0);
            if(total>0){let angle=-Math.PI/2;values.forEach((value,index)=>{if(!numeric(value)||value<=0)return;const sweep=value/total*Math.PI*2;const end=angle+sweep;const x1=190+95*Math.cos(angle),y1=145+95*Math.sin(angle),x2=190+95*Math.cos(end),y2=145+95*Math.sin(end);const fill=colors[index%colors.length];const name=item.categories[index]||'';if(sweep>=Math.PI*2-1e-6)drawing+=`<circle cx="190" cy="145" r="95" fill="${fill}"><title>${esc(name)}: ${label(value)}</title></circle>`;else drawing+=`<path d="M190 145 L${x1} ${y1} A95 95 0 ${sweep>Math.PI?1:0} 1 ${x2} ${y2} Z" fill="${fill}" stroke="white"><title>${esc(name)}: ${label(value)} (${label(value/total,true)})</title></path>`;angle=end;});}
            else drawing+=`<text x="190" y="145" text-anchor="middle" font-size="16" fill="#64748b">${values.some(numeric)?'불량 없음':'미입력'}</text>`;
            (item?.categories||[]).forEach((name,index)=>{drawing+=`<rect x="330" y="${55+index*26}" width="12" height="12" fill="${colors[index%colors.length]}"/><text x="350" y="${66+index*26}" font-size="13">${esc(name||'')} ${label(values[index])}</text>`;});
        }else{
            const groups=new Map();
            series.forEach(item=>{const key=item.axis||'default';if(!groups.has(key))groups.set(key,{max:0,percent:item.percent});const group=groups.get(key);group.max=Math.max(group.max,...item.values.filter(numeric),0);});
            for(const group of groups.values()){const value=group.max||1;const power=10**Math.floor(Math.log10(value));group.max=Math.ceil(value/power*1.15)*power;}
            if(!groups.size)groups.set('default',{max:1,percent:false});
            const axes=[...groups.values()];
            for(let i=0;i<5;i++){const y=45+i*42;drawing+=`<line x1="60" y1="${y}" x2="540" y2="${y}" stroke="#d9d9d9"/><text x="55" y="${y+4}" text-anchor="end" font-size="11">${label(axes[0].max*(4-i)/4,axes[0].percent)}</text>`;if(axes.length>1)drawing+=`<text x="546" y="${y+4}" font-size="11">${label(axes[1].max*(4-i)/4,axes[1].percent)}</text>`;}
            const cats=series.find(item=>item.categories.length)?.categories||[];
            const count=Math.max(cats.length,...series.map(item=>item.values.length),1),step=480/count;
            const bars=series.filter(item=>/bar/i.test(item.kind));
            series.forEach((item,index)=>{const color=/^#[a-f\d]{6}$/i.test(item.color)?item.color:colors[index%colors.length];const max=groups.get(item.axis||'default').max;let points=[];let segments=[];
                item.values.forEach((value,i)=>{if(!numeric(value)){if(points.length)segments.push(points);points=[];return;}const x=60+(i+0.5)*step,y=213-value/max*168;const tip=`${item.name} · ${cats[i]||''}: ${label(value,item.percent)}`;
                    if(/bar/i.test(item.kind)){const width=step*.76/Math.max(bars.length,1);const bx=60+i*step+step*.12+bars.indexOf(item)*width;drawing+=`<rect x="${bx}" y="${Math.min(y,213)}" width="${Math.max(width-1,1)}" height="${Math.max(Math.abs(213-y),0)}" fill="${color}"><title>${esc(tip)}</title></rect>`;}
                    else {points.push(`${x},${y}`);drawing+=`<circle cx="${x}" cy="${y}" r="2.5" fill="${color}"><title>${esc(tip)}</title></circle>`;}
                });if(points.length)segments.push(points);for(const points of segments)drawing+=`<polyline points="${points.join(' ')}" fill="none" stroke="${color}" stroke-width="2"/>`;
                const lx=60+(index%4)*125,ly=255+Math.floor(index/4)*18;drawing+=`<rect x="${lx}" y="${ly-9}" width="10" height="10" fill="${color}"/><text x="${lx+14}" y="${ly}" font-size="11">${esc(item.name.slice(0,12))}</text>`;
            });
            cats.forEach((name,index)=>{drawing+=`<text x="${60+(index+.5)*step}" y="233" text-anchor="middle" font-size="11"><title>${esc(name||'')}</title>${esc(String(name||'').slice(0,count>8?4:9))}</text>`;});
        }
        return `<svg viewBox="0 0 600 295" preserveAspectRatio="none" role="img" aria-label="${esc(chart.title||'실적 차트')}" style="font-family:맑은 고딕,sans-serif">${drawing}</svg>`;
    }
    function show(name){
        active=sheets.find(sheet=>sheet.name===name);if(!active)return;
        const paper=$('qualityFormSheet');paper.replaceChildren();style(paper,{width:active.width,height:active.height});
        for(const cell of active.cells){const node=document.createElement('div');node.className='quality-form-cell';node.dataset.cell=cell.address;style(node,cell.style);const value=payload?.cells?.[name]?.[cell.address];if(value?.numeric&&node.style.textAlign==='left')node.style.textAlign='right';if(value?.color)node.style.color=value.color;const text=document.createElement('span');text.textContent=value?value.text:cell.text;node.append(text);paper.append(node);}
        for(const chart of active.charts){const node=document.createElement('div');node.className='quality-form-chart';style(node,{left:chart.left,top:chart.top,width:chart.width,height:chart.height});node.innerHTML=chartSvg(chart,payload?.charts?.[chart.part]);
            const svg=node.querySelector('svg'),xs=chart.width/600,ys=chart.height/295;
            svg.setAttribute('viewBox',`0 0 ${chart.width} ${chart.height}`);
            for(const element of svg.children){if(element.tagName==='text'){element.setAttribute('x',Number(element.getAttribute('x'))*xs);element.setAttribute('y',Number(element.getAttribute('y'))*ys);}else element.setAttribute('transform',`scale(${xs} ${ys})`);}
            paper.append(node);}
        active.drawings.forEach((drawing,index)=>{const node=document.createElement('div');node.className='quality-form-drawing';style(node,{left:drawing.left,top:drawing.top,width:drawing.width,height:drawing.height,fontSize:drawing.fontSize});node.textContent=payload?.drawings?.[name]?.[index]??drawing.text;paper.append(node);});
        for(const button of $('qualityFormTabs').children)button.setAttribute('aria-selected',String(button.dataset.sheet===name));
        resize();
    }
    function query(){return new URLSearchParams({month:$('qualityFormMonth').value,source:$('qualityFormSource').value});}
    async function load(){
        const current=++generation,name=active?.name||'요약',params=query();payload=null;show(name);$('qualityFormExcel').disabled=true;$('qualityFormLoad').disabled=true;$('qualityFormIssues').hidden=true;$('qualityFormStatus').textContent='실적을 집계하고 있습니다.';
        try{const response=await fetch(`${base}/form?${params}`,{cache:'no-store',headers:{'X-MES-Menu-Path':'/quality/performance'}});if(response.status===404)throw new Error('품질실적 API가 등록되지 않았습니다. 업데이트한 서버의 실행 상태를 확인해야 합니다.');const result=await MesResponse.read(response);if(current!==generation)return;payload=result;show(active?.name||name);$('qualityFormStatus').textContent=`${result.month} · ${result.source==='EXTERNAL'?'외부 연동':'MES 입력'} · 조회 월까지의 월별 실적 · 수기 미입력 항목은 빈칸`;$('qualityFormExcel').disabled=false;
            $('qualityFormReference').textContent=result.reference_note||'';const list=$('qualityFormIssues').querySelector('ul');list.replaceChildren();for(const issue of result.issues){const li=document.createElement('li');li.textContent=issue;list.append(li);}$('qualityFormIssues').hidden=!result.issues.length&&!result.reference_note;
        }catch(error){if(current===generation)$('qualityFormStatus').textContent=error.message;}finally{if(current===generation)$('qualityFormLoad').disabled=false;}
    }
    $('qualityFormFit').onclick=()=>{fit=true;resize();};$('qualityFormActual').onclick=()=>{fit=false;resize();};
    $('qualityFormTabs').onclick=event=>{const button=event.target.closest('button[data-sheet]');if(button)show(button.dataset.sheet);};
    $('qualityFormLoad').onclick=load;
    $('qualityFormMonth').onchange=()=>{if($('qualityFormMonth').value)load();};$('qualityFormSource').onchange=load;
    $('qualityFormExcel').onclick=()=>{if(payload)location.href=`${base}/excel?${new URLSearchParams({month:payload.month,source:payload.source})}`;};
    new ResizeObserver(resize).observe($('qualityFormViewport'));
    if(!$('qualityFormMonth').value){const date=new Date();date.setDate(1);date.setMonth(date.getMonth()-1);const month=`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}`;$('qualityFormMonth').value=month;$('qualityFormMonth').max=month;}
    fetch('/static/quality_performance_form.json?v=20261106-2',{cache:'no-store'}).then(response=>{if(!response.ok)throw new Error('양식 파일을 불러오지 못했습니다.');return response.json();}).then(data=>{sheets=data.sheets;for(const sheet of sheets){const button=document.createElement('button');button.type='button';button.dataset.sheet=sheet.name;button.textContent=sheet.name;$('qualityFormTabs').append(button);}show('요약');return load();}).catch(error=>{$('qualityFormStatus').textContent=error.message;});
})();
