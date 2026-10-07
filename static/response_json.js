/* Shared JSON response handling. Fetch options and authorization stay with callers. */
(() => {
    'use strict';
    function detailText(detail) {
        if (typeof detail === 'string') return detail;
        if (Array.isArray(detail)) return detail.map(row => row.msg || '입력값을 확인해 주세요.').join('\n');
        if (detail && typeof detail === 'object') {
            const errors = Array.isArray(detail.errors) ? detail.errors.map(row =>
                [row.cell, row.message || row.msg].filter(Boolean).join(': ')) : [];
            return [detail.message, ...errors].filter(Boolean).join('\n');
        }
        return '';
    }
    async function read(response) {
        if (response.status === 204 && response.ok) return null;
        const body = await response.text();
        let data, valid = false;
        try { data = JSON.parse(body); valid = true; } catch (_) {}
        const loginRedirect = response.redirected && /\/login\/?$/.test(new URL(response.url, location.origin).pathname);
        if (loginRedirect || response.status === 401) {
            const error = new Error('로그인 세션이 만료되었습니다. 다시 로그인해 주세요.');
            error.status = response.status;
            throw error;
        }
        if (!response.ok || !valid) {
            const reason = detailText(data?.detail) || detailText(data?.message) ||
                (!response.ok ? '요청을 처리하지 못했습니다.' : '서버 응답 형식이 올바르지 않습니다.');
            const error = new Error(reason + ' (HTTP ' + response.status + ')');
            error.status = response.status;
            error.details = data?.detail;
            throw error;
        }
        return data;
    }
    window.MesResponse = Object.freeze({read, detailText});
})();
