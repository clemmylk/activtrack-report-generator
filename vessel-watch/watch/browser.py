from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit


class NeedsAttention(RuntimeError):
    pass


class AccessRequired(NeedsAttention):
    """User must resolve normal sign-in / verification; never automatically retried."""


class VesselUnavailable(NeedsAttention):
    """One vessel is missing from this site's search, not a whole-site failure."""


class Browser:
    """Own browser profile. No cookies/passwords are exported or copied from Chrome."""
    def __init__(self, profile, headless=True, stop=None, family="edge"):
        from selenium import webdriver
        if family not in {"edge", "chrome"}:
            raise ValueError("Browser must be edge or chrome")
        if family == "edge":
            from selenium.webdriver.edge.service import Service
            options = webdriver.EdgeOptions()
            launch = webdriver.Edge
        else:
            from selenium.webdriver.chrome.service import Service
            options = webdriver.ChromeOptions()
            launch = webdriver.Chrome
        self.family = family
        self.site_tabs = {}
        self.closed = False
        self.max_tabs = 2
        self.stop = stop
        options.add_argument("--user-data-dir=" + str(Path(profile).resolve()))
        options.add_argument("--window-size=1500,1000")
        if headless:
            options.add_argument("--headless=new")
        options.page_load_strategy = "eager"
        options.set_capability("unhandledPromptBehavior", "ignore")
        service = Service(log_output=subprocess.DEVNULL)
        if os.name == "nt":
            service.creation_flags = subprocess.CREATE_NO_WINDOW
        self.driver = launch(options=options, service=service)
        self.driver.set_page_load_timeout(35)
        self.driver.set_script_timeout(35)

    def check(self):
        if self.stop and self.stop.is_set():
            raise InterruptedError("Stopped by user")

    def wait(self, seconds):
        if self.stop:
            if self.stop.wait(seconds):
                raise InterruptedError("Stopped by user")
        else:
            time.sleep(seconds)

    def guard(self):
        self.check()
        text = (self.driver.find_element("tag name", "body").text or "")[:6000].lower()
        title = (self.driver.title or "").lower()
        indicators = ["verify you are human", "verify that you are human", "checking your browser", "access denied",
                      "unusual traffic", "please complete the captcha", "performing security verification", "just a moment..."]
        if any(p in text or p in title for p in indicators):
            raise AccessRequired("Human verification or access restriction — open this site and complete its normal verification.")
        frames = self.driver.find_elements("css selector", "iframe[src*='_Incapsula_Resource'],iframe[src*='challenge-platform'],iframe[src*='recaptcha'][src*='bframe'],iframe[title*='challenge']")
        if any(e.is_displayed() for e in frames):
            raise AccessRequired("Site access challenge — complete it yourself in the tracker browser, then Resume pending.")
        if any(e.is_displayed() for e in self.driver.find_elements("css selector", "input[type='password']")):
            raise AccessRequired("Sign-in required in the tracker browser.")

    def site_tab(self, site_id):
        """Bound the owned browser to two tabs; old pages can be reopened from saved URLs."""
        handle = self.site_tabs.get(site_id)
        if handle in self.driver.window_handles:
            self.site_tabs.pop(site_id)
            self.site_tabs[site_id] = handle
            self.driver.switch_to.window(handle)
            self.driver.switch_to.default_content()
            return True
        live = self.driver.window_handles
        self.site_tabs = {s:h for s,h in self.site_tabs.items() if h in live}
        if not self.site_tabs:
            self.driver.switch_to.window(live[0])
        elif len(live) >= self.max_tabs:
            old = next(iter(self.site_tabs))
            self.driver.switch_to.window(self.site_tabs.pop(old))
            self.driver.switch_to.default_content()
        else:
            self.driver.switch_to.new_window("tab")
        self.site_tabs[site_id] = self.driver.current_window_handle
        return False

    def remember_site_tab(self, site_id):
        self.site_tabs[site_id] = self.driver.current_window_handle
        current = self.driver.current_window_handle
        while len(self.driver.window_handles) > self.max_tabs:
            old = next(h for h in self.driver.window_handles if h != current)
            self.driver.switch_to.window(old)
            self.driver.close()
            self.site_tabs = {s:h for s,h in self.site_tabs.items() if h != old}
        self.driver.switch_to.window(current)

    def show_site(self, site):
        if not self.site_tab(site["id"]):
            self.open_for_user(site["url"])

    def open_for_user(self, url):
        """Leave login/challenge visible for the user; never solve it programmatically."""
        self.driver.get(url)

    def navigate(self, url):
        from selenium.webdriver.support.ui import WebDriverWait
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
            raise ValueError("Use an ordinary website link without embedded credentials.")
        if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Remote tracking sites must use HTTPS.")
        self.driver.get(url)
        WebDriverWait(self.driver,10).until(lambda d:d.find_elements('tag name','body'))
        self.guard()

    def element(self, step):
        from selenium.webdriver.support.ui import WebDriverWait
        selector = step.get("selector", "")
        by = step.get("by", "css selector")
        def visible(_driver):
            self.check()
            return next((e for e in _driver.find_elements(by, selector) if e.is_displayed()), False)
        return WebDriverWait(self.driver, min(int(step.get("timeout", 15)), 35)).until(visible)

    def run_steps(self, steps, values):
        from selenium.common.exceptions import TimeoutException, UnexpectedAlertPresentException
        for i, step in enumerate(steps, 1):
            try:
                self._execute_steps([step], values)
            except (NeedsAttention, InterruptedError):
                raise
            except UnexpectedAlertPresentException:
                # Preserve the error so the bounded retry layer can distinguish
                # a known server-busy notice from a dialog requiring the user.
                raise
            except TimeoutException:
                self.guard()
                raise
            except Exception as exc:
                raise NeedsAttention(f"Step {i} ({step.get('op')}) failed: {type(exc).__name__}. Site instructions need checking.") from exc

    def _execute_steps(self, steps, values):
        from selenium.webdriver.support.ui import Select
        for step in steps:
            self.check()
            op = step["op"]
            value = str(step.get("value", "")).format_map(values)
            if op == "click":
                before = set(self.driver.window_handles)
                self.element(step).click()
                if step.get("opens_tab"):
                    deadline = time.monotonic() + 15
                    while not (set(self.driver.window_handles) - before) and time.monotonic() < deadline:
                        self.wait(.2)
                    new = set(self.driver.window_handles) - before
                    if not new:
                        raise NeedsAttention("Expected result tab did not open.")
                    self.driver.switch_to.window(next(iter(new)))
            elif op == "click_text":
                wanted = re.sub(r"\s+", " ", value).strip().upper()
                buttons = [e for e in self.driver.find_elements("css selector", "button,input[type=submit],input[type=button],a")
                           if e.is_displayed() and re.sub(r"\s+", " ", e.text or e.get_attribute("value") or "").strip().upper() == wanted]
                if len(buttons) != 1:
                    raise NeedsAttention(f"Expected one {value} control; found {len(buttons)}. Site recipe needs adjustment.")
                before = set(self.driver.window_handles)
                buttons[0].click()
                self.wait(2)
                new = set(self.driver.window_handles) - before
                if len(new) == 1:
                    self.driver.switch_to.window(next(iter(new)))
                elif len(new) > 1:
                    raise NeedsAttention("Several result tabs opened; choose the exact result in the site recipe.")
            elif op == "dismiss_cookie_banner":
                # Site-specific, optional nonessential-cookie rejection only.
                controls = [e for e in self.driver.find_elements('css selector', 'button')
                            if e.is_displayed() and e.text.strip().upper() == 'REJECT ALL']
                if len(controls) == 1:
                    controls[0].click()
                    self.wait(.5)
                elif len(controls) > 1:
                    raise NeedsAttention('Cookie rejection control is ambiguous.')
            elif op == "select_vessel":
                wanted = re.sub(r"\s+", " ", value).strip().upper()
                candidates = []
                for select_el in self.driver.find_elements("tag name", "select"):
                    select = Select(select_el)
                    for option in select.options:
                        label = re.sub(r"\s+", " ", option.text).strip().upper()
                        stripped = re.sub(r"^[A-Z0-9]{2,5}\s*-\s*", "", label)
                        if label == wanted or stripped == wanted:
                            candidates.append((select, option))
                if len(candidates) != 1:
                    raise VesselUnavailable(f"Exact vessel dropdown selection unavailable: {value}")
                select, option = candidates[0]
                select.select_by_visible_text(option.text)
            elif op == "choose_vessel_option":
                from selenium.webdriver.support.ui import WebDriverWait
                wanted = re.sub(r"\s+", " ", value).strip().upper()
                def option_ready(_driver):
                    self.check()
                    matches = [o for o in _driver.find_elements("css selector", "[role=option]") if o.is_displayed()
                               and re.sub(r"\s*\([A-Z0-9]+\)$", "", re.sub(r"\s+", " ", o.text).strip().upper()) == wanted]
                    return matches[0] if len(matches) == 1 else False
                from selenium.common.exceptions import TimeoutException
                try:
                    WebDriverWait(self.driver, 15).until(option_ready).click()
                except TimeoutException as exc:
                    self.guard()
                    raise VesselUnavailable(f"No exact search option for {value}. Other vessels will still be checked.") from exc
            elif op == "select_text":
                # Case/space normalisation only; never substring-select a different vessel.
                select = Select(self.element(step))
                wanted = re.sub(r"\s+", " ", value).strip().upper()
                matches = [o for o in select.options if re.sub(r"\s+", " ", o.text).strip().upper() == wanted]
                if len(matches) != 1:
                    raise NeedsAttention(f"Exact dropdown item unavailable or ambiguous: {value}")
                select.select_by_visible_text(matches[0].text)
            elif op == "fill":
                el = self.element(step)
                if el.get_attribute("type") == "password":
                    raise NeedsAttention("Enter your password yourself in the tracker browser.")
                el.clear()
                el.send_keys(value)
            elif op == "enter":
                from selenium.webdriver.common.keys import Keys
                self.element(step).send_keys(Keys.ENTER)
            elif op == "wait":
                self.element(step)
            elif op == "frame":
                self.driver.switch_to.frame(self.element(step))
            elif op == "top":
                self.driver.switch_to.default_content()
            elif op == "delay":
                self.wait(min(float(value or 1), 10))
            else:
                raise ValueError(f"Unsupported recipe operation: {op}")
            self.guard()

    def snapshot(self):
        self.guard()
        return self.driver.execute_script(r"""
const clean = el => (el.innerText || el.textContent || '').replace(/\s+/g,' ').trim();
const tables = [...document.querySelectorAll('table')].filter(t => !t.querySelector('table')).map(t => {
 const grid=[];
 [...t.rows].forEach((row, r) => {
   grid[r] ||= [];
   let col=0;
   [...row.cells].forEach(cell => {
     while(grid[r][col] !== undefined) col++;
     const text=clean(cell), rs=cell.rowSpan||1, cs=cell.colSpan||1;
     for(let i=0;i<rs;i++) { grid[r+i] ||= []; for(let j=0;j<cs;j++) grid[r+i][col+j]=text; }
     col+=cs;
   });
 });
 return {id:t.id, rows:grid};
});
return {url:location.href, title:document.title, text:document.body.innerText.slice(0,180000), tables,
 selects:[...document.querySelectorAll('select')].map(e=>({id:e.id,name:e.name, options:[...e.options].map(o=>({text:clean(o),value:o.value}))})),
 buttons:[...document.querySelectorAll('button,input[type=submit],input[type=button],a')].filter(e=>e.offsetWidth).map(e=>({text:clean(e)||e.value,id:e.id,href:e.getAttribute('href')})).slice(0,180),
 inputs:[...document.querySelectorAll('input:not([type=password]):not([type=hidden])')].map(e=>({id:e.id,name:e.name,placeholder:e.placeholder,type:e.type})),
 frames:[...document.querySelectorAll('iframe')].map(e=>({id:e.id,src:e.src}))};
""")

    def close(self):
        if not self.closed:
            self.driver.quit()
            self.closed = True
