
import collections, json, random, re, time, os, urllib.parse
import xml.etree.ElementTree as ET
import concurrent
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Callable

import requests
import stealth_requests
from playwright.sync_api import sync_playwright, Error as PlaywrightError

from . import adt, common, pdict, services, settings, xpath


SUCCESS_STATUS = (200, 201)
NON_RETRIABLE_STATUS = (404, )


@dataclass
class Request:
    url: str
    headers: dict = None
    data: str = None
    callback: Callable = None

    def get_key(self):
        """Create key for caching this request
        """
        key = self.url
        if self.data:
            key = '{} {}'.format(key, self.data)
        return key


class Response:
    def __init__(self, text, status_code, reason):
        self.text = text
        self.status_code = status_code
        self.reason = reason
        self.tree = None
        self.redirect_url = None

    def get(self, path):
        if self.tree is None:
            self.tree = xpath.Tree(self.text)
        return self.tree.get(path)

    def search(self, path):
        if self.tree is None:
            self.tree = xpath.Tree(self.text)
        return self.tree.search(path)

    def regex(self, r, flags=0):
        return re.compile(r, flags=flags).search(self.text)

    def findall(self, r):
        return re.findall(r, self.text)

    def json(self):
        return json.loads(self.text)

    def jsonp(self):
        return common.parse_jsonp(self.text)

    def xml(self):
        return ET.fromstring(self.text)

    def __str__(self):
        return '{}: {}'.format(self.status_code, self.text[:100] if self.text else '')

    def __bool__(self):
        return self.status_code in SUCCESS_STATUS


class Throttle:
    def __init__(self, delay=0):
        self.delay = delay
        self.last_time = {}

    def __call__(self, delay, ip):
        delay = self.delay if delay is None else delay
        seconds = delay * (0.5 + random.random())
        last_time = self.last_time.get(ip, datetime.now() - timedelta(seconds=seconds))
        next_time = last_time + timedelta(seconds=seconds)
        while next_time > datetime.now():
            time.sleep(0.1)
        self.last_time[ip] = next_time


class Download:
    def __init__(self, cache_file='', cache=None, session=None, delay=1, max_retries=1, proxy_file=None, proxies=None, cache_expires=None, timeout=30, browser=None, render=False, stealth=False):
        self.cache = cache or pdict.PersistentDict(cache_file or settings.cache_file, expires=cache_expires)
        self.session = session
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxies = open(proxy_file).read().splitlines() if proxy_file and os.path.exists(proxy_file) else proxies
        # track the number of consecutive download errors for each proxy
        self.proxy_errors = collections.Counter()
        self.browser = browser or Browser()
        self.render = render
        self.stealth = stealth
        self._throttle = Throttle(delay)

    def _format_headers(self, url, headers, user_agent):
        headers = headers or {}
        for name, value in list(settings.default_headers.items()) + [('User-Agent', user_agent), ('Referer', url)]:
            if name not in headers and name.lower() not in headers:
                headers[name] = value
        return headers

    def _format_data(self, data, max_length=100):
        if data:
            data_str = str(data)
            if len(data_str) > max_length:
                data_str = data_str[:max_length] + '...'
        else:
            data_str = ''
        return data_str

    def _should_retry(self, response, num_failures=0, max_retries=1):
        if response.status_code in SUCCESS_STATUS or response.status_code in NON_RETRIABLE_STATUS:
            return False
        else:
            return num_failures < max_retries


    def get(self, url, delay=None, max_retries=None, user_agent='', read_cache=True, write_cache=True, headers=None, data=None, ssl_verify=True, auto_encoding=True, use_proxy=True, render=False, stealth=False, key=None):
        if isinstance(data, dict):
            data = urllib.parse.urlencode(sorted(data.items()))
        key = key or Request(url, data=data).get_key()
        max_retries = self.max_retries if max_retries is None else max_retries
        try:
            if not read_cache:
                raise KeyError()
            response = self.cache[key]
            if isinstance(response, (str, dict)):
                response = Response(response, 200, '')
            if self._should_retry(response, 0, max_retries):
                raise KeyError()

        except KeyError:
            if self.render or render:
                response = self.browser.get(url, self.get_proxy(), self.timeout)
            else:
                response = self.fetch(url, delay, max_retries, user_agent, headers, data, ssl_verify, auto_encoding, use_proxy, stealth)
            if write_cache:
                self.cache[key] = response
        return response

                
    def fetch(self, url, delay, max_retries, user_agent, headers, data, ssl_verify, auto_encoding, use_proxy, stealth):
        if self.session is None:
            session = stealth_requests.StealthSession() if self.stealth or stealth else requests.Session()
        else:
            session = self.session
        headers = self._format_headers(url, headers, user_agent)
        for num_failures in range(max_retries + 1):
            print('Download: {} {}'.format(url, self._format_data(data)))
            proxy = self.get_proxy() if use_proxy else None
            self._throttle(delay, proxy)
            try:
                request_proxies = {'http': proxy, 'https': proxy} if proxy else None
                if data is not None:
                    request_response = session.post(url, headers=headers, data=data, verify=ssl_verify, proxies=request_proxies, timeout=self.timeout)
                else:
                    request_response = session.get(url, headers=headers, verify=ssl_verify, proxies=request_proxies, timeout=self.timeout)
            except Exception as e:
                print('ws.download error:', e)
                response = Response('', 500, str(e))
                self.proxy_errors[proxy] += 1
            else:
                content = request_response.content if not request_response.encoding or not auto_encoding else request_response.text
                response = Response(content, request_response.status_code, request_response.reason)
                if request_response.url != url:
                    response.redirect_url = request_response.url
                if self._should_retry(response, num_failures, max_retries):
                    print('Download error:', response.status_code)
                else:
                    break
                self.proxy_errors[proxy] = 0
        return response


    def get_proxy(self):
        if self.proxies:
            proxy = self.proxies.pop()
            self.proxies = [proxy] + self.proxies
            return proxy


    def geocode(self, address, api_key):
        gm = services.GoogleMaps(self, api_key)
        return gm.geocode(address, delay=self._throttle.delay)


    def threaded(self, requests, max_workers=4, max_queue=1000, filter_duplicates=True):
        def process_callback(request, response):
            if request.callback:
                for next_request in request.callback(request, response) or []:
                    if isinstance(next_request, Request):
                        if filter_duplicates:
                            if next_request.get_key() in seen:
                                continue
                            else:
                                seen.add(next_request.get_key())
                        requests.append(next_request)
                    else:
                        yield next_request

        seen = adt.HashDict()
        if filter_duplicates:
            filtered_requests = []
            for request in requests:
                if request.get_key() not in seen:
                    seen.add(request.get_key())
                    filtered_requests.append(request)
            requests = filtered_requests
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while requests:
                # avoid loading too many requests into memory at once
                cur_requests = []
                for _ in range(max_queue):
                    if requests:
                        cur_requests.append(requests.pop())
                    else:
                        break

                future_to_request = {}
                for request in cur_requests:
                    try:
                        response = self.cache[request.get_key()]
                        if self._should_retry(response):
                            raise KeyError()
                    except KeyError:
                        future = executor.submit(self.get, url=request.url, headers=request.headers, data=request.data, read_cache=False, write_cache=False)
                        future_to_request[future] = request
                    else:
                        yield from process_callback(request, response)

                # process the completed callbacks
                for future in concurrent.futures.as_completed(future_to_request):
                    request = future_to_request[future]
                    try:
                        response = future.result()
                    except Exception as e:
                        print('{} generated an exception: {}'.format(request.url, e))
                    else:
                        self.cache[request.get_key()] = response
                        yield from process_callback(request, response)
                    del future_to_request[future]


class Browser:
    def __init__(self, headless=True):
        self.headless = headless
        self.initialized = False

    def __del__(self):
        if self.initialized:
            self.playwright.stop()
            self.browser.close()

    def get(self, url, proxy=None, timeout=30, wait_until='load'):
        """
        wait_until: commit -> domcontentloaded -> load
        """
        print('Rendering: {}'.format(url))
        if not self.initialized:
            self.initialized = True
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.firefox.launch(headless=self.headless)

        context = self.browser.new_context(proxy=self.parse_proxy(proxy))
        page = context.new_page()
        try:
            response = page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
        except PlaywrightError as e:
            print('Render error:', e)
            content = ''
            status = 500
            error = str(e)
        else:
            if wait_until == 'commit':
                content = response.text()
            else:
                content = page.content()
            status = response.status
            error = ''
        page.close()
        context.close()
        return Response(content, status, error)

    def parse_proxy(self, server):
        if server:
            login_regex = re.match('http://(.*?):(.*?)@(.*?)$', server)
            if login_regex:
                username, password, server = login_regex.groups()
                proxy = {
                    'server': 'http://' + server,
                    'username': username,
                    'password': password
                }
            else:
                if not server.startswith('http'):
                    server = 'http://' + server
                proxy = {
                    'server': server
                }
            print('PROXY:', proxy)
            return proxy
