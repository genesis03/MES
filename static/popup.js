/* Adapt existing menus to one popup shell without replacing business handlers. */
(() => {
    'use strict';
    const overlays='.mes-popup-backdrop,.modal-overlay,.modal-backdrop,.lookup-backdrop,.modal,.adj-modal,.mv-modal,.pd-popup,.qi-popup';
    const headSelector='.mes-popup-head,.modal-header,.modal-head,.lookup-head,.po-modal-head,.so-modal-head,.ob-modal-head,.si-modal-head,.adj-head,.mv-head,.pd-popup-head,.qi-popup-head,.cp-item-dialog-head,header';
    const bodySelector='.mes-popup-body,.modal-body,.lookup-body,.po-modal-body,.so-modal-body,.ob-modal-body,.si-modal-body,.pd-popup-body,.qi-popup-body,.cp-item-dialog-body,.adj-form,.mv-form';
    const footSelector='.mes-popup-foot,.modal-footer,.modal-foot,.lookup-foot,.po-modal-foot,.so-modal-foot,.ob-modal-foot,.si-modal-foot,.adj-foot,.mv-foot,.pd-popup-foot,.qi-popup-foot,.po-popup-footer,.mes-revision-actions';
    const filterSelector='.lookup-filters,.po-search-grid,.so-search-grid,.ob-search,.ob-history-filters,.si-search,.pd-search,.qi-popup-filter,#esItemSearch,.cp-item-dialog-search';
    const records=new Map();let scheduled=false,counter=0,order=0;
    function template(){return document.getElementById('mesPopupTemplate');}
    function part(selector){return template().content.querySelector(selector).cloneNode(true);}
    function closingButton(button){
        if(button.dataset.mesPopupKeep!==undefined||button.type==='reset')return false;
        const handler=button.getAttribute('onclick')||'';
        if(/reset|restore|revert|undo/i.test(handler))return false;
        return /^(닫기|취소|×|✕|x|close|cancel)$/i.test(button.textContent.trim())||button.getAttribute('aria-label')==='닫기';
    }
    function rootFor(panel){
        const first=panel.firstElementChild;
        return first&&(first.tagName==='FORM'&&panel.children.length===1||first.classList.contains('po-modal-shell')||first.classList.contains('mes-popup-layout'))?first:panel;
    }
    function close(record){
        if(record.originalClose){if(!record.originalClose.disabled)record.originalClose.click();return;}
        if(record.native){record.panel.close('');return;}
        // Legacy overlays use either an inline display toggle or a .show class.
        if(record.overlay.classList.contains('show'))record.overlay.classList.remove('show');
        else if(record.overlay.hasAttribute('hidden')||record.usesHidden)record.overlay.hidden=true;
        else record.overlay.style.display='none';
    }
    function prepare(panel,overlay=panel){
        if(records.has(panel)||panel.dataset.mesPopupSkip!==undefined||!template())return;
        const native=panel.tagName==='DIALOG',root=rootFor(panel);
        const originalStyle=getComputedStyle(panel),width=panel.style.width||originalStyle.width;
        let size=width&&width!=='auto'&&width!=='0px'?width:'1000px';
        if(originalStyle.maxWidth!=='none'&&originalStyle.maxWidth!=='0px')size=`min(${size},${originalStyle.maxWidth})`;
        panel.style.setProperty('--mes-popup-width',size);
        panel.classList.add('mes-popup');panel.dataset.mesPopup='';
        if(root!==panel)root.classList.add(root.tagName==='FORM'?'mes-popup-form':'mes-popup-layout');
        if(!native){overlay.classList.add('mes-popup-backdrop');panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');}
        let head=root.querySelector(headSelector);
        if(!head){
            const title=root.querySelector(':scope > h2,:scope > h3,:scope > h4,:scope > .modal-title');
            head=part('.mes-popup-head');head.querySelector('.mes-popup-close').remove();
            if(title)head.querySelector('.mes-popup-title').replaceWith(title);
            else head.querySelector('.mes-popup-title').textContent=panel.getAttribute('aria-label')||'조회 / 입력';
            root.prepend(head);
        }
        head.classList.add('mes-popup-head');
        let title=head.querySelector('strong,h2,h3,h4,.mes-popup-title,span');
        if(title){title.classList.add('mes-popup-title');if(!title.id)title.id='mes-popup-title-'+(++counter);panel.setAttribute('aria-labelledby',title.id);}
        const buttons=Array.from(panel.querySelectorAll('button'));
        const closeButtons=buttons.filter(closingButton);
        const headerClose=closeButtons.find(b=>head.contains(b));
        let foot=root.querySelector(footSelector);
        if(!foot){
            const source=closeButtons.find(b=>!head.contains(b));
            const parent=source?.parentElement;
            if(parent&&parent!==root&&!parent.querySelector('input,select,textarea')&&parent.tagName!=='FORM')foot=parent;
            else foot=part('.mes-popup-foot');
            if(!foot.isConnected)root.append(foot);
        }
        foot.classList.add('mes-popup-foot');
        let body=root.querySelector(bodySelector);
        if(!body){
            body=part('.mes-popup-body');
            for(const child of Array.from(root.childNodes))if(child!==head&&child!==foot)body.append(child);
            head.after(body);
        }
        body.classList.add('mes-popup-body');
        // Keep a footer inside its original form, preserving submit semantics.
        if(foot.closest('form')===root.closest('form')&&body.contains(foot))root.append(foot);
        for(const form of panel.querySelectorAll('form'))if(form.contains(body)||form.contains(foot))form.classList.add('mes-popup-form');
        // The time picker has action buttons in its filter toolbar.
        const timeApply=panel.querySelector('#ppTimeApply');
        if(timeApply)foot.append(timeApply);
        const originalClose=headerClose||closeButtons[0]||null;
        const record={panel,overlay,native,head,body,foot,originalClose,usesHidden:overlay.hasAttribute('hidden'),visible:false,order:0};
        let control=headerClose;
        if(!control){control=part('.mes-popup-close');control.dataset.mesPopupGeneratedClose='';control.addEventListener('click',()=>close(record));head.append(control);}
        control.classList.add('mes-popup-close');control.type='button';control.textContent='닫기';control.setAttribute('aria-label','닫기');
        for(const button of closeButtons)if(button!==control){button.dataset.mesPopupDuplicateClose='';button.setAttribute('aria-hidden','true');button.tabIndex=-1;}
        for(const button of foot.querySelectorAll('button'))if(/저장|등록|적용|선택|확인|불러오기|추가|개정 생성/.test(button.textContent)&&!closingButton(button))button.dataset.mesPopupPrimary='';
        const visibleActions=Array.from(foot.querySelectorAll('button,input,select')).some(n=>n.dataset.mesPopupDuplicateClose===undefined);
        if(!visibleActions&&!foot.textContent.trim())foot.dataset.mesPopupEmpty='';
        if(Array.from(foot.children).every(n=>n.dataset.mesPopupDuplicateClose!==undefined))foot.dataset.mesPopupEmpty='';
        for(const filter of panel.querySelectorAll(filterSelector))filter.classList.add('mes-popup-filters');
        for(const table of panel.querySelectorAll('table'))if(table.querySelector('thead'))table.classList.add('mes-popup-table');
        records.set(panel,record);drag(record);
    }
    function clamp(record,x,y){
        const rect=record.panel.getBoundingClientRect();
        record.panel.dataset.mesPopupDragged='';
        record.panel.style.setProperty('--mes-popup-x',Math.max(0,Math.min(x,innerWidth-rect.width))+'px');
        record.panel.style.setProperty('--mes-popup-y',Math.max(0,Math.min(y,innerHeight-rect.height))+'px');
    }
    function drag(record){
        let start=null;
        record.head.addEventListener('pointerdown',event=>{
            if(event.button!==0||event.target.closest('button,input,select,textarea,a,[contenteditable]'))return;
            const rect=record.panel.getBoundingClientRect();start={x:event.clientX,y:event.clientY,left:rect.left,top:rect.top};
            record.head.setPointerCapture(event.pointerId);event.preventDefault();
        });
        record.head.addEventListener('pointermove',event=>{if(start)clamp(record,start.left+event.clientX-start.x,start.top+event.clientY-start.y);});
        for(const type of ['pointerup','pointercancel','lostpointercapture'])record.head.addEventListener(type,()=>{start=null;});
    }
    function refresh(){
        scheduled=false;
        for(const dialog of document.querySelectorAll('dialog'))prepare(dialog);
        for(const overlay of document.querySelectorAll(overlays)){
            if(overlay.tagName==='DIALOG'||overlay.closest('dialog')||overlay.dataset.mesPopup!==undefined)continue;
            const panel=overlay.firstElementChild;
            if(panel)prepare(panel,overlay);
        }
        for(const [panel,record] of records){
            if(!panel.isConnected){records.delete(panel);continue;}
            const visible=(!record.native||panel.open)&&panel.getClientRects().length>0&&getComputedStyle(record.overlay).display!=='none'&&!record.overlay.hidden;
            if(visible&&!record.visible){
                delete panel.dataset.mesPopupDragged;record.order=++order;record.previousFocus=document.activeElement;
                if(!panel.contains(document.activeElement))(record.body.querySelector('input:not([type=hidden]),select,textarea')||record.head.querySelector('.mes-popup-close'))?.focus({preventScroll:true});
            }
            if(!visible&&record.visible&&record.previousFocus?.isConnected&&(panel.contains(document.activeElement)||document.activeElement===document.body))record.previousFocus.focus({preventScroll:true});
            record.visible=visible;
        }
    }
    function schedule(){if(!scheduled){scheduled=true;requestAnimationFrame(refresh);}}
    function topPopup(){return Array.from(records.values()).filter(r=>r.visible).sort((a,b)=>Number(b.native)-Number(a.native)||b.order-a.order)[0];}
    function start(){
        refresh();
        new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['open','hidden','class','style']});
        window.addEventListener('resize',()=>{for(const r of records.values())if(r.visible&&r.panel.dataset.mesPopupDragged!==undefined){const rect=r.panel.getBoundingClientRect();clamp(r,rect.left,rect.top);}});
        document.addEventListener('keydown',event=>{
            const record=topPopup();if(!record||record.native||event.defaultPrevented)return;
            if(event.key==='Escape'){event.preventDefault();close(record);return;}
            if(event.key!=='Tab')return;
            const controls=Array.from(record.panel.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')).filter(n=>!n.disabled&&n.tabIndex>=0&&n.getClientRects().length);
            const first=controls[0],last=controls.at(-1);
            if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
            else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
        });
    }
    function create({title,width='600px',content,buttons=[]}={}){
        const dialog=document.createElement('dialog');dialog.style.width=typeof width==='number'?width+'px':width;
        dialog.append(template().content.cloneNode(true));
        dialog.querySelector('.mes-popup-title').textContent=title||'';
        const body=dialog.querySelector('.mes-popup-body'),footer=dialog.querySelector('.mes-popup-foot');
        if(content instanceof Node)body.append(content);else if(content!=null)body.textContent=String(content);
        for(const action of buttons){const button=document.createElement('button');button.type='button';button.textContent=action.label;if(action.primary)button.dataset.mesPopupPrimary='';button.addEventListener('click',action.onClick);footer.append(button);}
        // A new shell has no legacy handler for its upper close button.
        dialog.querySelector('.mes-popup-close').addEventListener('click',()=>dialog.close(''));
        document.body.append(dialog);prepare(dialog);
        return {element:dialog,body,footer,show:()=>dialog.showModal(),close:()=>dialog.close('')};
    }
    window.MESPopup=Object.freeze({create,refresh:schedule});
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
