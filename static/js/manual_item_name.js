/* Label names are editable snapshots; the item master remains unchanged. */
(() => {
    const part = document.getElementById('inPartNo');
    const name = document.getElementById('inPartName');
    if (!part || !name) return;
    let timer, version = 0, nameVersion = 0, autoName = '';
    name.addEventListener('input', () => { nameVersion++; });
    async function lookup(requestVersion, editingVersion) {
        const key = part.value.trim();
        if (!key) return;
        try {
            const response = await fetch('/api/manual/item?' + new URLSearchParams({part_no: key}));
            if (!response.ok) return;
            const data = await response.json();
            if (requestVersion !== version || key !== part.value.trim() || editingVersion !== nameVersion) return;
            if (data.part_name != null) name.value = autoName = data.part_name;
        } catch (_) { /* Manual entry remains available when lookup fails. */ }
    }
    function schedule(immediate) {
        clearTimeout(timer);
        const requestVersion = ++version, editingVersion = nameVersion;
        if (autoName && name.value === autoName) name.value = '';
        autoName = '';
        if (immediate) lookup(requestVersion, editingVersion);
        else timer = setTimeout(() => lookup(requestVersion, editingVersion), 200);
    }
    part.addEventListener('input', () => schedule(false));
    part.addEventListener('change', () => schedule(true));
})();
