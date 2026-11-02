/* Registered candidates keep their canonical part numbers; custom label names remain editable. */
(() => {
    const part=document.getElementById('inPartNo'),name=document.getElementById('inPartName');
    if(!part||!name)return;
    const box=document.createElement('div');box.id='manualPartCandidates';box.hidden=true;box.setAttribute('role','listbox');
    Object.assign(box.style,{position:'absolute',top:'100%',left:'0',minWidth:'100%',width:'330px',maxWidth:'min(330px,85vw)',maxHeight:'280px',overflowY:'auto',zIndex:'60',background:'#fff',border:'1px solid #cbd5e1',borderRadius:'5px',boxShadow:'0 4px 12px #0002'});
    part.parentElement.style.position='relative';part.parentElement.appendChild(box);
    part.setAttribute('aria-autocomplete','list');part.setAttribute('aria-controls',box.id);part.setAttribute('aria-expanded','false');
    let timer,version=0,nameVersion=0,autoName='',rows=[],total=0,active=-1,context=null;
    name.addEventListener('input',()=>nameVersion++);
    function close(){box.hidden=true;part.setAttribute('aria-expanded','false');active=-1;}
    function choose(row,editingVersion=nameVersion){
        part.value=row.part_no;version++;
        if(editingVersion===nameVersion)name.value=autoName=row.part_name;
        close();
    }
    function render(){
        box.replaceChildren();active=-1;
        rows.forEach((row,index)=>{
            const button=document.createElement('button');button.type='button';button.setAttribute('role','option');button.setAttribute('aria-selected','false');
            Object.assign(button.style,{display:'block',width:'100%',padding:'8px',textAlign:'left',border:'0',background:'#fff',cursor:'pointer'});
            button.textContent=row.part_no+' · '+row.part_name;
            button.onmousedown=e=>e.preventDefault();button.onclick=()=>choose(row,context?.editingVersion??nameVersion);box.appendChild(button);
        });
        if(total>rows.length){const note=document.createElement('p');note.style.padding='8px';note.textContent=`총 ${total}건 · 검색어를 더 입력하세요.`;box.appendChild(note);}
        box.hidden=!rows.length;part.setAttribute('aria-expanded',String(!box.hidden));
    }
    async function lookup(requestVersion,editingVersion,commit){
        const key=part.value.trim();if(!key){rows=[];close();return;}
        try{
            const response=await fetch('/api/manual/items?'+new URLSearchParams({q:key}));if(!response.ok)return;
            const data=await response.json();if(requestVersion!==version||key!==part.value.trim())return;
            rows=data.items||[];total=data.total??rows.length;context={key,editingVersion};
            const picked=MESPartSearch.pick(rows,key,total);
            if(commit&&picked){choose(picked,editingVersion);return;}
            render();
            const exact=rows.find(row=>row.part_no.toLowerCase()===key.toLowerCase());
            if(exact&&editingVersion===nameVersion)name.value=autoName=exact.part_name;
        }catch(_){close();}
    }
    function schedule(commit){
        clearTimeout(timer);const requestVersion=++version,editingVersion=nameVersion;
        if(autoName&&name.value===autoName)name.value='';autoName='';
        if(commit)return lookup(requestVersion,editingVersion,true);
        timer=setTimeout(()=>lookup(requestVersion,editingVersion,false),200);
    }
    part.addEventListener('input',()=>schedule(false));
    part.addEventListener('change',()=>schedule(true));
    part.addEventListener('keydown',event=>{
        if(event.key==='Escape'){close();return;}
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){
            if(box.hidden||!rows.length)return;event.preventDefault();
            active=Math.min(rows.length-1,Math.max(0,active+(event.key==='ArrowDown'?1:-1)));
            [...box.querySelectorAll('button')].forEach((button,index)=>{button.style.background=index===active?'#eff6ff':'#fff';button.setAttribute('aria-selected',String(index===active));if(index===active)button.scrollIntoView({block:'nearest'});});
        }
        if(event.key==='Enter'){
            event.preventDefault();
            if(active>=0&&!box.hidden)choose(rows[active],context?.editingVersion??nameVersion);else schedule(true);
        }
    });
    document.addEventListener('pointerdown',event=>{if(event.target!==part&&!box.contains(event.target))close();});
})();
