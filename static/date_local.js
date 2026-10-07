/* HTML date inputs use the user's local calendar date, not a UTC timestamp. */
(() => {
    'use strict';
    function format(date) {
        return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'),
            String(date.getDate()).padStart(2, '0')].join('-');
    }
    window.MesLocalDate = Object.freeze({format, today: () => format(new Date())});
})();
