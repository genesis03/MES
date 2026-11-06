(() => {
    'use strict';
    const $=id=>document.getElementById(id);
    let sheets=[],active=null,fit=true;
    const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    function style(node,values){for(const [key,value] of Object.entries(values))node.style[key]=typeof value==='number'?`${value}px`:value;}
    function resize(){if(!active)return;const scale=fit?Math.min(1,Math.max(0.2,($('qualityFormViewport').clientWidth-42)/active.width)):1;$('qualityFormSheet').style.transform=`scale(${scale})`;style($('qualityFormSizer'),{width:active.width*scale,height:active.height*scale});}
    function show(name){
        active=sheets.find(sheet=>sheet.name===name);if(!active)return;
        const paper=$('qualityFormSheet');paper.replaceChildren();style(paper,{width:active.width,height:active.height});
        for(const cell of active.cells){const node=document.createElement('div');node.className='quality-form-cell';node.dataset.cell=cell.address;style(node,cell.style);const text=document.createElement('span');text.textContent=cell.text;node.append(text);paper.append(node);}
        for(const chart of active.charts){
            const node=document.createElement('div');node.className='quality-form-chart';style(node,{left:chart.left,top:chart.top,width:chart.width,height:chart.height});
            const title=esc(chart.title);const pie=/Pie|Doughnut/.test(chart.type);
            node.innerHTML=`<svg viewBox="0 0 600 280" preserveAspectRatio="none" aria-label="${title||'차트'} · 데이터 미연결"><text x="300" y="24" text-anchor="middle" font-size="16" font-family="맑은 고딕,sans-serif">${title}</text>${pie?'':Array.from({length:5},(_,i)=>`<line x1="48" y1="${55+i*40}" x2="570" y2="${55+i*40}" stroke="#d9d9d9" stroke-width="1"/>`).join('')+'<path d="M48 50 V235 H570" fill="none" stroke="#999"/>'}</svg>`;
            paper.append(node);
        }
        for(const drawing of active.drawings){const node=document.createElement('div');node.className='quality-form-drawing';style(node,{left:drawing.left,top:drawing.top,width:drawing.width,height:drawing.height,fontSize:drawing.fontSize});node.textContent=drawing.text;paper.append(node);}
        for(const button of $('qualityFormTabs').children)button.setAttribute('aria-selected',String(button.dataset.sheet===name));
        $('qualityFormStatus').textContent=`시트 ${sheets.indexOf(active)+1} / ${sheets.length} · ${name} · 양식 검토용`;
        resize();
    }
    $('qualityFormFit').onclick=()=>{fit=true;resize();};$('qualityFormActual').onclick=()=>{fit=false;resize();};
    $('qualityFormTabs').onclick=event=>{const button=event.target.closest('button[data-sheet]');if(button)show(button.dataset.sheet);};
    new ResizeObserver(resize).observe($('qualityFormViewport'));
    fetch('/static/quality_performance_form.json?v=20261106-1',{cache:'no-store'}).then(response=>{if(!response.ok)throw new Error('양식 파일을 불러오지 못했습니다.');return response.json();}).then(data=>{sheets=data.sheets;for(const sheet of sheets){const button=document.createElement('button');button.type='button';button.dataset.sheet=sheet.name;button.textContent=sheet.name;$('qualityFormTabs').append(button);}show('요약');}).catch(error=>{$('qualityFormStatus').textContent=error.message;});
})();
