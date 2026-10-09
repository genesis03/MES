/* One selection rule for registered part pickers. Ambiguous results stay open. */
(() => {
  const normalize = value => String(value || '').trim().toLowerCase();
  function pick(rows, query, total = rows.length) {
    const key = normalize(query);
    if (!key) return null;
    const exact = rows.filter(row => normalize(row.part_no) === key);
    if (exact.length === 1) return exact[0];
    return rows.length === 1 && Number(total) === 1 ? rows[0] : null;
  }
  window.MESPartSearch = Object.freeze({pick});
})();
