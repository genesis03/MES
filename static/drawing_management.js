(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const state = {items: [], revisions: [], item: null, revision: null, options: null, previousId: null,
        canWrite: $('drawingApp').dataset.canWrite === 'true', busy: false, itemRequest: 0, documentRequest: 0};
    const statusName = {DRAFT: '초안', CURRENT: '현재 사용', SUPERSEDED: '구버전', RETIRED: '폐기'};
    const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    // 외부 아이콘 라이브러리 없이 기존 링크 색상을 따르는 SVG 아이콘입니다.
    const viewIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M15 3h6v6M21 3l-9 9M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/></svg>';
    const downloadIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 3v12M7 10l5 5 5-5M5 16v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3"/></svg>';

    function message(text = '', error = false) {
        $('drawingMessage').textContent = text;
        $('drawingMessage').classList.toggle('error', error);
    }

    async function api(url, init = {}) {
        const headers = new Headers(init.headers || {});
        headers.set('X-MES-Menu-Path', '/basic-info/drawings');
        const response = await fetch(url, {...init, headers});
        if (!response.ok) {
            let detail;
            try { detail = (await response.json()).detail; } catch { detail = '서버 응답을 확인할 수 없습니다.'; }
            if (Array.isArray(detail)) detail = detail.map(row => row.msg).join(', ');
            throw new Error(detail || `요청 실패 (${response.status})`);
        }
        return response.json();
    }

    function jsonPost(url, body) {
        return api(url, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
    }

    function updateActions() {
        const writable = state.canWrite && !state.busy && state.item?.is_active === 'Y';
        document.querySelectorAll('#drawingApp [data-write]').forEach(button => { button.disabled = !writable; });
        $('drawingNewRevision').hidden = state.revisions.length > 0;
        $('drawingNewRevision').disabled = !writable || !state.item;
        $('drawingRevise').disabled = !writable || !state.revision || state.revision.status === 'RETIRED';
        $('drawingActivate').disabled = !writable || state.revision?.status !== 'DRAFT';
        // 폐기는 사용중지 품목에서도 허용합니다.
        $('drawingRetire').disabled = !state.canWrite || state.busy || !state.revision || state.revision.status === 'RETIRED';
        $('drawingUpload').hidden = !state.canWrite || state.item?.is_active !== 'Y' || state.revision?.status !== 'DRAFT';
    }

    async function run(operation) {
        if (state.busy) return;
        state.busy = true;
        updateActions();
        try { await operation(); } catch (error) { message(error.message, true); }
        finally { state.busy = false; updateActions(); }
    }

    function renderItems() {
        $('drawingItemCount').textContent = `${state.items.length}건`;
        $('drawingItemRows').innerHTML = state.items.length ? state.items.map(item => `
            <tr class="${state.item?.item_id === item.item_id ? 'selected' : ''}"><td>
                <button type="button" class="drawing-item-button" data-item-id="${item.item_id}">${escape(item.part_no)}
                <span class="drawing-item-name">${escape(item.part_name)}${item.is_active !== 'Y' ? ' · 사용중지' : ''}</span></button></td>
                <td>${item.drawing_count ? `<span class="drawing-badge current">${escape(item.current_revision)}</span><small> ${item.drawing_count}건</small>` : '없음'}</td></tr>`).join('')
            : '<tr><td colspan="2">조회된 품목이 없습니다.</td></tr>';
    }

    async function loadItems(preferredId = state.item?.item_id) {
        const params = new URLSearchParams({keyword: $('drawingKeyword').value.trim(), drawing_no: $('drawingNoSearch').value.trim(), drawing_state: $('drawingState').value});
        state.items = await api(`/api/documents/items?${params}`);
        renderItems();
        const selected = state.items.find(row => row.item_id === Number(preferredId)) || state.items[0];
        if (selected) await selectItem(selected.item_id);
        else {
            state.itemRequest++; state.documentRequest++;
            state.item = null; state.revision = null; state.revisions = [];
            $('drawingDetail').hidden = true; $('drawingEmpty').hidden = false; updateActions();
        }
    }

    async function selectItem(itemId) {
        const request = ++state.itemRequest;
        state.documentRequest++;
        state.item = state.items.find(row => row.item_id === itemId);
        state.revision = null;
        state.revisions = [];
        $('drawingDocuments').innerHTML = '';
        $('drawingUpload').reset();
        $('drawingDetail').hidden = false; $('drawingEmpty').hidden = true;
        $('drawingItemTitle').textContent = `${state.item.part_no} · ${state.item.part_name}`;
        $('drawingItemMeta').textContent = `품목 ID ${itemId} · ${state.item.current_revision ? `현재 사용 ${state.item.current_revision}` : '현재 사용 Revision 없음'}${state.item.is_active !== 'Y' ? ' · 사용중지' : ''}`;
        renderItems(); updateActions();
        const rows = await api(`/api/documents/items/${itemId}/revisions`);
        if (request !== state.itemRequest) return;
        state.revisions = rows;
        $('drawingRevision').innerHTML = rows.length ? rows.map(row => `<option value="${row.id}">${escape(row.revision_code)} · ${statusName[row.status]}</option>`).join('') : '<option value="">등록 이력 없음</option>';
        const selected = rows.find(row => row.status === 'CURRENT') || rows[0];
        if (selected) {
            $('drawingRevision').value = String(selected.id);
            await selectRevision(selected.id);
        } else {
            $('drawingRevisionStatus').textContent = '미등록';
            $('drawingRevisionStatus').classList.remove('current');
            $('drawingRevisionMeta').textContent = '최초 등록으로 Revision 초안을 생성해 주세요.';
            updateActions();
        }
        const url = new URL(window.location.href);
        url.searchParams.set('item_id', itemId);
        window.history.replaceState(null, '', url);
    }

    async function selectRevision(revisionId) {
        const request = ++state.documentRequest;
        state.revision = state.revisions.find(row => row.id === Number(revisionId));
        if (!state.revision) return;
        const row = state.revision;
        $('drawingRevisionStatus').textContent = statusName[row.status];
        $('drawingRevisionStatus').classList.toggle('current', row.status === 'CURRENT');
        $('drawingRevisionMeta').textContent = `등록 ${row.created_by} · ${row.created_at}${row.activated_at ? ` / 적용 ${row.activated_at}` : ''}${row.change_reason ? ` / 개정 사유: ${row.change_reason}` : ''}${row.retire_reason ? ` / 폐기 사유: ${row.retire_reason}` : ''}${row.note ? ` / 비고: ${row.note}` : ''}`;
        $('drawingDocuments').innerHTML = '<p class="drawing-muted">도면을 조회 중입니다…</p>';
        $('drawingUpload').reset(); updateActions();
        const documents = await api(`/api/documents/revisions/${revisionId}/documents`);
        if (request !== state.documentRequest) return;
        $('drawingDocuments').innerHTML = documents.length ? documents.map(document => `
            <article class="drawing-document ${document.retired_at ? 'retired' : ''}">
                <h3>${escape(document.title)} ${document.retired_at ? '<span class="drawing-badge">폐기</span>' : ''}</h3>
                <p class="drawing-muted">${escape(document.document_type_name)} · 도면번호 ${escape(document.document_no || '미입력')} · 문서 REV ${escape(document.document_revision || '미입력')}</p>
                <p class="drawing-muted">등록 ${escape(document.created_by)} · ${escape(document.created_at)}${document.note ? ` · ${escape(document.note)}` : ''}${document.retire_reason ? ` · 폐기 사유: ${escape(document.retire_reason)}` : ''}</p>
                ${document.files.map(file => `<div class="drawing-file"><span class="drawing-file-name">${escape(file.original_name)}<br><small>${(file.size_bytes / 1024).toFixed(1)} KB · ${escape(file.extension.toUpperCase())}</small></span>
                    ${file.can_preview ? `<a class="drawing-icon-link" href="${escape(file.preview_url)}" target="_blank" rel="noopener noreferrer" title="도면 보기" aria-label="도면 보기">${viewIcon}</a>` : ''}
                    <a class="drawing-icon-link" href="${escape(file.download_url)}" title="다운로드" aria-label="다운로드">${downloadIcon}</a></div>`).join('')}
                ${row.status === 'DRAFT' && !document.retired_at && state.canWrite ? `<button type="button" class="danger" data-write data-retire-document="${document.id}">문서 폐기</button>` : ''}
            </article>`).join('') : '<p class="drawing-muted">등록된 도면이 없습니다.</p>';
        updateActions();
    }

    function openRevision(previous = null) {
        state.previousId = previous?.id || null;
        $('drawingRevisionDialogTitle').textContent = previous ? '개정 등록' : '최초 Revision 등록';
        $('drawingRevisionForm').reset();
        $('drawingRevisionCode').value = previous ? '' : state.item.master_revision;
        $('drawingChangeReason').required = !!previous;
        $('drawingRevisionDialog').showModal();
        $('drawingRevisionCode').focus();
    }

    $('drawingSearch').addEventListener('submit', event => { event.preventDefault(); run(() => loadItems()); });
    $('drawingReset').addEventListener('click', () => run(async () => { $('drawingSearch').reset(); message(); await loadItems(); }));
    $('drawingItemRows').addEventListener('click', event => {
        const button = event.target.closest('[data-item-id]');
        if (button && !state.busy) selectItem(Number(button.dataset.itemId)).catch(error => message(error.message, true));
    });
    $('drawingRevision').addEventListener('change', event => {
        if (!state.busy) selectRevision(Number(event.target.value)).catch(error => message(error.message, true));
    });
    $('drawingNewRevision').addEventListener('click', () => openRevision());
    $('drawingRevise').addEventListener('click', () => openRevision(state.revision));
    $('drawingRevisionCancel').addEventListener('click', () => $('drawingRevisionDialog').close());
    $('drawingRevisionForm').addEventListener('submit', event => {
        event.preventDefault();
        run(async () => {
            const row = await jsonPost(`/api/documents/items/${state.item.item_id}/revisions`, {
                revision_code: $('drawingRevisionCode').value.trim(), previous_revision_id: state.previousId,
                change_reason: $('drawingChangeReason').value.trim(), note: $('drawingRevisionNote').value.trim()});
            $('drawingRevisionDialog').close();
            await loadItems();
            $('drawingRevision').value = String(row.id);
            await selectRevision(row.id);
            message('Revision 초안을 생성했습니다. 이번 Revision에 사용할 도면 파일을 등록해 주세요.');
        });
    });
    $('drawingUpload').addEventListener('submit', event => {
        event.preventDefault();
        run(async () => {
            const selected = Array.from($('drawingFiles').files);
            if (!selected.length || selected.length > state.options.max_files) throw new Error(`파일은 1~${state.options.max_files}개 선택해 주세요.`);
            if (selected.some(file => file.size > state.options.max_file_bytes)) throw new Error('파일 크기 제한을 초과했습니다.');
            const revisionId = state.revision.id;
            const body = new FormData($('drawingUpload'));
            body.set('document_type', state.options.drawing_type);
            await api(`/api/documents/revisions/${revisionId}/documents`, {method: 'POST', body});
            await selectRevision(revisionId);
            message('도면 초안을 저장했습니다. 확인 후 현재 사용 적용을 실행해 주세요.');
        });
    });
    $('drawingActivate').addEventListener('click', () => {
        if (!confirm(`[${state.revision.revision_code}]을 현재 사용으로 적용하시겠습니까? 기존 현재 Revision은 구버전으로 전환됩니다.`)) return;
        run(async () => { await jsonPost(`/api/documents/revisions/${state.revision.id}/activate`, {}); await loadItems(); message('현재 사용 도면을 적용했습니다. 품목마스터 Revision도 갱신했습니다.'); });
    });
    $('drawingRetire').addEventListener('click', () => {
        const reason = prompt('폐기 사유를 입력해 주세요. 이력과 파일은 보존됩니다. 현재 사용 Revision을 폐기하면 사용 도면이 없는 상태가 됩니다.');
        if (!reason?.trim()) return;
        run(async () => { await jsonPost(`/api/documents/revisions/${state.revision.id}/retire`, {reason: reason.trim()}); await loadItems(); message('Revision을 폐기 처리했습니다. 파일과 이력은 보존됩니다.'); });
    });
    $('drawingDocuments').addEventListener('click', event => {
        const retire = event.target.closest('[data-retire-document]');
        if (retire) {
            const reason = prompt('이 초안 문서의 폐기 사유를 입력해 주세요. 파일은 보존됩니다.');
            if (!reason?.trim()) return;
            run(async () => { await jsonPost(`/api/documents/${retire.dataset.retireDocument}/retire`, {reason: reason.trim()}); await selectRevision(state.revision.id); message('초안 문서를 폐기 처리했습니다.'); });
        }
    });
    document.addEventListener('DOMContentLoaded', () => run(async () => {
        state.options = await api('/api/documents/options');
        $('drawingFiles').accept = state.options.allowed_extensions.map(extension => `.${extension}`).join(',');
        $('drawingFileHelp').textContent = `파일당 ${(state.options.max_file_bytes / (1024 * 1024)).toFixed(0)}MB, 최대 ${state.options.max_files}개 · ${state.options.allowed_extensions.join(', ')}`;
        const itemId = new URLSearchParams(window.location.search).get('item_id');
        await loadItems(itemId);
    }));
})();
