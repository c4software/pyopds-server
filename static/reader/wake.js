// Web reader: keeps the screen on while reading (Screen Wake Lock API, HTTPS only; ignored elsewhere).
// The lock is taken on reading activity (a page turned, a tap, a key) and released after 10 minutes
// without any, so a phone left with the book open still goes to sleep. The browser drops the lock
// when the page is hidden: it is taken again on the next activity.
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const IDLE_MS = 10 * 60 * 1000;

    /** Screen kept on while active: {activity()}. */
    function keep() {
        let lock = null;
        let asking = false;
        let idleTimer = null;

        async function acquire() {
            if (!navigator.wakeLock || asking || (lock && !lock.released) || document.visibilityState !== 'visible') return;
            asking = true;
            try {
                lock = await navigator.wakeLock.request('screen');
            } catch (e) {
                // Refused (battery saver, no HTTPS…): the screen sleeps as usual
                lock = null;
            } finally {
                asking = false;
            }
        }

        function release() {
            if (lock && !lock.released) lock.release().catch(function () {});
            lock = null;
        }

        function activity() {
            acquire();
            clearTimeout(idleTimer);
            idleTimer = setTimeout(release, IDLE_MS);
        }

        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') activity();
        });
        activity();
        return { activity: activity };
    }

    PyReader.wake = { keep: keep };
})();
