// Web reader: reading time for the statistics of the sync account. Counts the seconds the page is
// visible while the reader is active (a page turned, a key, a tap or a scroll in the last minutes) and
// the pages turned forward, then adds them to the "web reader" device (POST /webui/reader/session)
// every minute and when the page is hidden or closed. A pause of more than 10 minutes starts a new
// reading session.
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const TICK_MS = 5000;
    const IDLE_MS = 3 * 60 * 1000;
    const SESSION_GAP_MS = 10 * 60 * 1000;
    const FLUSH_MS = 60 * 1000;
    const SESSION_SECONDS_MAX = 3600;
    const FINISHED = 0.98;

    /**
     * Start counting for a library book (`path`, relative to the library). Returns {activity(),
     * pageTurned(count), reached(fraction), flush(keepalive)}; nothing is sent without an account.
     */
    function track(path, signedIn) {
        const state = {
            seconds: 0, pages: 0, newSession: true, completed: false,
            lastActivity: Date.now(), lastTick: Date.now(), lastFlush: Date.now(), sending: false,
        };

        function activity() {
            const now = Date.now();
            if (now - state.lastActivity > SESSION_GAP_MS) state.newSession = true;
            state.lastActivity = now;
        }

        function tick() {
            const now = Date.now();
            const elapsed = Math.min(now - state.lastTick, TICK_MS * 2);
            state.lastTick = now;
            if (document.visibilityState === 'visible' && now - state.lastActivity < IDLE_MS) {
                state.seconds += elapsed / 1000;
            }
            if (now - state.lastFlush >= FLUSH_MS) flush(false);
        }

        async function flush(keepalive) {
            state.lastFlush = Date.now();
            const seconds = Math.min(Math.floor(state.seconds), SESSION_SECONDS_MAX);
            if (!signedIn || state.sending || (seconds < 1 && !state.pages)) return;
            const body = {
                path: path, seconds: seconds, pages: state.pages, new_session: state.newSession && seconds > 0,
                completed: state.completed, tz: new Date().getTimezoneOffset(),
            };
            state.seconds -= seconds;
            state.pages = 0;
            if (body.new_session) state.newSession = false;
            state.sending = true;
            try {
                const response = await fetch('/webui/reader/session', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(body),
                    credentials: 'same-origin',
                    keepalive: Boolean(keepalive),
                });
                if (!response.ok) throw new Error(String(response.status));
            } catch (e) {
                // Kept for the next upload
                state.seconds += body.seconds;
                state.pages += body.pages;
                if (body.new_session) state.newSession = true;
            } finally {
                state.sending = false;
            }
        }

        setInterval(tick, TICK_MS);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'hidden') {
                tick();
                flush(true);
            } else {
                state.lastTick = Date.now();
                activity();
            }
        });
        window.addEventListener('pagehide', function () { flush(true); });
        ['keydown', 'pointerdown', 'wheel', 'touchstart'].forEach(function (type) {
            window.addEventListener(type, activity, { passive: true });
        });

        return {
            activity: activity,
            flush: flush,
            /** Pages turned forward (a jump in the book is not counted). */
            pageTurned: function (count) {
                activity();
                state.pages += Math.max(0, Math.round(count));
            },
            /** Progress reached: the book counts as finished near its end. */
            reached: function (fraction) {
                if (fraction >= FINISHED) state.completed = true;
            },
        };
    }

    PyReader.stats = { track: track };
})();
