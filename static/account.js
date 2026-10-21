// Sync account shared by the sign-in page and the reading page: MD5 key (KOSync x-auth-key)
// and the account kept in localStorage, so that one sign-in serves the catalog and the reading page.
(function () {
    'use strict';

    const STORAGE_KEY = 'sync-account';

    function md5(text) {
        const bytes = new TextEncoder().encode(text);
        const length = bytes.length;
        const words = new Uint32Array((((length + 8) >> 6) + 1) * 16);
        for (let i = 0; i < length; i++) words[i >> 2] |= bytes[i] << ((i % 4) * 8);
        words[length >> 2] |= 0x80 << ((length % 4) * 8);
        words[words.length - 2] = length * 8;
        words[words.length - 1] = Math.floor(length / 0x20000000);

        const S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
        const K = new Uint32Array(64);
        for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 0x100000000);

        let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
        for (let chunk = 0; chunk < words.length; chunk += 16) {
            let a = a0, b = b0, c = c0, d = d0;
            for (let i = 0; i < 64; i++) {
                let f, g;
                if (i < 16) { f = (b & c) | (~b & d); g = i; }
                else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
                else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
                else { f = c ^ (b | ~d); g = (7 * i) % 16; }
                const shift = S[(i >> 4) * 4 + (i % 4)];
                const sum = (a + f + K[i] + words[chunk + g]) | 0;
                a = d; d = c; c = b;
                b = (b + ((sum << shift) | (sum >>> (32 - shift)))) | 0;
            }
            a0 = (a0 + a) | 0; b0 = (b0 + b) | 0; c0 = (c0 + c) | 0; d0 = (d0 + d) | 0;
        }
        return [a0, b0, c0, d0].map(function (n) {
            let hex = '';
            for (let i = 0; i < 4; i++) hex += ((n >>> (i * 8)) & 0xff).toString(16).padStart(2, '0');
            return hex;
        }).join('');
    }

    function load() {
        try {
            const account = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
            return account && account.user && account.key ? account : null;
        } catch (e) {
            return null;
        }
    }

    function save(account) {
        try {
            if (account) localStorage.setItem(STORAGE_KEY, JSON.stringify(account));
            else localStorage.removeItem(STORAGE_KEY);
        } catch (e) { /* private mode: the account simply is not remembered */ }
    }

    // Catalog session opened by /login (OPDS_AUTH): its user name is readable, the session itself is not
    function sessionUser() {
        const match = document.cookie.match(/(?:^|;\s*)pyopds_user=([^;]*)/);
        return match ? decodeURIComponent(match[1]) : null;
    }

    window.SyncAccount = { md5: md5, load: load, save: save, sessionUser: sessionUser };
})();
