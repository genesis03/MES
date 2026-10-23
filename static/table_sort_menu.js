/* Header context menu; the page supplies sorting over its complete query. */
(() => {
    'use strict';
    function attach(table, onSort, onSubtotal) {
        const headers = [...table.querySelectorAll('thead th[data-sort-key]')];
        const menu = document.createElement('div');
        menu.className = 'table-sort-menu';
        menu.setAttribute('role', 'menu');
        menu.setAttribute('aria-label', '열 정렬');
        menu.hidden = true;
        let selected = null;
        let criteria = [];
        const close = restore => {
            menu.hidden = true;
            if (restore && selected) selected.focus();
        };
        for (const [order, label] of [['asc','오름차순'],['desc','내림차순']]) {
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('role','menuitem');
            button.textContent = label;
            button.onclick = () => {
                const key = selected.dataset.sortKey;
                close(true);
                onSort(key, order);
            };
            menu.appendChild(button);
        }
        let subtotalButton;
        if(onSubtotal){
            subtotalButton=document.createElement('button');
            subtotalButton.type='button';subtotalButton.setAttribute('role','menuitemcheckbox');
            subtotalButton.setAttribute('aria-label','부분합 표시');
            subtotalButton.textContent='부분합 표시';
            subtotalButton.onclick=()=>{const key=selected.dataset.sortKey;const checked=!criteria.find(c=>c.key===key)?.subtotal;close(true);onSubtotal(key,checked);};
            menu.appendChild(subtotalButton);
        }
        document.body.appendChild(menu);
        function open(header, x, y) {
            selected = header;
            menu.hidden = false;
            menu.setAttribute('aria-label', header.dataset.sortLabel + ' 정렬');
            if(subtotalButton)subtotalButton.setAttribute('aria-checked',String(!!criteria.find(c=>c.key===header.dataset.sortKey)?.subtotal));
            menu.style.left = Math.max(4, Math.min(x, innerWidth-menu.offsetWidth-4)) + 'px';
            menu.style.top = Math.max(4, Math.min(y, innerHeight-menu.offsetHeight-4)) + 'px';
            menu.querySelector('button').focus();
        }
        headers.forEach(header => {
            header.tabIndex = 0;
            header.dataset.sortLabel = header.textContent.trim();
            header.title = '우클릭하여 오름차순·내림차순 정렬';
            header.setAttribute('aria-sort','none');
            const indicator = document.createElement('span');
            indicator.className = 'table-sort-indicator';
            indicator.setAttribute('aria-hidden','true');
            header.appendChild(indicator);
            header.oncontextmenu = event => {
                event.preventDefault();
                const rect = header.getBoundingClientRect();
                open(header, event.clientX || rect.left, event.clientY || rect.bottom);
            };
            header.onkeydown = event => {
                if (event.key === 'Enter' || event.key === ' ' || event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
                    event.preventDefault();
                    const rect = header.getBoundingClientRect();
                    open(header, rect.left, rect.bottom);
                }
            };
        });
        document.addEventListener('pointerdown', event => {if (!menu.contains(event.target)) close(false);});
        document.addEventListener('keydown', event => {if (event.key === 'Escape' && !menu.hidden) {event.preventDefault();close(true);}});
        menu.onkeydown = event => {
            const buttons = [...menu.querySelectorAll('button')];
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const step = event.key === 'ArrowDown' ? 1 : -1;
                buttons[(buttons.indexOf(document.activeElement)+step+buttons.length)%buttons.length].focus();
            } else if (event.key === 'Tab') close(false);
        };
        document.addEventListener('scroll', () => close(false), true);
        window.addEventListener('resize', () => close(false));
        function setSort(nextCriteria) {
            criteria=nextCriteria;
            headers.forEach(header => {
                const index=criteria.findIndex(c=>c.key===header.dataset.sortKey);
                const direction=index<0?'':criteria[index].order;
                header.setAttribute('aria-sort',index===0?(direction==='asc'?'ascending':'descending'):index>0?'other':'none');
                header.querySelector('.table-sort-indicator').textContent=index<0?'':` ${index+1}${direction==='asc'?'▲':'▼'}${criteria[index].subtotal?' Σ':''}`;
                header.title=index<0?'우클릭하여 정렬 기준 추가':`${index+1}차 ${direction==='asc'?'오름차순':'내림차순'} · 우클릭하여 방향 변경`;
            });
        }
        return {setSort,reset() {close(false);setSort([]);}};
    }
    window.MesTableSortMenu = Object.freeze({attach});
})();
