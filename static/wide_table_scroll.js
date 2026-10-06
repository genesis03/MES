(() => {
'use strict';
function attach(table){
 const target=table.parentElement;
 const nav=document.createElement('div');nav.className='wide-table-nav no-print';nav.hidden=true;
 const label=document.createElement('label');label.textContent='표 가로 이동';
 const first=document.createElement('button');first.type='button';first.textContent='처음';first.setAttribute('aria-label','표 왼쪽 끝 보기');
 const last=document.createElement('button');last.type='button';last.textContent='마지막';last.setAttribute('aria-label','표 오른쪽 끝 보기');
 const track=document.createElement('div');track.className='wide-table-track';track.tabIndex=0;track.setAttribute('role','region');track.setAttribute('aria-label','표 가로 이동 스크롤');
 const spacer=document.createElement('div');track.append(spacer);nav.append(label,first,track,last);document.body.append(nav);
 let scheduled=false;
 function update(){
  scheduled=false;
  const rect=target.getBoundingClientRect(),main=document.querySelector('.main-content')?.getBoundingClientRect();
  const left=Math.max(rect.left,main?.left||0,8),right=Math.min(rect.right,window.innerWidth-8);
  const visible=rect.bottom>80&&rect.top<window.innerHeight-55&&table.getClientRects().length&&target.scrollWidth>target.clientWidth+1&&right-left>180;
  nav.hidden=!visible;if(!visible)return;
  nav.style.left=left+'px';nav.style.width=(right-left)+'px';
  const max=target.scrollWidth-target.clientWidth;
  spacer.style.width=(track.clientWidth+max)+'px';
  if(Math.abs(track.scrollLeft-target.scrollLeft)>.5)track.scrollLeft=target.scrollLeft;
  first.disabled=target.scrollLeft<=0;last.disabled=target.scrollLeft>=max-1;
 }
 function schedule(){if(!scheduled){scheduled=true;requestAnimationFrame(update);}}
 track.addEventListener('scroll',()=>{if(Math.abs(target.scrollLeft-track.scrollLeft)>.5)target.scrollLeft=track.scrollLeft;});
 first.onclick=()=>{target.scrollLeft=0;schedule();};last.onclick=()=>{target.scrollLeft=target.scrollWidth;schedule();};
 document.addEventListener('scroll',schedule,true);window.addEventListener('resize',schedule);
 const observer=new ResizeObserver(schedule);observer.observe(target);observer.observe(table);
 schedule();
}
function init(){document.querySelectorAll('.pf-analysis,.cp-analysis').forEach(attach);}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
