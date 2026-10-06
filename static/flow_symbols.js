(() => {
'use strict';
const shapes={
 CIRCLE:'<circle cx="16" cy="16" r="11"/>',
 ARROW:'<path d="M3 11H17V5L29 16L17 27V21H3Z"/>',
 SQUARE:'<rect x="5" y="5" width="22" height="22"/>',
 DIAMOND:'<path d="M16 3L29 16L16 29L3 16Z"/>',
 INVERTED_TRIANGLE:'<path d="M3 5H29L16 28Z"/>',
 DELAY:'<path d="M5 5H16A11 11 0 0 1 16 27H5Z"/>',
 // 복합기호는 주 기능을 바깥쪽, 보조 기능을 안쪽에 표시합니다.
 DIAMOND_SQUARE:'<path d="M16 3L29 16L16 29L3 16Z"/><rect x="9.5" y="9.5" width="13" height="13"/>',
 SQUARE_DIAMOND:'<rect x="5" y="5" width="22" height="22"/><path d="M16 5L27 16L16 27L5 16Z"/>',
 CIRCLE_SQUARE:'<circle cx="16" cy="16" r="12"/><rect x="8" y="8" width="16" height="16"/>',
 CIRCLE_ARROW:'<circle cx="16" cy="16" r="13"/><path d="M6 12H16V7L25 16L16 25V20H6Z"/>'
};
const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
window.MESFlowSymbols={shapes,render(shape,name){return shapes[shape]?'<svg class="flow-symbol" viewBox="0 0 32 32" role="img" aria-label="'+escape(name||'공정 기호')+'"><g fill="white" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round">'+shapes[shape]+'</g></svg>':'<span class="flow-symbol-empty" title="기호 미지정">—</span>';}};
})();
