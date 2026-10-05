"""Real macOS Safari. Fixed-width frames check layout, never iPhone/iOS behavior."""

import json
import platform
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
HERO_CTA = ".первый-экран__действие a"
PHONES = {"tel:+79510317826", "tel:+79015496383"}
SIZES = [
    ("desktop", 1440, 900), ("desktop", 1280, 600),
    ("desktop", 1024, 700), ("mobile-layout", 375, 548),
    ("mobile-layout", 390, 664), ("mobile-layout", 430, 750),
    ("mobile-layout", 390, 844), ("tablet-layout", 768, 900),
    ("landscape-layout", 844, 390),
]

report = {
    "started_utc": datetime.now(timezone.utc).isoformat(),
    "status": "not_checked", "platform": platform.platform(),
    "scope": "Real desktop Safari on a temporary macOS VM. Fixed-size same-origin "
             "iframes test responsive layout; they do not emulate iPhone, iOS, touch, "
             "safe areas or mobile browser chrome. tel: links are checked, not dialed.",
    "browser": {}, "views": [], "checks": [], "errors": [], "warnings": [],
}


def check(view, name, passed, details=None, category="behavior"):
    report["checks"].append({"view": view, "category": category, "name": name,
                             "passed": bool(passed), "details": details})


def warning(view, name, details=None):
    report["warnings"].append({"view": view, "name": name, "details": details})


def attempt(view, name, fn):
    try:
        return fn()
    except Exception as exc:
        report["errors"].append({"view": view, "name": name,
                                 "error": f"{type(exc).__name__}: {exc}",
                                 "traceback": traceback.format_exc(limit=3)})
        return None


class SiteHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/__safari_harness__.html":
            body = (b'<!doctype html><html><head><meta charset="utf-8"><style>'
                    b'html,body{margin:0;background:#eee}iframe{display:block;'
                    b'border:0;margin:0}</style></head><body>'
                    b'<iframe id="site" title="responsive layout test"></iframe>'
                    b'</body></html>')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def log_message(self, *_args):
        pass


@contextmanager
def local_server():
    handler = partial(SiteHandler, directory=str(ROOT / "site"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def wait_ready(driver):
    WebDriverWait(driver, 15).until(
        lambda d: d.execute_script("return document.readyState === 'complete' && "
                                   "location.pathname === '/index.html' && !!document.querySelector('h1')"))
    driver.set_script_timeout(15)
    driver.execute_async_script("""
        const done = arguments[arguments.length - 1];
        Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 10000))])
          .then(() => requestAnimationFrame(() => requestAnimationFrame(done)));
    """)


def viewport(driver):
    return driver.execute_script("""
        return {width:innerWidth,height:innerHeight,devicePixelRatio:devicePixelRatio,
                scrollX:scrollX,scrollY:scrollY,outerWidth:outerWidth,outerHeight:outerHeight};
    """)


def resize_desktop(driver, width, height):
    driver.switch_to.default_content()
    driver.set_window_rect(x=0, y=0, width=width, height=height)
    for _ in range(4):
        actual = viewport(driver)
        if actual["width"] == width and actual["height"] == height:
            break
        outer = driver.get_window_rect()
        driver.set_window_rect(x=0, y=0,
                               width=outer["width"] + width - actual["width"],
                               height=outer["height"] + height - actual["height"])
        time.sleep(0.15)
    return viewport(driver)


def load_view(driver, origin, mode, width, height, view):
    driver.switch_to.default_content()
    frame = None
    if mode == "desktop":
        resize_desktop(driver, width, height)
        driver.get(origin + "/index.html")
    else:
        resize_desktop(driver, max(1024, width + 30), max(1050, height + 30))
        driver.get(origin + "/__safari_harness__.html")
        frame = driver.find_element(By.ID, "site")
        driver.execute_script("""
            const f=arguments[0]; f.style.width=arguments[1]+'px';
            f.style.height=arguments[2]+'px'; f.src=arguments[3];
        """, frame, width, height, origin + "/index.html")
        driver.switch_to.frame(frame)
    wait_ready(driver)
    actual = viewport(driver)
    entry = {"id": view, "mode": mode, "requested": {"width": width, "height": height},
             "actual": actual, "screenshots": []}
    report["views"].append(entry)
    if actual["width"] != width or actual["height"] != height:
        warning(view, "Requested viewport was not achieved; only actual size was checked", actual)
    return frame, entry


def screenshot(driver, frame, entry, label):
    path = REPORTS / f'{entry["id"]}-{label}.png'
    if frame is None:
        driver.save_screenshot(str(path))
        entry["screenshots"].append({"file": path.name, "kind": "window"})
        return
    driver.switch_to.default_content()
    try:
        frame.screenshot(str(path))
        entry["screenshots"].append({"file": path.name, "kind": "iframe-element"})
    except Exception as exc:
        driver.save_screenshot(str(path))
        entry["screenshots"].append({"file": path.name, "kind": "outer-window-fallback"})
        warning(entry["id"], "Iframe screenshot fallback", str(exc))
    finally:
        driver.switch_to.frame(frame)


def scroll_y(driver, y):
    driver.execute_script("window.scrollTo(0, arguments[0])", y)
    time.sleep(0.35)


def overflow(driver, view, phase):
    data = driver.execute_script("""
        const w=innerWidth;
        return {viewport:w,document:document.documentElement.scrollWidth,
            body:document.body.scrollWidth,scrollY:scrollY,
            suspects:Array.from(document.querySelectorAll('body *')).map(e=>{
                const r=e.getBoundingClientRect(),s=getComputedStyle(e);
                return {tag:e.tagName,class:e.className.baseVal||e.className,
                        left:r.left,right:r.right,width:r.width,position:s.position,
                        display:s.display,visibility:s.visibility};
            }).filter(e=>e.width>0&&e.visibility!=='hidden'&&
                         (e.left < -1||e.right>w+1)).slice(0,35)};
    """)
    check(view, f"No horizontal overflow ({phase})",
          max(data["document"], data["body"]) <= data["viewport"] + 1, data, "layout")


def hero_check(driver, view):
    data = driver.execute_script("""
        const e=document.querySelector(arguments[0]); if(!e) return null;
        const r=e.getBoundingClientRect();
        const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
        return {rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom},
          viewport:{width:innerWidth,height:innerHeight},
          inside:r.top>=0&&r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight,
          unobstructed:!!hit&&(hit===e||e.contains(hit)),href:e.getAttribute('href')};
    """, HERO_CTA)
    # On short phone windows the hero button may sit below the first screen while the
    # sticky call bar is shown instead; that case is a warning, the sticky bar is checked separately.
    if data and data["inside"] and data["unobstructed"]:
        check(view, "Hero call button visible without scrolling", True, data, "layout")
    else:
        warning(view, "Hero call button below first screen (sticky call bar expected)", data)


def sticky_bar_check(driver, view):
    scroll_y(driver, 1400)
    data = driver.execute_script("""
        const e=document.querySelector('.липкая-кнопка a'); if(!e) return null;
        const r=e.getBoundingClientRect(), s=getComputedStyle(e.parentElement);
        return {display:s.display,rect:{top:r.top,bottom:r.bottom,left:r.left,right:r.right},
                viewport:{width:innerWidth,height:innerHeight},href:e.getAttribute('href')};
    """)
    ok = bool(data and data["rect"]["bottom"] <= data["viewport"]["height"] + 1
              and data["rect"]["top"] >= 0 and data["rect"]["right"] <= data["viewport"]["width"] + 1)
    check(view, "Sticky call bar visible after scrolling (narrow layouts)", ok, data, "layout")
    scroll_y(driver, 0)


def phones_check(driver, view):
    hrefs = driver.execute_script(
        "return Array.from(document.querySelectorAll('a[href^=\"tel:\"]')).map(a=>a.getAttribute('href'))")
    check(view, "Only the two expected tel: numbers (links are never dialed)",
          set(hrefs) == PHONES and len(hrefs) >= 6, hrefs, "content")


def fonts_check(driver, view):
    data = driver.execute_script("""
        const faces=Array.from(document.fonts).map(f=>({family:f.family,weight:f.weight,status:f.status}));
        return {status:document.fonts.status,faces,
          heading:getComputedStyle(document.querySelector('h1')).fontFamily,
          googleRequests:performance.getEntriesByType('resource').filter(r=>/googleapis|gstatic/.test(r.name)).length};
    """)
    loaded = [f for f in data["faces"] if f["status"] == "loaded"]
    check(view, "Own fonts load (Montserrat and PT Sans), no Google font requests",
          not any(f["status"] == "error" for f in data["faces"]) and len(loaded) >= 2
          and data["googleRequests"] == 0, data, "assets")


def images_check(driver, view):
    height = driver.execute_script("return document.documentElement.scrollHeight")
    for y in range(0, height + 600, 600):
        scroll_y(driver, y)
    time.sleep(1.0)
    data = driver.execute_script("""
        return Array.from(document.images).map(i=>({src:i.getAttribute('src'),
          currentSrc:i.currentSrc,complete:i.complete,naturalWidth:i.naturalWidth,
          naturalHeight:i.naturalHeight,width:i.width,height:i.height,
          hasSizeAttrs:i.hasAttribute('width')&&i.hasAttribute('height')}));
    """)
    check(view, "All seven page images loaded after scrolling",
          len(data) == 7 and all(x["complete"] and x["naturalWidth"] > 0 for x in data), data, "assets")
    check(view, "Every image declares width and height (reserved space)",
          all(x["hasSizeAttrs"] for x in data), data, "layout")
    overflow(driver, view, "images loaded")
    scroll_y(driver, 0)


def metadata_check(driver, view):
    data = driver.execute_script("""
        const canonical=document.querySelector('link[rel="canonical"]');
        const schemas=Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
          .map(e=>{try{return JSON.parse(e.textContent)}catch(err){return {parseError:String(err)}}});
        const robots=document.querySelector('meta[name="robots"]');
        return {canonical:canonical&&canonical.href,schemas,robots:robots&&robots.content,
                title:document.title};
    """)
    check(view, "Canonical points to production URL",
          data["canonical"] == "https://bez-zasora56.ru/", data["canonical"], "metadata")
    plumber = [s for s in data["schemas"] if isinstance(s, dict) and s.get("@type") == "Plumber"]
    check(view, "Valid Plumber JSON-LD with both phones",
          len(plumber) == 1 and set(plumber[0].get("telephone", [])) == {"+79510317826", "+79015496383"},
          data["schemas"], "metadata")
    check(view, "Page is open to search engines",
          "noindex" not in (data["robots"] or ""), data["robots"], "metadata")


def shot_at(driver, frame, entry, y, label):
    scroll_y(driver, y)
    screenshot(driver, frame, entry, label)
    scroll_y(driver, 0)


def view_checks(driver, origin, mode, width, height, index):
    view = f"{mode}-{width}x{height}"
    frame, entry = load_view(driver, origin, mode, width, height, view)
    attempt(view, "Fonts", lambda: fonts_check(driver, view))
    attempt(view, "Hero screenshot", lambda: screenshot(driver, frame, entry, "hero"))
    attempt(view, "Fresh horizontal overflow", lambda: overflow(driver, view, "fresh load"))
    attempt(view, "Hero call button position", lambda: hero_check(driver, view))
    attempt(view, "Phones", lambda: phones_check(driver, view))
    if width < 900:
        attempt(view, "Sticky call bar", lambda: sticky_bar_check(driver, view))
    attempt(view, "Middle screenshot", lambda: shot_at(driver, frame, entry, 1800, "middle"))
    attempt(view, "Image loading", lambda: images_check(driver, view))
    attempt(view, "Bottom screenshot", lambda: shot_at(driver, frame, entry, 10 ** 6, "bottom"))
    if index == 0:
        attempt(view, "Metadata", lambda: metadata_check(driver, view))


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    driver = None
    try:
        if not (ROOT / "site" / "index.html").is_file():
            raise FileNotFoundError("Built site/index.html is missing")
        with local_server() as origin:
            try:
                driver = webdriver.Safari()
            except Exception as exc:
                report["errors"].append({"category": "launch", "name": "Safari did not start",
                                         "error": f"{type(exc).__name__}: {exc}"})
                return 2
            driver.set_page_load_timeout(25)
            report["browser"] = {"capabilities": driver.capabilities,
                                 "userAgent": driver.execute_script("return navigator.userAgent")}
            is_safari = driver.capabilities.get("browserName", "").lower() == "safari"
            check("global", "Actual Safari browser", is_safari, report["browser"], "environment")
            if not is_safari:
                return 2
            report["status"] = "checking"
            for index, (mode, width, height) in enumerate(SIZES):
                print(f"Checking {mode} {width}x{height}", flush=True)
                attempt(f"{mode}-{width}x{height}", "View checks",
                        lambda mode=mode, width=width, height=height, index=index:
                        view_checks(driver, origin, mode, width, height, index))
            failed = [c for c in report["checks"] if not c["passed"]]
            report["status"] = "failed" if failed or report["errors"] else "passed"
            return 1 if report["status"] == "failed" else 0
    except Exception as exc:
        report["errors"].append({"category": "harness", "name": "Unexpected harness error",
                                 "error": f"{type(exc).__name__}: {exc}",
                                 "traceback": traceback.format_exc(limit=5)})
        if report["status"] != "not_checked":
            report["status"] = "failed"
        return 2
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                warning("global", "Safari cleanup", str(exc))
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report["summary"] = {"passed": sum(c["passed"] for c in report["checks"]),
                             "failed": sum(not c["passed"] for c in report["checks"]),
                             "errors": len(report["errors"]),
                             "warnings": len(report["warnings"]),
                             "views": len(report["views"])}
        (REPORTS / "safari-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], **report["summary"]}), flush=True)


if __name__ == "__main__":
    sys.exit(main())
