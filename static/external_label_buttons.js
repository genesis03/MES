/* External labels reuse the internal renderer and never generate a LOT. */
window.ExternalLabels = {
  button(row) {
    if (row.record_source !== 'EXTERNAL' && !('can_print_label' in row)) return '';
    const esc = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    if (!row.can_print_label) return `<button type="button" class="btn es-btn" disabled title="${esc(row.label_error)}">라벨 출력</button>`;
    return `<a class="btn es-btn" href="${esc(row.label_url)}" target="_blank" rel="noopener">라벨 출력</a>`;
  }
};
