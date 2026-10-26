/* Shared revision display; stored identities and legacy codes are preserved. */
(() => {
    'use strict';
    const storedNumber = /^(?:REV\.\s*)?([0-9]+)$/i;
    function digits(value) { return (value.replace(/^0+/, '') || '0').padStart(2, '0'); }
    function display(value) {
        const raw = String(value ?? '').trim(), match = storedNumber.exec(raw);
        return match ? 'Rev.' + digits(match[1]) : raw;
    }
    function code(value, maxDigits = 46) {
        const digits = String(value ?? '').trim();
        if (!/^[0-9]+$/.test(digits) || digits.length > maxDigits) {
            throw new Error('개정번호는 숫자 ' + maxDigits + '자리 이내로 입력해 주세요. REV.는 자동으로 붙습니다.');
        }
        return 'REV.' + digits;
    }
    function setInput(input, stored, preserveLegacy = false) {
        const raw = String(stored ?? '').trim();
        const match = storedNumber.exec(raw);
        const legacy = !!raw && !match;
        input.value = match ? digits(match[1]) : raw;
        input.dataset.originalRevision = raw;
        input.dataset.originalRevisionInput = input.value;
        input.closest('.revision-number-field')?.classList.toggle('legacy', legacy);
        input.dataset.legacyRevision = legacy ? raw : '';
        if (preserveLegacy) input.readOnly = legacy;
    }
    function read(input, preserveLegacy = false) {
        if (preserveLegacy && input.dataset.legacyRevision &&
                input.value === input.dataset.legacyRevision) return input.value;
        if (input.dataset.originalRevision && input.value === input.dataset.originalRevisionInput &&
                storedNumber.test(input.dataset.originalRevision)) return input.dataset.originalRevision;
        return code(input.value, Number(input.maxLength) > 0 ? input.maxLength : 46);
    }
    function ask(title) {
        return new Promise(resolve => {
            const dialog = document.createElement('dialog');
            dialog.className = 'mes-revision-dialog';
            dialog.innerHTML = '<form><h3></h3><label>새 개정번호 *<span class="revision-number-field"><span class="revision-number-prefix">Rev.</span><input required inputmode="numeric" pattern="[0-9]+" maxlength="46" placeholder="숫자 입력" aria-label="새 개정번호 숫자"></span></label><div class="mes-revision-actions"><button type="button">취소</button><button type="submit">확인</button></div></form>';
            dialog.querySelector('h3').textContent = title;
            const input = dialog.querySelector('input');
            dialog.querySelector('form').addEventListener('submit', event => {
                event.preventDefault();
                try { code(input.value); dialog.close(input.value.trim()); }
                catch (error) { input.setCustomValidity(error.message); input.reportValidity(); }
            });
            input.addEventListener('input', () => input.setCustomValidity(''));
            dialog.querySelector('button[type="button"]').onclick = () => dialog.close('');
            dialog.addEventListener('cancel', event => { event.preventDefault(); dialog.close(''); });
            dialog.addEventListener('close', () => {
                const result = dialog.returnValue || null;
                dialog.remove();
                resolve(result);
            }, {once: true});
            document.body.append(dialog);
            dialog.showModal();
            input.focus();
        });
    }
    document.addEventListener('focusout', event => {
        const input = event.target;
        if (input.matches?.('.revision-number-field input') && /^[0-9]+$/.test(input.value.trim()))
            input.value = digits(input.value.trim());
    });
    window.MesRevisionNumber = Object.freeze({code, setInput, read, ask, display});
})();
