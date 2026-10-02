(() => {
    'use strict';
    const panel = document.getElementById('mesSessionCountdown');
    if (!panel) return;
    const note = document.getElementById('mesSessionWarning');
    // 읽기 전용 메뉴의 업무 쓰기 차단과 별개인 인증용 통신입니다.
    const authFetch = window.fetch.bind(window);
    let key = '', deadline = null, timeoutSeconds = 0, serverTime = 0;
    let checking = false, sending = false, pendingInput = false, lastInput = -Infinity;
    let lastSend = -Infinity, sendTimer = null, leaving = false, unavailable = false;

    function loginRequired() {
        if (leaving) return;
        leaving = true;
        panel.textContent = '로그인 시간이 만료되었습니다.';
        window.location.replace('/login?reason=idle');
    }

    function show() {
        if (unavailable || deadline === null) {
            panel.textContent = unavailable ? '로그아웃 시간 확인 불가' : '로그아웃 시간 확인 중…';
            panel.classList.remove('is-warning');
            note.hidden = !unavailable;
            note.textContent = '서버 연결을 확인해 주세요. 작업 시간을 임의로 연장하지 않습니다.';
            return;
        }
        const seconds = Math.max(0, Math.ceil((deadline - performance.now()) / 1000));
        const time = String(Math.floor(seconds / 60)).padStart(2, '0') + ':' +
            String(seconds % 60).padStart(2, '0');
        panel.textContent = seconds ? '자동 로그아웃까지 ' + time : '로그인 만료 확인 중…';
        const warning = seconds <= Math.min(120, timeoutSeconds / 10);
        panel.classList.toggle('is-warning', warning);
        note.hidden = !warning;
        note.textContent = '작성 중인 내용을 저장해 주세요.';
        if (!seconds && !checking) checkStatus();
    }

    function accept(data) {
        if (data.username !== panel.dataset.username) {
            // 다른 탭에서 계정이 바뀌면 이전 계정 화면을 계속 사용하지 않습니다.
            leaving = true;
            window.location.reload();
            return;
        }
        if (data.server_time_ms < serverTime) return;
        serverTime = data.server_time_ms;
        key = data.activity_key;
        timeoutSeconds = data.idle_timeout_seconds;
        deadline = performance.now() + Math.max(0, data.expires_at_ms - data.server_time_ms);
        unavailable = false;
        show();
    }

    async function parseResponse(response) {
        if (response.status === 401) {
            loginRequired();
            return null;
        }
        if (!response.ok) throw new Error('로그인 시간 확인 실패');
        return response.json();
    }

    async function checkStatus() {
        if (checking || leaving) return;
        checking = true;
        try {
            const response = await authFetch('/api/session', {cache:'no-store', credentials:'same-origin'});
            const data = await parseResponse(response);
            if (data) accept(data);
        } catch (_) {
            unavailable = true;
            show();
        } finally {
            checking = false;
            if (pendingInput) scheduleActivity();
        }
    }

    function scheduleActivity() {
        if (leaving || sending || !key || !pendingInput || sendTimer !== null) return;
        const wait = Math.max(0, 10000 - (performance.now() - lastSend));
        sendTimer = window.setTimeout(() => {sendTimer = null; sendActivity();}, wait);
    }

    async function sendActivity(keepalive = false) {
        if (leaving || sending || !pendingInput || !key) return;
        // 오래된 입력을 재접속/폴링 시 새 입력처럼 보내 세션을 살리지 않습니다.
        if (performance.now() - lastInput > 10000) {pendingInput = false; return;}
        pendingInput = false;
        sending = true;
        lastSend = performance.now();
        try {
            const response = await authFetch('/api/session/activity', {
                method:'POST', credentials:'same-origin', cache:'no-store', keepalive,
                headers:{'X-MES-Session-Activity':key}
            });
            const data = await parseResponse(response);
            if (data) accept(data);
        } catch (_) {
            unavailable = true;
            show();
        } finally {
            sending = false;
            scheduleActivity();
        }
    }

    function inputActivity(event) {
        if (!event.isTrusted || leaving || document.visibilityState !== 'visible') return;
        pendingInput = true;
        lastInput = performance.now();
        scheduleActivity();
    }

    // 단순 마우스 이동·페이지 열기·자동 조회·상태 폴링은 활동으로 보지 않습니다.
    ['pointerdown','keydown','input','wheel','touchstart'].forEach(name =>
        window.addEventListener(name, inputActivity, {passive:true}));
    window.addEventListener('pagehide', () => {
        if (sendTimer !== null) {clearTimeout(sendTimer); sendTimer = null;}
        // 닫기 자체는 시간을 연장하지 않고 아직 전송하지 못한 실제 입력만 전달합니다.
        if (pendingInput) sendActivity(true);
    });
    window.addEventListener('pageshow', event => {if (event.persisted) checkStatus();});
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') checkStatus();
    });

    checkStatus();
    window.setInterval(show, 1000);
    window.setInterval(checkStatus, 30000);
})();
