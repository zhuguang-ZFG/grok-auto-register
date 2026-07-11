/**
 * Stealth injection: randomize Canvas / WebGL / AudioContext fingerprints.
 *
 * This mimics what Camoufox (community's preferred anti-detect browser) does
 * at the C level, but via JS injection since we use Chromium (DrissionPage).
 *
 * Injected via CDP on every page load before any site script runs.
 */

// Canvas fingerprint: add tiny per-session noise to toDataURL / getImageData
(function () {
    const NOISE = (Math.random() - 0.5) * 0.0001;
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (type) {
        const ctx = this.getContext("2d");
        if (ctx && this.width > 0 && this.height > 0) {
            try {
                const img = ctx.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < img.data.length; i += 4) {
                    img.data[i] = Math.max(0, Math.min(255, img.data[i] + NOISE));
                }
                ctx.putImageData(img, 0, 0);
            } catch (e) { /* tainted canvas — skip */ }
        }
        return origToDataURL.apply(this, arguments);
    };

    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function () {
        const data = origGetImageData.apply(this, arguments);
        for (let i = 0; i < data.data.length; i += 4) {
            data.data[i] = Math.max(0, Math.min(255, data.data[i] + NOISE));
        }
        return data;
    };
})();

// WebGL fingerprint: spoof VENDOR + RENDERER
(function () {
    const VENDORS = ["Google Inc. (NVIDIA)", "Google Inc. (Intel)", "Google Inc. (AMD)"];
    const RENDERERS = [
        "ANGLE (NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
        "ANGLE (Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
        "ANGLE (AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0)",
    ];
    const idx = Math.floor(Math.random() * VENDORS.length);
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (param) {
        // UNMASKED_VENDOR_WEBGL
        if (param === 37445) return VENDORS[idx];
        // UNMASKED_RENDERER_WEBGL
        if (param === 37446) return RENDERERS[idx];
        return getParameter.apply(this, arguments);
    };
    if (window.WebGL2RenderingContext) {
        const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function (param) {
            if (param === 37445) return VENDORS[idx];
            if (param === 37446) return RENDERERS[idx];
            return getParameter2.apply(this, arguments);
        };
    }
})();

// AudioContext fingerprint: add noise to sample rate / channel data
(function () {
    const origGetChannelData = AnalyserNode.prototype.getFloatFrequencyData;
    if (origGetChannelData) {
        AnalyserNode.prototype.getFloatFrequencyData = function (array) {
            origGetChannelData.apply(this, arguments);
            for (let i = 0; i < array.length; i++) {
                array[i] = array[i] + (Math.random() - 0.5) * 0.1;
            }
        };
    }
})();

// navigator.hardwareConcurrency: randomize 4-12
(function () {
    try {
        Object.defineProperty(navigator, "hardwareConcurrency", {
            get: function () {
                return [4, 6, 8, 12][Math.floor(Math.random() * 4)];
            },
        });
    } catch (e) {}
})();

// navigator.deviceMemory: randomize 4-16
(function () {
    try {
        Object.defineProperty(navigator, "deviceMemory", {
            get: function () {
                return [4, 8, 16][Math.floor(Math.random() * 3)];
            },
        });
    } catch (e) {}
})();

// navigator.plugins: fake common plugins (real Chrome has these)
(function () {
    try {
        Object.defineProperty(navigator, "plugins", {
            get: function () {
                return [
                    { name: "PDF Viewer", filename: "internal-pdf-viewer" },
                    { name: "Chrome PDF Viewer", filename: "internal-pdf-viewer" },
                    { name: "Chromium PDF Viewer", filename: "internal-pdf-viewer" },
                    { name: "Microsoft Edge PDF Viewer", filename: "internal-pdf-viewer" },
                    { name: "WebKit built-in PDF", filename: "internal-pdf-viewer" },
                ];
            },
        });
    } catch (e) {}
})();

// WebRTC: prevent real local IP leak (Cloudflare uses this for fingerprinting)
(function () {
    const origRTCPeerConnection = window.RTCPeerConnection;
    if (origRTCPeerConnection) {
        window.RTCPeerConnection = function () {
            const pc = new origRTCPeerConnection(arguments[0], arguments[1]);
            const origCreateDataChannel = pc.createDataChannel;
            pc.createDataChannel = function () {
                return origCreateDataChannel.apply(this, arguments);
            };
            return pc;
        };
        window.RTCPeerConnection.prototype = origRTCPeerConnection.prototype;
    }
})();
