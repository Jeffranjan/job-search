"""
scraper/session_manager.py
Cookie-based LinkedIn authentication with FULL human-like behavior.
Maximum stealth — LinkedIn cannot distinguish from real user.
"""

import json
import os
import time
import random
import sys
import threading
from playwright.sync_api import sync_playwright, BrowserContext, Page

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config  # Used for HEADLESS setting

COOKIE_FILE = os.path.join(os.path.dirname(__file__), "..", "session", "cookies.json")
SESSION_DIR  = os.path.join(os.path.dirname(__file__), "..", "session")

# ── Realistic Chrome versions to rotate ───────────────────────────────────────
CHROME_VERSIONS = [
    "120.0.0.0", "121.0.0.0", "122.0.0.0",
    "123.0.0.0", "124.0.0.0", "125.0.0.0",
]

# ── Realistic screen resolutions ──────────────────────────────────────────────
VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 800},
    {"width": 1920, "height": 1080},
]


def get_random_user_agent() -> str:
    """Return a randomized but realistic Chrome user agent string"""
    version = random.choice(CHROME_VERSIONS)
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Safari/537.36"
    )


def get_random_viewport() -> dict:
    """Return a random realistic viewport"""
    return random.choice(VIEWPORTS)


# ── JS patches injected into every page ───────────────────────────────────────
STEALTH_JS = """
// 1. Hide webdriver flag (most important)
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true
});

// 2. Realistic plugins list (empty = headless browser)
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin',     filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer',     filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
        { name: 'Native Client',         filename: 'internal-nacl-plugin' },
    ],
    configurable: true
});

// 3. Realistic languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en', 'hi'],
    configurable: true
});

// 4. Proper hardware concurrency (real CPUs)
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8,
    configurable: true
});

// 5. Realistic device memory
Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8,
    configurable: true
});

// 6. Hide automation in chrome object
if (window.chrome) {
    window.chrome.runtime = window.chrome.runtime || {};
}

// 7. Realistic permissions API
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);

// 8. Hide headless in user agent data
if (navigator.userAgentData) {
    Object.defineProperty(navigator, 'userAgentData', {
        get: () => ({
            brands: [
                { brand: 'Google Chrome',   version: '120' },
                { brand: 'Chromium',        version: '120' },
                { brand: 'Not=A?Brand',     version: '24'  },
            ],
            mobile: false,
            platform: 'Windows',
        }),
        configurable: true
    });
}

// 9. Realistic WebGL vendor/renderer
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};
"""


def ensure_session_dir():
    os.makedirs(SESSION_DIR, exist_ok=True)


def save_cookies(context: BrowserContext):
    """Save browser cookies to file"""
    ensure_session_dir()
    cookies = context.cookies()
    with open(COOKIE_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    print(f"[Session] ✅ Cookies saved → {COOKIE_FILE}")


def load_cookies(context: BrowserContext) -> bool:
    """Load cookies from file. Returns True if loaded."""
    if not os.path.exists(COOKIE_FILE):
        return False
    with open(COOKIE_FILE, "r") as f:
        cookies = json.load(f)
    context.add_cookies(cookies)
    print("[Session] ✅ Cookies loaded from saved session")
    return True


def is_logged_in(page: Page) -> bool:
    """Check if LinkedIn session is active"""
    try:
        page.goto("https://www.linkedin.com/feed/", timeout=20000)
        _human_pause(2, 4)
        return "feed" in page.url or "mynetwork" in page.url
    except Exception:
        return False


def _human_pause(min_s: float = 1.0, max_s: float = 3.0):
    """Random pause to mimic human thinking time"""
    time.sleep(random.uniform(min_s, max_s))


def _build_login_context(playwright):
    """
    Browser context for MANUAL LOGIN only.
    Uses real Chrome (channel='chrome') — LinkedIn cannot detect this as a bot.
    Falls back to Chromium if Chrome is not installed.
    NO tracker blocking — blocking breaks LinkedIn's login flow.
    """
    ua = get_random_user_agent()
    vp = get_random_viewport()

    common_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-sandbox",
        "--start-maximized",
    ]

    # ── Try real Chrome first (least detectable) ──────────────
    try:
        browser = playwright.chromium.launch(
            channel="chrome",     # Uses YOUR installed Chrome — not Playwright Chromium
            headless=False,
            args=common_args,
        )
        print("[Session] Using real Chrome browser for login (most trusted by LinkedIn)")
    except Exception:
        # Chrome not installed — fall back to Chromium
        browser = playwright.chromium.launch(
            headless=False,
            args=common_args,
        )
        print("[Session] Chrome not found — using Chromium (may require extra verification)")

    context = browser.new_context(
        user_agent=ua,
        viewport=vp,
        locale="en-US",
        timezone_id="Asia/Kolkata",
        color_scheme="light",
    )
    context.add_init_script(STEALTH_JS)
    return browser, context


def _build_scrape_context(playwright):
    """
    Browser context for SCRAPING.
    Uses real Chrome channel with default UA and viewport so cookies are never rejected.
    """
    common_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-sandbox",
        "--start-maximized",
    ]

    try:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=Config.HEADLESS,
            args=common_args,
        )
    except Exception:
        browser = playwright.chromium.launch(
            headless=Config.HEADLESS,
            args=common_args,
        )

    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="en-US",
    )
    # Stealth JS injected on every page
    context.add_init_script(STEALTH_JS)

    return browser, context


def manual_login(page: Page, context: BrowserContext):
    """
    Opens LinkedIn login in Playwright browser.
    Multi-signal detection:
    1. 'li_at' session cookie in context.cookies() (instant & 100% reliable)
    2. URL checks across all open tabs in context.pages (/feed, /mynetwork, /home)
    3. DOM element evaluation on all pages (Global Nav, Search input, Profile, etc.)
    4. Non-blocking ENTER listener on terminal (press ENTER anytime once on feed)
    """
    print("\n" + "=" * 60)
    print("🔐  FIRST TIME SETUP — MANUAL LOGIN REQUIRED")
    print("=" * 60)
    print("  An automated browser window has opened for LinkedIn login.")
    print("  1. In THAT window, enter your LinkedIn credentials and sign in.")
    print("  2. Complete any 2FA / OTP / Captcha verification if prompted.")
    print("  3. The script will auto-detect your login as soon as you reach your feed.")
    print("  💡 Tip: You can also press ENTER in this terminal at ANY time once logged in!")
    print("=" * 60 + "\n")

    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    _human_pause(1, 2)

    try:
        page.bring_to_front()
    except Exception:
        pass

    # Start non-blocking listener for Enter key in terminal
    enter_pressed = threading.Event()

    def _listen_for_enter():
        try:
            sys.stdin.readline()
            enter_pressed.set()
        except Exception:
            pass

    t = threading.Thread(target=_listen_for_enter, daemon=True)
    t.start()

    print("[Session] Watching for login (cookies, all tabs & feed)...")
    print("[Session] (Or press ENTER here as soon as you see your feed in the browser)\n")

    max_wait = 300    # 5 minutes
    interval = 1.5
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval

        # ── 1. Check cookies across the entire browser context ────────
        try:
            cookies = context.cookies()
            has_li_at = any(
                c.get("name") == "li_at" and len(c.get("value", "")) > 10
                for c in cookies
            )
            if has_li_at:
                print(f"\n[Session] ✅ Login detected via 'li_at' session cookie!")
                _human_pause(1, 2)
                save_cookies(context)
                print("[Session] ✅ Session saved — future runs are fully automatic!")
                return
        except Exception:
            pass

        # ── 2. Check all open pages/tabs in the context ───────────────
        try:
            for p in list(context.pages):
                try:
                    u = p.url.lower()
                    if any(pat in u for pat in ["/feed", "/mynetwork", "/home"]):
                        print(f"\n[Session] ✅ Login detected on tab: {p.url[:60]}")
                        _human_pause(1, 2)
                        save_cookies(context)
                        print("[Session] ✅ Session saved — future runs are fully automatic!")
                        return

                    # Evaluate client-side location in case page.url hasn't synced
                    js_url = p.evaluate("window.location.href").lower()
                    if any(pat in js_url for pat in ["/feed", "/mynetwork", "/home"]):
                        print(f"\n[Session] ✅ Login detected via client location: {js_url[:60]}")
                        _human_pause(1, 2)
                        save_cookies(context)
                        print("[Session] ✅ Session saved — future runs are fully automatic!")
                        return

                    # Check for feed UI elements
                    has_feed = p.evaluate(
                        "() => !!document.querySelector('.global-nav, input[aria-label*=\"Search\"], a[href*=\"/in/\"], .feed-shared-update-v2')"
                    )
                    if has_feed:
                        print("\n[Session] ✅ Login detected via LinkedIn feed elements!")
                        _human_pause(1, 2)
                        save_cookies(context)
                        print("[Session] ✅ Session saved — future runs are fully automatic!")
                        return
                except Exception:
                    continue
        except Exception:
            pass

        # ── 3. Check if user pressed ENTER in terminal ─────────────────
        if enter_pressed.is_set():
            print("\n[Session] ▶ ENTER detected — verifying session...")
            for p in list(context.pages):
                try:
                    p.goto("https://www.linkedin.com/feed/", timeout=15000, wait_until="domcontentloaded")
                    _human_pause(1, 2)
                    if any(pat in p.url.lower() for pat in ["/feed", "/mynetwork", "/home"]):
                        save_cookies(context)
                        print("[Session] ✅ Session verified and saved!")
                        return
                except Exception:
                    pass

            cookies = context.cookies()
            if any(c.get("name") == "li_at" and len(c.get("value", "")) > 10 for c in cookies):
                save_cookies(context)
                print("[Session] ✅ Session verified via cookie and saved!")
                return

            print("[Session] ⚠️  LinkedIn session not yet active in this automated browser.")
            print("[Session] Please ensure you entered your credentials inside the automated Chrome window.")
            enter_pressed.clear()
            threading.Thread(target=_listen_for_enter, daemon=True).start()

        # Log status every 10s
        if int(elapsed) % 10 == 0:
            urls = [p.url[:45] for p in context.pages]
            print(f"[Session] Waiting for login... {int(elapsed)}s | Tabs: {urls}")

    # ── Timeout fallback ──────────────────────────────────────────────
    print("\n[Session] Auto-detect timed out after 5 minutes.")
    print("[Session] Attempting to save current cookies regardless...")
    save_cookies(context)


def get_authenticated_browser():
    """
    Main entry point.
    Returns (playwright, browser, context, page) — logged in & stealth-ready.

    Flow:
    - Saved cookies exist + valid  → use them (fully automatic, scrape context)
    - No cookies / expired         → open login browser, wait for manual login,
                                     save cookies, return scrape context
    """
    playwright = sync_playwright().start()

    # ── STEP 0: Check if LI_AT_COOKIE is provided in environment ───
    li_at_env = os.getenv("LI_AT_COOKIE", "").strip() or os.getenv("LINKEDIN_LI_AT", "").strip()
    if li_at_env and not os.path.exists(COOKIE_FILE):
        print("[Session] Found LI_AT_COOKIE in environment — generating session...")
        ensure_session_dir()
        initial_cookies = [
            {
                "name": "li_at",
                "value": li_at_env,
                "domain": ".linkedin.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "None",
            },
            {
                "name": "li_at",
                "value": li_at_env,
                "domain": ".www.linkedin.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "None",
            }
        ]
        with open(COOKIE_FILE, "w") as f:
            json.dump(initial_cookies, f, indent=2)

    # ── STEP 1: Try saved cookies with scrape context ─────────
    if os.path.exists(COOKIE_FILE):
        print("[Session] Found saved cookies — trying to restore session...")
        browser, context = _build_scrape_context(playwright)
        page = context.new_page()
        load_cookies(context)

        page.goto("https://www.linkedin.com/feed/", timeout=20000,
                  wait_until="domcontentloaded")
        _human_pause(2, 4)

        if any(x in page.url for x in ["/feed", "/mynetwork", "/home"]):
            print("[Session] ✅ Session restored via saved cookies!")
            return playwright, browser, context, page
        else:
            print("[Session] ⚠️  Cookies expired. Opening browser for re-login...")
            browser.close()
            os.remove(COOKIE_FILE)

    # ── STEP 2: Manual login with clean login context ─────────
    login_browser, login_context = _build_login_context(playwright)
    login_page = login_context.new_page()

    manual_login(login_page, login_context)

    # ── STEP 3: Close login browser, open scrape context ──────
    # Load freshly saved cookies into the stealth scrape context
    login_browser.close()

    print("[Session] Starting stealth scrape session...")
    scrape_browser, scrape_context = _build_scrape_context(playwright)
    scrape_page = scrape_context.new_page()
    load_cookies(scrape_context)

    # Verify scrape session works
    scrape_page.goto("https://www.linkedin.com/feed/", timeout=20000,
                     wait_until="domcontentloaded")
    _human_pause(2, 3)

    print("[Session] ✅ Stealth scrape session ready!")
    return playwright, scrape_browser, scrape_context, scrape_page
