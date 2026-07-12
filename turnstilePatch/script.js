// Turnstile Patch (merged): local MouseEvent screenXY + anti-automation hints.
// MUST run in MAIN world (manifest world: MAIN) so page/iframe sees patches.
(function () {
    "use strict";

    function getRandomInt(min, max) {
        return Math.floor(Math.random() * (max - min + 1)) + min;
    }

    // --- A: screenX/Y (4K-safe; original local patch) ---
    try {
        var _sx = getRandomInt(800, 1200);
        var _sy = getRandomInt(400, 600);
        Object.defineProperty(MouseEvent.prototype, "screenX", { configurable: true, get: function () { return _sx + this.clientX; } });
        Object.defineProperty(MouseEvent.prototype, "screenY", { configurable: true, get: function () { return _sy + this.clientY; } });
    } catch (e) {}

    // --- B: community anti-detect (webdriver / chrome.runtime / permissions) ---
    try {
        Object.defineProperty(navigator, "webdriver", {
            get: function () {
                return false;
            },
            configurable: true,
        });
    } catch (e) {}

    try {
        if (window.chrome && window.chrome.runtime) {
            try {
                delete window.chrome.runtime.onConnect;
            } catch (e1) {}
            try {
                delete window.chrome.runtime.onMessage;
            } catch (e2) {}
        }
    } catch (e) {}

    try {
        if (navigator.permissions && navigator.permissions.query) {
            var origQuery = navigator.permissions.query.bind(navigator.permissions);
            navigator.permissions.query = function (params) {
                if (params && params.name === "notifications") {
                    return Promise.resolve({
                        state: Notification.permission,
                        onchange: null,
                    });
                }
                return origQuery(params);
            };
        }
    } catch (e) {}

    // languages: only fill if empty/odd (don't force en-US over real locale)
    try {
        var langs = navigator.languages;
        if (!langs || langs.length === 0) {
            Object.defineProperty(navigator, "languages", {
                get: function () {
                    return ["en-US", "en"];
                },
                configurable: true,
            });
        }
    } catch (e) {}
})();
