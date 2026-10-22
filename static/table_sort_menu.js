/* Header context menu; the page supplies sorting over its complete query. */
(() => {
    'use strict';
    function attach(table, onSort) {
        const headers = [...table.querySelectorAll('thead th[data-sort-key]')];
        const menu = document.createElement('div');
        menu.className = 'table-sort-menu';
        menu.setAttribute('role', 'menu');
        menu.setAttribute('aria-label', '열 정렬');
        menu.hidden = true;
        let selected = null;
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
                headers.forEach(header => {
                    const active = header === selected;
                    header.setAttribute('aria-sort', active ? (order === 'asc' ? 'ascending' : 'descending') : 'none');
                    header.querySelector('.table-sort-indicator').textContent = active ? (order === 'asc' ? ' ▲' : ' ▼') : '';
                });
                onSort(key, order);
            };
            menu.appendChild(button);
        }
        document.body.appendChild(menu);
        function open(header, x, y) {
            selected = header;
            menu.hidden = false;
            menu.setAttribute('aria-label', header.dataset.sortLabel + ' 정렬');
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
        return {reset() {close(false);headers.forEach(h=>{h.setAttribute('aria-sort','none');h.querySelector('.table-sort-indicator').textContent='';});}};
    }
    window.MesTableSortMenu = Object.freeze({attach});
})();
