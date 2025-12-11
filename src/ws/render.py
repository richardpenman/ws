# -*- coding: utf-8 -*-

import os, queue, re, signal, sys, threading, time, urllib, zipfile
from http.cookiejar import Cookie, CookieJar
from . import download, xpath
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By 
import undetected_chromedriver as uc


class CacheBrowser:
    def __init__(self, headless=True, cache=None, cookie_jar=None, cookie_key=None, proxy=None, init_callback=None, timeout=30):
        self.chrome_options = uc.ChromeOptions()
        self.chrome_options.add_argument("--disable-dev-shm-usage") # https://stackoverflow.com/a/50725918/1689770
        self.chrome_options.add_argument("--disable-gpu") #https://stackoverflow.com/questions/51959986/how-to-solve-selenium-chromedriver-timed-out-receiving-message-from-renderer-exc
        if headless:
            self.chrome_options.add_argument('--headless')
        if proxy:
            self.chrome_options.add_argument(f'--proxy-server={proxy}')
        self.init_callback = init_callback

        self.driver = None
        self.timeout = timeout
        self.cache = download.Download().cache if cache is None else cache
        self.cookie_key = cookie_key
        if cookie_key:
            try:
                self.cookies = self.cache[cookie_key]
                print('loading:', self.cookies)
            except KeyError:
                self.cookies = []
        else:
            self.cookies = self.format_cookies(cookie_jar)
        signal.signal(signal.SIGINT, self.exit_gracefully)

    def init(self):
        if self.driver is None:
            self.driver = uc.Chrome(options=self.chrome_options)
            self.driver.set_page_load_timeout(self.timeout)
            self.driver.set_script_timeout(self.timeout)
            if self.init_callback is not None:
                self.init_callback()

    def exit_gracefully(self, signum, frame):
        self.close()
        sys.exit(1)

    def close(self):
        self.save_cookies()
        if self.driver is not None:
            self.driver.quit()

    def format_cookies(self, cookie_jar):
        cookies = []
        for cookie in cookie_jar or []:
            cookies.append({'name': cookie.name, 'value': cookie.value, 'path': cookie.path, 'domain': cookie.domain, 'secure': cookie.secure, 'expiry': cookie.expiry})
        return cookies

    def load_cookies(self, url):
        cookies = []
        loaded_cookies = False
        for cookie in self.cookies:
            if cookie['domain'] in url:
                # can only load cookies when at the domain
                self.driver.add_cookie(cookie)
                loaded_cookies = True
            else:
                cookies.append(cookie)
        if loaded_cookies:
            # need to reload page with cookies
            print('reload cookies')
            self.driver.get(url)
            self.cookies = cookies

    def get_cookies(self):
        cj = CookieJar()
        for c in self.driver.get_cookies():
            cj.set_cookie(Cookie(0, c['name'], c['value'], None, False, c['domain'], c['domain'].startswith('.'), c['domain'].startswith('.'), c['path'], True, c['secure'], c.get('expiry', 2147483647), False, None, None, {}))
        return cj

    def save_cookies(self):
        if self.cookie_key is not None and self.driver is not None:
            print('saving:', self.driver.get_cookies())
            self.cache[self.cookie_key] = self.driver.get_cookies()

    def wait(self, xpath):
        self.driver.implicitly_wait(self.timeout)
        return self.driver.find_element(By.XPATH, xpath)

    def get_page_source(self):
        result_queue = queue.Queue()

        def get_page_source_cb():
            try:
                # The operation that might hang
                html = self.driver.page_source
            except Exception as e:
                response = download.Response('', 500, str(e)) # Pass any Selenium error back
            else:
                # chrome will wrap JSON in pre - how to solve this properly?
                if '<body><pre>{' in html:
                    html = xpath.get(html, '/html/body/pre')
                response = download.Response(html, 200, '')
            result_queue.put(response)

        # Start the fetching in a separate thread
        fetch_thread = threading.Thread(target=get_page_source_cb)
        fetch_thread.start()
        fetch_thread.join(timeout=5)

        # Check if the thread is still alive after the wait
        if fetch_thread.is_alive():
            # If alive, it means the operation timed out
            response = download.Response('', 408, 'driver.page_source timed out')
        else:
            response = result_queue.get(block=False)
        return response


    def get(self, url, read_cache=True, write_cache=True, retry=True, delay=5, wait_xpath=None):
        try:
            if not read_cache:
                raise KeyError()
            response = self.cache[url]
            if not response and retry:
                raise KeyError()
        except KeyError:
            self.init()
            print('Rendering:', url)
            try:
                self.driver.get(url)
            except TimeoutException:
                print('Request timed out')
                response = download.Response('', 408, 'Request timed out')
            else: 
                time.sleep(delay)
                if wait_xpath:
                    self.wait(wait_xpath)
                self.load_cookies(url)
                response = self.get_page_source()
                if write_cache:
                    self.cache[url] = response
                self.save_cookies()
        return response
