(() => {
    'use strict';
    // Combined inquiry screens and document pickers need every matching page.
    async function all(url, options = {}) {
        const target = new URL(url, location.origin);
        target.searchParams.set('limit', '500');
        const items = [];
        let total = 0;
        do {
            target.searchParams.set('offset', String(items.length));
            const data = await MesResponse.read(await fetch(target, options));
            if (!Array.isArray(data.items) || !Number.isInteger(data.total) || data.total < 0) {
                throw new Error('조회 응답 형식이 올바르지 않습니다.');
            }
            total = data.total;
            if (!data.items.length) {
                if (items.length < total) throw new Error('조회 중 데이터가 변경되었습니다. 다시 조회해 주세요.');
                break;
            }
            items.push(...data.items);
        } while (items.length < total);
        return {items, total};
    }
    window.MesPurchaseQuery = Object.freeze({all});
})();
