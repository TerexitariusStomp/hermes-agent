#!/usr/bin/env python3
"""Self-hosted CAPTCHA Solver & Browser Stealth Module for Hermes Agent

Combines techniques from three open-source projects:
1. Steel Browser (steel-dev/steel-browser) - stealth chrome, fingerprint injection
2. Firecrawl (firecrawl/firecrawl) - proxy rotation, UA randomization, DNS caching
3. Dev Browser (dobrowser/dev-browser) - persistent sessions, sandboxed execution

Key insights:

Steel Browser (api/src/services/cdp/cdp.service.ts):
  - Uses fingerprint-generator + fingerprint-injector libraries
  - Injects realistic browser fingerprints via CDP (navigator, WebGL, Canvas, etc.)
  - Custom Chrome launch args for anti-detection
  - Session context persistence (cookies, localStorage, indexedDB, sessionStorage)
  - Custom headers per request
  - Bandwidth optimization (block images/media/stylesheets)

Firecrawl (apps/playwright-service-ts/api.ts):
  - Random UserAgent from user-agents library per session
  - DNS caching with TTL (30 seconds)
  - Proxy support with auth
  - Ad blocking via domain blacklist
  - Service worker blocking
  - Safe URL validation (DNS check for private IPs)
  - Semaphore-based concurrency limiting

Dev Browser (cli/daemon):
  - Sandboxed JS execution via QuickJS WASM
  - Persistent pages across scripts
  - Auto-connect to running Chrome via CDP
  - Full Playwright API emulation

CAPTCHA Bypass Strategy:

Level 1 - Stealth (prevent detection):
  - Anti-detection Chrome launch args
  - Fingerprint injection (navigator, hardware, WebGL)
  - UA randomization
  - Session persistence
  - DNS caching
  - Service worker blocking

Level 2 - External API (solve CAPTCHA when detected):
  - CapSolver API (TurnstileTaskProxyless) - $0.775/1000
  - 2Captcha API - $2.99/1000 Turnstile
  - AntiCaptcha API - $1.00/1000 Turnstile

Level 3 - Self-hosted turnstile bypass (research):
  - The repos use external APIs for actual CAPTCHA solving
  - The stealth techniques prevent CAPTCHAs from appearing in the first place
  - For Cloudflare Turnstile specifically, the token must be obtained from
    Cloudflare's servers - this requires either:
    a) A real browser with residential IP (Steel Browser approach)
    b) CAPTCHA solving farm (CapSolver/2Captcha)
    c) Custom JS to extract iframe token (complex, rarely works on CF)

Token Injection:
  - Turnstile: textarea[name="cf-turnstile-response"]
  - reCAPTCHA v2/v3: textarea[name="g-recaptcha-response"]
  - hCaptcha: textarea[name="h-captcha-response"]

Usage:
    from tools.captcha_solver import CaptchaSolver, StealthBrowser, inject_turnstile
    solver = CaptchaSolver()
    token = solver.solve_turnstile(sitekey, page_url)
    inject_turnstile(page, token)

Environment variables (in ~/.hermes/.env):
    CAPSOLVER_API_KEY=CAPxxxx
    TWO_CAPTCHA_API_KEY=xxxx
    ANTICAPTCHA_API_KEY=xxxx
"""
import requests
import time
import logging
import os
import random
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Known CAPTCHA sitekeys for quick reference
KNOWN_CAPTCHAS = {
    "openrouter": {
        "sitekey": "0x4AAAAAAAc4qhUEsytXBEJx",
        "url": "https://openrouter.ai",
        "type": "turnstile"
    },
    "recaptcha_v2_example": {
        "sitekey": "6LeIxAcTAAAAAJcZVRqyHh71UMEEGNp_M3JI7aP",
        "url": "https://www.google.com/recaptcha/api2/demo",
        "type": "recaptcha_v2"
    },
}


class StealthBrowser:
    """Steel Browser-inspired stealth browser configuration.
    
    Based on:
    - steel-browser/api/src/services/cdp/cdp.service.ts
    - steel-browser/api/src/types/browser.ts
    - steel-browser/docker-compose.yml
    
    Steel Browser's approach:
    1. Launch Chrome with specific anti-detection args
    2. Generate realistic browser fingerprint (fingerprint-generator)
    3. Inject fingerprint into page via CDP (fingerprint-injector)
    4. Persist session context (cookies, localStorage, etc.)
    5. Use custom headers per request
    6. Block unnecessary resources (images, media, stylesheets)
    
    Usage:
        args = StealthBrowser.launch_args()
        ua = StealthBrowser.random_ua()
        injection = StealthBrowser.fingerprint_injection_js()
        
        # With Playwright:
        browser = playwright.chromium.launch(args=args)
        context = browser.new_context(user_agent=ua)
        context.add_init_script(injection)
    """
    
    @staticmethod
    def launch_args() -> List[str]:
        """Anti-detection Chrome launch arguments from Steel Browser.
        
        Steel Browser uses puppeteer-core with these args to launch a
        stealth Chrome instance that appears as a regular user's browser.
        """
        return [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--no-first-run",
            "--no-zygote",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--hide-scrollbars",
            "--mute-audio",
            "--disable-extensions",
            "--disable-infobars",
            "--disable-logging",
            "--disable-notifications",
            "--disable-popup-blocking",
            "--disable-translate",
            "--disable-default-apps",
            "--disable-hang-monitor",
            "--disable-prompt-on-repost",
            "--disable-sync",
            "--metrics-recording-only",
            "--enable-features=NetworkService,NetworkServiceInProcess",
            "--disable-features=TranslateUI,VizDisplayCompositor",
            "--window-size=1920,1080",
            "--start-maximized",
            "--lang=en-US",
        ]
    
    @staticmethod
    def user_agents() -> List[str]:
        """Realistic User Agents from Firecrawl's user-agents library.
        
        Firecrawl rotates UA per session to prevent fingerprinting.
        """
        return [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
            "Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        ]
    
    @staticmethod
    def random_ua() -> str:
        """Get a random realistic UserAgent."""
        return random.choice(StealthBrowser.user_agents())
    
    @staticmethod 
    def fingerprint_injection_js() -> str:
        """JavaScript to inject realistic browser fingerprints.
        
        From Steel Browser's fingerprint-injector approach.
        This overrides all navigator properties that CAPTCHAs check.
        """
        return """
        () => {
            // Remove webdriver property entirely (critical for anti-detection)
            try {
                delete Object.getPrototypeOf(navigator).webdriver;
            } catch(e) {
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            }
            
            // Realistic plugins list
            const plugins = [
                {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer',
                 description: 'Portable Document Format'},
                {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                 description: ''},
                {name: 'Native Client', filename: 'internal-nacl-plugin',
                 description: ''},
            ];
            Object.defineProperty(navigator, 'plugins', {
                get: () => Object.assign([], plugins, {
                    item: (i) => plugins[i] || null,
                    namedItem: (name) => plugins.find(p => p.name === name) || null,
                    length: plugins.length
                })
            });
            
            // Realistic languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            
            // Hardware specs (matches common laptop)
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8
            });
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8
            });
            Object.defineProperty(navigator, 'maxTouchPoints', {
                get: () => 0
            });
            
            // Chrome runtime (for non-automation detection)
            if (!window.chrome) {
                window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
            }
            if (!window.chrome.runtime) { window.chrome.runtime = {}; }
            Object.defineProperty(window.chrome, 'runtime', {
                get: () => ({})
            });
            
            // Permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({state: Notification.permission}) :
                    originalQuery(parameters)
            );
            
            // Window dimensions
            Object.defineProperty(window, 'outerWidth', {get: () => 1920});
            Object.defineProperty(window, 'outerHeight', {get: () => 1080});
            Object.defineProperty(window, 'innerWidth', {get: () => 1920});
            Object.defineProperty(window, 'innerHeight', {get: () => 975});
            Object.defineProperty(window, 'devicePixelRatio', {get: () => 1});
            
            // Screen dimensions
            Object.defineProperty(window, 'screen', {
                get: () => ({
                    width: 1920, height: 1080,
                    availWidth: 1920, availHeight: 1040,
                    colorDepth: 24, pixelDepth: 24
                })
            });
            
            // WebGL renderer signature
            try {
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Google Inc. (Intel)';
                    if (parameter === 37446) return 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 620, OpenGL 4.6)';
                    return getParameter.apply(this, arguments);
                };
            } catch(e) {}
            
            // Remove automation traces
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        }
        """
    
    @staticmethod
    def turnstile_bypass_js(sitekey=None):
        """JS to attempt bypassing Cloudflare Turnstile via stealth alone.
        
        This doesn't solve the CAPTCHA but may prevent it from appearing
        by making the browser look completely non-automated.
        
        Sometimes Turnstile in 'non-interactive' mode will auto-pass if
        the risk score is low enough.
        """
        return f"""
        () => {{
            // Turnstile configuration
            const sitekey = {repr(sitekey)};
            
            // 1. Check if Turnstile is already loaded
            if (window.turnstile) {{
                console.log('[stealth] Turnstile API found');
                return \x27turnstile_found\x27;
            }}
            
            // 2. Wait for Turnstile script to load
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {{
                if (s.src && s.src.includes('challenges.cloudflare.com/turnstile')) {{
                    console.log('[stealth] Turnstile script loading');
                }}
            }}
            
            // 3. Find hidden textarea for cf-turnstile-response
            const textarea = document.querySelector('textarea[name="cf-turnstile-response"]');
            if (textarea) {{
                console.log('[stealth] Found cf-turnstile-response textarea');
                return \x27textarea_found\x27;
            }}
            
            // 4. Check for any Turnstile widget
            const els = document.querySelectorAll('[data-sitekey]');
            for (const el of els) {{
                if (el.dataset.sitekey) {{
                    console.log('[stealth] Found Turnstile widget');
                }}
            }}
            
            return \x27nothing_found\x27;
        }}
        """


class CaptchaAPISolver:
    """External CAPTCHA solving via third-party API services.
    
    Supported providers:
    1. CapSolver (primary) - Best Turnstile support, $0.775/1000
       API: POST https://api.capsolver.com/createTask
       Docs: https://docs.capsolver.com/guide/captcha/CloudflareTurnstile.html
       Task types: TurnstileTaskProxyless, ReCaptchaV2TaskProxyless
        
    2. 2Captcha (fallback) - Cheapest overall, $2.99/1000 Turnstile
       API: POST https://api.2captcha.com/in.php
       Docs: https://2captcha.com/2captcha-api#turnstile
       Methods: turnstile, userrecaptcha
        
    3. AntiCaptcha (alternative) - $1.00/1000 Turnstile
       API: POST https://api.anti-captcha.com/createTask
       Task types: TurnstileTaskProxyless
    
    All providers work the same way:
    1. You POST/GET to create a task with sitekey + pageurl
    2. Provider solves it using real browsers/residential IPs
    3. You poll for the result (usually 10-30 seconds)
    4. Provider returns the solution token
    5. You inject the token into the page's hidden form field
    """
    
    def __init__(self, capsolver_key=None, twocaptcha_key=None, 
                 anticaptcha_key=None):
        self.capsolver_key = capsolver_key or os.environ.get(
            "CAPSOLVER_API_KEY", "")
        self.twocaptcha_key = twocaptcha_key or os.environ.get(
            "TWO_CAPTCHA_API_KEY", "")
        self.anticaptcha_key = anticaptcha_key or os.environ.get(
            "ANTICAPTCHA_API_KEY", "")
        self._capsolver_url = "https://api.capsolver.com"
        self._2c_url = "https://api.2captcha.com"
        self._ac_url = "https://api.anti-captcha.com"
    
    def is_configured(self) -> bool:
        return bool(self.capsolver_key or self.twocaptcha_key or 
                   self.anticaptcha_key)
    
    def solve_turnstile(self, sitekey: str, page_url: str,
                       timeout: int = 120) -> Optional[str]:
        """Solve Cloudflare Turnstile CAPTCHA.
        
        Returns the solution token, or None on failure.
        """
        # Try CapSolver first (native Turnstile, cheapest)
        if self.capsolver_key:
            token = self._capsolver_turnstile(sitekey, page_url, timeout)
            if token: return token
        
        # Try AntiCaptcha
        if self.anticaptcha_key:
            token = self._anticaptcha_turnstile(sitekey, page_url, timeout)
            if token: return token
        
        # Try 2Captcha last (most expensive)
        if self.twocaptcha_key:
            token = self._2c_turnstile(sitekey, page_url, timeout)
            if token: return token
        
        return None
    
    def _capsolver_turnstile(self, sitekey, page_url, timeout):
        """Solve Turnstile via CapSolver API."""
        payload = {
            "clientKey": self.capsolver_key,
            "task": {
                "type": "TurnstileTaskProxyless",
                "websiteURL": page_url,
                "websiteKey": sitekey
            }
        }
        
        try:
            print(f"[CapSolver] Creating task: {sitekey}")
            r = requests.post(f"{self._capsolver_url}/createTask",
                            json=payload, timeout=30)
            data = r.json()
            print(f"[CapSolver] Response: {data}")
            
            if data.get("errorId", 1) != 0:
                print(f"[CapSolver] Error: {data.get('errorDescription')}")
                return None
            
            task_id = data.get("taskId")
            if not task_id: return None
            
            print(f"[CapSolver] Task {task_id}, polling for {timeout}s...")
            start = time.time()
            while time.time() - start < timeout:
                time.sleep(5)
                r = requests.post(f"{self._capsolver_url}/getTaskResult",
                    json={"clientKey": self.capsolver_key, "taskId": task_id},
                    timeout=15)
                result = r.json()
                
                if result.get("status") == "ready":
                    token = result.get("solution", {}).get("token")
                    if token:
                        print(f"[CapSolver] Solved in {time.time()-start:.1f}s")
                        return token
                elif result.get("status") != "processing":
                    print(f"[CapSolver] Unexpected: {result.get('status')}")
                    break
                print(f"[CapSolver] Waiting... ({time.time()-start:.0f}s)")
        except Exception as e:
            print(f"[CapSolver] Error: {e}")
        return None
    
    def _anticaptcha_turnstile(self, sitekey, page_url, timeout):
        """Solve Turnstile via AntiCaptcha API."""
        payload = {
            "clientKey": self.anticaptcha_key,
            "task": {
                "type": "TurnstileTaskProxyless",
                "websiteURL": page_url,
                "websiteKey": sitekey
            }
        }
        try:
            print(f"[AntiCaptcha] Creating task...")
            r = requests.post(f"{self._ac_url}/createTask",
                            json=payload, timeout=30)
            data = r.json()
            
            if data.get("errorId", 1) != 0:
                print(f"[AntiCaptcha] Error: {data.get('errorDescription')}")
                return None
            
            task_id = data.get("taskId")
            if not task_id: return None
            
            start = time.time()
            while time.time() - start < timeout:
                time.sleep(5)
                r = requests.post(f"{self._ac_url}/getTaskResult",
                    json={"clientKey": self.anticaptcha_key, "taskId": task_id},
                    timeout=15)
                result = r.json()
                if result.get("status") == "ready":
                    return result.get("solution", {}).get("token")
                elif result.get("status") != "processing":
                    break
        except Exception as e:
            print(f"[AntiCaptcha] Error: {e}")
        return None
    
    def _2c_turnstile(self, sitekey, page_url, timeout):
        """Solve Turnstile via 2Captcha API."""
        try:
            # Submit task
            params = {"key": self.twocaptcha_key, "method": "turnstile",
                     "sitekey": sitekey, "pageurl": page_url, "json": 1}
            r = requests.post(f"{self._2c_url}/in.php", data=params, timeout=30)
            data = r.json()
            
            if data.get("status") == 1:
                task_id = data.get("request")
                print(f"[2Captcha] Task {task_id}")
            else:
                print(f"[2Captcha] Error: {data.get('request')}")
                return None
            
            # Poll
            start = time.time()
            while time.time() - start < timeout:
                time.sleep(5)
                r = requests.get(f"{self._2c_url}/res.php",
                    params={"key": self.twocaptcha_key, "id": task_id,
                           "action": "get", "json": 1}, timeout=15)
                result = r.json()
                
                if result.get("status") == 1:
                    return result.get("request")  # The token
                elif "CAPCHA_NOT_READY" not in result.get("request", ""):
                    print(f"[2Captcha] Error: {result.get('request')}")
                    break
        except Exception as e:
            print(f"[2Captcha] Error: {e}")
        return None
    
    def get_balance(self) -> Dict[str, Any]:
        """Get balance for all configured providers."""
        result = {}
        if self.capsolver_key:
            try:
                r = requests.post(f"{self._capsolver_url}/getBalance",
                    json={"clientKey": self.capsolver_key}, timeout=10)
                result["capsolver"] = r.json()
            except: result["capsolver"] = {"error": "failed"}
        if self.twocaptcha_key:
            try:
                r = requests.get(f"{self._2c_url}/res.php",
                    params={"key": self.twocaptcha_key, 
                           "action": "getbalance", "json": 1}, timeout=10)
                result["twocaptcha"] = r.json()
            except: result["twocaptcha"] = {"error": "failed"}
        if self.anticaptcha_key:
            try:
                r = requests.post(f"{self._ac_url}/getBalance",
                    json={"clientKey": self.anticaptcha_key}, timeout=10)
                result["anticaptcha"] = r.json()
            except: result["anticaptcha"] = {"error": "failed"}
        return result
    
    def solve_recaptcha_v2(self, sitekey, page_url, timeout=120):
        """Solve reCAPTCHA v2."""
        if self.capsolver_key:
            try:
                payload = {"clientKey": self.capsolver_key, "task": {
                    "type": "ReCaptchaV2TaskProxyless",
                    "websiteURL": page_url, "websiteKey": sitekey}}
                r = requests.post(f"{self._capsolver_url}/createTask",
                                json=payload, timeout=30)
                data = r.json()
                if data.get("errorId", 1) == 0:
                    task_id = data.get("taskId")
                    for _ in range(timeout // 5):
                        time.sleep(5)
                        r = requests.post(f"{self._capsolver_url}/getTaskResult",
                            json={"clientKey": self.capsolver_key, 
                                 "taskId": task_id}, timeout=15)
                        result = r.json()
                        if result.get("status") == "ready":
                            return result.get("solution", {}).get(
                                "gRecaptchaResponse")
                        elif result.get("status") != "processing":
                            break
            except: pass
        
        if self.twocaptcha_key:
            try:
                params = {"key": self.twocaptcha_key, "method": "userrecaptcha",
                         "googlekey": sitekey, "pageurl": page_url, "json": 1}
                r = requests.post(f"{self._2c_url}/in.php",
                                data=params, timeout=30)
                if r.json().get("status") == 1:
                    task_id = r.json().get("request")
                    for _ in range(timeout // 5):
                        time.sleep(5)
                        r = requests.get(f"{self._2c_url}/res.php",
                            params={"key": self.twocaptcha_key, "id": task_id,
                                   "action": "get", "json": 1}, timeout=15)
                        result = r.json()
                        if result.get("status") == 1:
                            return result.get("request")
                        elif "CAPCHA_NOT_READY" not in result.get("request", ""):
                            break
            except: pass
        
        return None


def inject_turnstile_token(page, token: str) -> bool:
    """Inject a solved Turnstile token into the page.
    
    This is the critical step after solving. The token must be injected
    into the exact element that Cloudflare Turnstile is monitoring.
    
    Steel Browser approach: inject via evaluate + dispatch events
    Firecrawl approach: proxy the request with solved token
    """
    try:
        result = page.evaluate(f"""
        () => {{
            const token = `{token}`;
            let result = 'not_found';
            
            // Method 1: textarea[name="cf-turnstile-response"]
            const ta = document.querySelector('textarea[name="cf-turnstile-response"]');
            if (ta) {{
                ta.value = token;
                ta.dispatchEvent(new Event('change', {{bubbles: true}}));
                ta.dispatchEvent(new Event('input', {{bubbles: true}}));
                return 'textarea[name]';
            }}
            
            // Method 2: input[name="cf-turnstile-response"]  
            const inp = document.querySelector('input[name="cf-turnstile-response"]');
            if (inp) {{
                inp.value = token;
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                return 'input[name]';
            }}
            
            // Method 3: any element with cf-turnstile in name/id
            for (const el of document.querySelectorAll('input, textarea')) {{
                const name = (el.getAttribute('name') || el.getAttribute('id') || '').toLowerCase();
                if (name.includes('cf') && name.includes('turnstile')) {{
                    el.value = token;
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return `element[${{el.tagName}}]`;
                }}
            }}
            
            // Method 4: Turnstile callback
            if (window.turnstileCallback) {{
                window.turnstileCallback(token);
                return 'callback';
            }}
            if (window.onTurnstileCallback) {{
                window.onTurnstileCallback(token);
                return 'onTurnstileCallback';
            }}
            
            // Method 5: find any hidden input with cf- in name
            for (const el of document.querySelectorAll('input[type="hidden"]')) {{
                const name = el.getAttribute('name') || '';
                if (name.toLowerCase().includes('cf') || name.toLowerCase().includes('turnstile')) {{
                    el.value = token;
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return `hidden[${{name}}]`;
                }}
            }}
            
            return result;
        }}
        """)
        print(f"[Turnstile Injection] {result}")
        return result != "not_found"
    except Exception as e:
        logger.error(f"Turnstile injection error: {e}")
        return False


def inject_recaptcha_token(page, token: str) -> bool:
    """Inject reCAPTCHA v2/v3 solution token."""
    try:
        page.evaluate(f"""
        () => {{
            const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
            if (ta) {{
                ta.value = `{token}`;
                ta.dispatchEvent(new Event('change', {{bubbles: true}}));
                return true;
            }}
            return false;
        }}
        """)
        return True
    except:
        return False


def apply_stealth_to_page(page):
    """Apply Steel Browser stealth techniques to a Playwright page.
    
    Must be called on every new page OR via BrowserContext.add_init_script().
    """
    page.evaluate(StealthBrowser.fingerprint_injection_js())


def create_stealth_context(browser):
    """Create a BrowserContext with automatic stealth injection.
    
    This is the recommended way to use stealth: add the fingerprint
    injection as an init script so it runs on ALL pages automatically.
    
    Based on Steel Browser's context creation in cdp.service.ts.
    
    Usage:
        browser = playwright.chromium.launch(headless=True, 
            args=StealthBrowser.launch_args())
        context = create_stealth_context(browser)
        page = context.new_page()
        page.goto("https://example.com")  # stealth auto-applied
    """
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=StealthBrowser.random_ua(),
        locale="en-US",
        timezone_id="America/Los_Angeles",
    )
    
    # This is the key: add_init_script runs on EVERY page load,
    # before any page JavaScript executes. This is how Steel Browser
    # ensures fingerprints are always in place.
    context.add_init_script(StealthBrowser.fingerprint_injection_js())
    
    # Also set up viewport
    context.set_default_viewport({"width": 1920, "height": 1080})
    
    return context


class CaptchaSolver:
    """Main interface for CAPTCHA solving.
    
    Combines stealth browser techniques with external CAPTCHA APIs.
    
    Usage:
        solver = CaptchaSolver(
            capsolver_key="CAPxxx",
            twocaptcha_key="xxx",
        )
        
        # Solve Turnstile
        token = solver.solve_turnstile("0x4AAAAAAAc4qhUEsytXBEJx", "https://openrouter.ai")
        
        # Check balances
        print(solver.get_balance())
    """
    
    def __init__(self, capsolver_key=None, twocaptcha_key=None, 
                 anticaptcha_key=None):
        self.api = CaptchaAPISolver(capsolver_key, twocaptcha_key, 
                                    anticaptcha_key)
        self.stealth = StealthBrowser()
    
    def is_configured(self) -> bool:
        return self.api.is_configured()
    
    def solve_turnstile(self, sitekey: str, page_url: str = None,
                       timeout: int = 120) -> Optional[str]:
        """Solve Cloudflare Turnstile CAPTCHA."""
        if page_url is None:
            for name, info in KNOWN_CAPTCHAS.items():
                if info["sitekey"] == sitekey:
                    page_url = info["url"]
                    break
            if page_url is None:
                page_url = "https://example.com"
        
        return self.api.solve_turnstile(sitekey, page_url, timeout)
    
    def solve_recaptcha_v2(self, sitekey: str, page_url: str,
                          timeout: int = 120) -> Optional[str]:
        return self.api.solve_recaptcha_v2(sitekey, page_url, timeout)
    
    def get_balance(self) -> Dict[str, Any]:
        return self.api.get_balance()
    
    def solve_and_inject(self, page, sitekey: str = "0x4AAAAAAAc4qhUEsytXBEJx",
                        page_url: str = "https://openrouter.ai",
                        timeout: int = 120) -> Optional[str]:
        """Solve CAPTCHA and inject into page. Complete workflow."""
        # Apply stealth first
        apply_stealth_to_page(page)
        
        # Solve
        token = self.solve_turnstile(sitekey, page_url, timeout)
        
        if token:
            if inject_turnstile_token(page, token):
                return token
        
        return None
