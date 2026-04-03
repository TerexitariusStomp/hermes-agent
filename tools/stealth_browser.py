#!/usr/bin/env python3
"""
Stealth Browser Module for Hermes Agent
========================================

Combines techniques from:
1. Steel Browser - fingerprint injection via CDP
2. Firecrawl - random UserAgent, ad blocking, proxy rotation
3. Dev Browser - Playwright with persistent Chromium

Usage:
    from tools.stealth_browser import create_stealth_browser
    browser, context = create_stealth_browser()
    page = context.new_page()
    page.goto("https://example.com")

Or use the main solve function:
    from tools.stealth_browser import solve_captcha
    token = solve_captcha("turnstile", "0x4AAAAAAAc4qhUEsytXBEJx", "https://openrouter.ai")
"""
import subprocess
import logging
import random
import os
import time
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Steel Browser-style fingerprint injection script
# This is the actual JS that Steel Browser injects via CDP/evaluate
FINGERPRINT_SCRIPT = """
(fixedPlatform, fixedVendor, fixedRenderer, fixedDeviceMemory, fixedHardwareConcurrency, fixedArchitecture, fixedBitness, fixedModel, fixedPlatformVersion, fixedUaFullVersion, fixedBrands, fixedFullVersionList) => {
    // 1. Override User-Agent via Chrome CDP (handled separately)
    
    // 2. Override navigator.webdriver
    try {
        delete Object.getPrototypeOf(navigator).webdriver;
    } catch(e) {}
    
    // 3. Override navigator.plugins (must have realistic structure)
    const MimeType = function(options) {
        this.type = options.type;
        this.suffixes = options.suffixes;
        this.description = options.description || '';
    };
    
    const Plugin = function(options) {
        this.name = options.name;
        this.filename = options.filename || '';
        this.description = options.description || '';
        this.length = options.mimeTypes ? options.mimeTypes.length : 0;
        this.mimeTypes = options.mimeTypes || [];
        for (let i = 0; i < this.length; i++) {
            Object.defineProperty(this.mimeTypes, i, {
                value: this.mimeTypes[i],
                writable: false,
                enumerable: false
            });
        }
    };
    
    const plugins = [
        new Plugin({
            name: 'Chrome PDF Plugin',
            filename: 'internal-pdf-viewer',
            description: 'Portable Document Format',
            mimeTypes: [
                new MimeType({type: 'application/pdf', suffixes: 'pdf', description: ''})
            ]
        }),
        new Plugin({
            name: 'Chrome PDF Viewer',
            filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
            description: 'Portable Document Format',
            mimeTypes: []
        }),
        new Plugin({
            name: 'Native Client',
            filename: 'internal-nacl-plugin',
            description: '',
            mimeTypes: []
        }),
        new Plugin({
            name: 'Widevine Content Decryption Module',
            filename: 'widevinecdmadapter.so',
            description: 'Enables Widevine licenses for playback of HTML audio and video content.',
            mimeTypes: []
        }),
    ];
    
    // Add item and namedItem methods
    plugins.item = (i) => plugins[i] || null;
    plugins.namedItem = (name) => plugins.find(p => p.name === name) || null;
    
    Object.defineProperty(navigator, 'plugins', {
        get: () => Object.assign([], plugins, {
            item: plugins.item,
            namedItem: plugins.namedItem,
            length: plugins.length
        })
    });
    
    // 4. navigator.mimeTypes (empty array in Chrome)
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => Object.assign([], {
            item: () => null,
            namedItem: () => null,
            length: 0
        })
    });
    
    // 5. navigator.languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en']
    });
    
    // 6. navigator.hardwareConcurrency
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => Number(fixedHardwareConcurrency) || 8
    });
    
    // 7. navigator.deviceMemory
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => Number(fixedDeviceMemory) || 8
    });
    
    // 8. navigator.platform
    Object.defineProperty(navigator, 'platform', {
        get: () => fixedPlatform || 'Linux x86_64'
    });
    
    // 9. navigator.userAgentData (for Chrome 120+)
    if (navigator.userAgentData) {
        const brands = fixedBrands || [
            {brand: 'Not_A Brand', version: '8'},
            {brand: 'Chromium', version: '120'},
            {brand: 'Google Chrome', version: '120'}
        ];
        
        Object.defineProperty(navigator, 'userAgentData', {
            get: () => ({
                brands: brands,
                fullVersionList: fixedFullVersionList || brands,
                mobile: false,
                platform: fixedPlatform || 'Linux x86_64',
                architecture: fixedArchitecture || 'x86',
                bitness: fixedBitness || '64',
                model: fixedModel || '',
                platformVersion: fixedPlatformVersion || '15.0.0',
                uaFullVersion: fixedUaFullVersion || '120.0.6099.71',
                getHighEntropyValues: () => Promise.resolve({
                    architecture: fixedArchitecture || 'x86',
                    bitness: fixedBitness || '64',
                    brands: brands,
                    fullVersionList: fixedFullVersionList || brands,
                    mobile: false,
                    model: fixedModel || '',
                    platform: fixedPlatform || 'Linux x86_64',
                    platformVersion: fixedPlatformVersion || '15.0.0',
                    uaFullVersion: fixedUaFullVersion || '120.0.6099.71'
                })
            })
        });
    }
    
    // 10. navigator.maxTouchPoints
    Object.defineProperty(navigator, 'maxTouchPoints', {
        get: () => 0
    });
    
    // 11. navigator.connection
    if (navigator.connection || navigator.mozConnection || navigator.webkitConnection) {
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                effectiveType: '4g',
                rtt: 50,
                downlink: 10,
                saveData: false
            })
        });
    }
    
    // 12. Chrome runtime
    if (!window.chrome) {
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
    }
    if (!window.chrome.runtime) {
        window.chrome.runtime = {};
    }
    
    // 13. window.devicePixelRatio
    Object.defineProperty(window, 'devicePixelRatio', {
        get: () => 1
    });
    
    // 14. screen dimensions (must match browser viewport)
    Object.defineProperty(window, 'screen', {
        get: () => ({
            width: 1920,
            height: 1080,
            availWidth: 1920,
            availHeight: 1040,
            colorDepth: 24,
            pixelDepth: 24,
            orientation: {type: 'landscape-primary', angle: 0}
        })
    });
    
    // 15. WebGL renderer spoof
    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        const getParameterShimmed = function(parameter) {
            // UNMASKED_VENDOR_WEBGL
            if (parameter === 37445) return fixedVendor || 'Google Inc. (Intel)';
            // UNMASKED_RENDERER_WEBGL
            if (parameter === 37446) return fixedRenderer || 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 620, OpenGL 4.6)';
            return getParameter.call(this, parameter);
        };
        WebGLRenderingContext.prototype.getParameter = getParameterShimmed;
    } catch(e) {}
    
    // 16. Remove automation detection traces
    try {
        // These are added by ChromeDriver/Playwright
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        delete window.document.$chromeAsync;
    } catch(e) {}
    
    // 17. Permissions API
    try {
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({state: Notification.permission}) :
                origQuery(parameters)
        );
    } catch(e) {}
}
"""


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.71 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
    "Gecko/20100101 Firefox/121.0",
]


def random_ua():
    return random.choice(USER_AGENTS)


def stealth_launch_args():
    """Anti-detection Chrome launch args from Steel Browser + Firecrawl."""
    return [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-accelerated-2d-canvas",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-zygote",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-sync",
        "--metrics-recording-only",
        "--enable-features=NetworkService,NetworkServiceInProcess",
        "--disable-features=TranslateUI,VizDisplayCompositor",
        "--window-size=1920,1080",
    ]


def launch_stealth_browser(playwright):
    """Launch a stealth Chromium browser using Dev Browser's Chromium.
    
    This replicates Steel Browser's stealth approach:
    1. Launch with anti-detection args
    2. Create context with realistic UA
    3. Add fingerprint injection as init script
    4. Return browser and context
    
    Usage:
        with sync_playwright() as pw:
            browser, context = launch_stealth_browser(pw)
            page = context.new_page()
            # fingerprint auto-injected on all pages
    """
    ua = random_ua()
    
    browser = playwright.chromium.launch(
        headless=True,
        args=stealth_launch_args(),
        executable_path=None,  # Uses system default (Dev Browser's Chromium)
        ignore_default_args=["--enable-automation"]
    )
    
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=ua,
        locale="en-US",
        timezone_id="America/New_York",
        color_scheme="light",
    )
    
    # Add fingerprint injection to ALL pages automatically
    # This is Steel Browser's key technique
    context.add_init_script(FINGERPRINT_SCRIPT)
    
    # Block ad requests (Firecrawl approach)
    context.route("**/*.{png,jpg,jpeg,gif,svg,ico}", lambda route: route.abort())
    context.route("**/*.{mp3,mp4,avi,flac,ogg,wav,webm}", lambda route: route.abort())
    
    return browser, context


def inject_turnstile_token(page, token):
    """Inject Turnstile solution token into page.
    
    Steel Browser approach: use CDP to set value and dispatch events.
    """
    try:
        result = page.evaluate(f"""
        () => {{
            const token = `{token}`;
            
            // Method 1: textarea[name="cf-turnstile-response"]
            const ta = document.querySelector('textarea[name="cf-turnstile-response"]');
            if (ta) {{
                ta.value = token;
                ta.dispatchEvent(new Event('change', {{bubbles:true}}));
                ta.dispatchEvent(new Event('input', {{bubbles:true}}));
                return true;
            }}
            
            // Method 2: input[name="cf-turnstile-response"]
            const inp = document.querySelector('input[name="cf-turnstile-response"]');
            if (inp) {{
                inp.value = token;
                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                return true;
            }}
            
            // Method 3: Find by cf-turnstile in any attribute
            for (const el of document.querySelectorAll('input, textarea, iframe')) {{
                const attrs = Array.from(el.attributes);
                for (const attr of attrs) {{
                    if (attr.name.toLowerCase().includes('cf') || 
                        attr.name.toLowerCase().includes('turnstile')) {{
                        if (el.tagName === 'IFRAME') {{
                            try {{
                                el.contentWindow.postMessage(token, '*');
                            }} catch(e) {{}}
                        }} else {{
                            el.value = token;
                            el.dispatchEvent(new Event('change', {{bubbles:true}}));
                        }}
                        return true;
                    }}
                }}
            }}
            
            return false;
        }}
        """)
        logger.info(f"Turnstile injection result: {result}")
        return result
    except Exception as e:
        logger.error(f"Turnstile injection error: {e}")
        return None


def solve_captcha_with_api(captcha_type, sitekey, page_url, timeout=180):
    """Solve CAPTCHA via external API services.
    
    Requires CAPSOLVER_API_KEY or TWO_CAPTCHA_API_KEY or ANTICAPTCHA_API_KEY in env.
    """
    capsolver_key = os.environ.get("CAPSOLVER_API_KEY", "")
    twocaptcha_key = os.environ.get("TWO_CAPTCHA_API_KEY", "")
    anticaptcha_key = os.environ.get("ANTICAPTCHA_API_KEY", "")
    
    if not (capsolver_key or twocaptcha_key or anticaptcha_key):
        logger.warning("No CAPTCHA API key configured")
        return None
    
    # Import the API solver
    from tools.captcha_solver import CaptchaAPISolver
    solver = CaptchaAPISolver(capsolver_key, twocaptcha_key, anticaptcha_key)
    
    if captcha_type == "turnstile":
        return solver.solve_turnstile(sitekey, page_url, timeout=timeout)
    elif captcha_type in ("recaptcha_v2", "recaptcha"):
        return solver.solve_recaptcha_v2(sitekey, page_url, timeout=timeout)
    
    return None


def solve_turnstile(page, sitekey="0x4AAAAAAAc4qhUEsytXBEJx", 
                    page_url="https://openrouter.ai", timeout=180):
    """Solve Cloudflare Turnstile with auto-API fallback.
    
    1. Check if CapSolver/2Captcha/AntiCaptcha API key is available
    2. If so, solve via API
    3. Inject token into page
    4. Return token or None
    """
    # Try API solve
    token = solve_captcha_with_api("turnstile", sitekey, page_url, timeout)
    
    if token:
        result = inject_turnstile_token(page, token)
        if result:
            return token
    
    # If no API, try stealth alone wait
    logger.info("No API key - waiting for Turnstile auto-resolution with stealth")
    time.sleep(15)
    
    # Check if auto-resolved
    value = page.evaluate("""
        () => {
            const ta = document.querySelector('textarea[name="cf-turnstile-response"]');
            return ta ? ta.value : null;
        }
    """)
    
    if value and len(value) > 100:
        logger.info(f"Turnstile auto-resolved via stealth: {value[:30]}...")
        return value
    
    return None
