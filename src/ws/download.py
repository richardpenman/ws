
import collections, json, random, re, time, os, logging, traceback, urllib.parse
import xml.etree.ElementTree as ET
import concurrent
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Callable

import requests
import stealth_requests
from playwright.sync_api import sync_playwright, Error as PlaywrightError
import ua_generator

from . import adt, common, pdict, services, settings, xpath


SUCCESS_STATUS = (200, 201)
NON_RETRIABLE_STATUS = (404, 411, 413, 415, 422, 431)


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
        return self.get_tree().get(path)

    def search(self, path):
        return self.get_tree().search(path)

    def get_tree(self):
        if self.tree is None:
            self.tree = xpath.Tree(self.text)
        return self.tree

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

    def utf(self):
        try:
            self.text = self.text.decode('utf-8')
        except (AttributeError, UnicodeDecodeError):
            pass
        try:
            self.text = self.text.encode('latin-1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        return self

    def save(self, filename, flag='w'):
        open(filename, flag).write(self.text)

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
        self.proxies = proxies
        if proxy_file:
            if os.path.exists(proxy_file):
                self.proxies = open(proxy_file).read().splitlines()
            else:
                print(f'Proxy file {proxy_file} missing')
        # track the number of consecutive download errors for each proxy
        self.proxy_errors = collections.Counter()
        self.browser = browser or Browser()
        self.render = render
        self.stealth = stealth
        self._throttle = Throttle(delay)

    def _format_headers(self, url, headers, user_agent):
        headers = headers or {}
        user_agent = user_agent or ua_generator.generate(device='desktop', browser=('chrome', 'edge')).text
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

    def _is_success(self, response):
        if not isinstance(response, Response):
            return True
        elif response.status_code in SUCCESS_STATUS:
            return True
        else:
            return False

    def _should_retry(self, response, num_failures=0, max_retries=1, retry_callback=None):
        if self._is_success(response):
            return False
        elif response.status_code in NON_RETRIABLE_STATUS:
            return False
        elif retry_callback is not None and retry_callback(response):
            return True
        else:
            return num_failures < max_retries

    def get(self, url, delay=None, max_retries=None, retry_callback=None, user_agent='', read_cache=True, write_cache=True, headers=None, data=None, ssl_verify=True, auto_encoding=True, use_proxy=True, render=False, stealth=False, key=None):
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
            if self._should_retry(response, max_retries=max_retries):
                raise KeyError()
        except KeyError:
            if self.render or render:
                response = self.browser.get(url, self.get_proxy(), self.timeout)
            else:
                response = self.fetch(url, delay, max_retries, retry_callback, user_agent, headers, data, ssl_verify, auto_encoding, use_proxy, stealth)
            if write_cache:
                self.cache[key] = response
        return response

                
    def fetch(self, url, delay, max_retries, retry_callback, user_agent, headers, data, ssl_verify, auto_encoding, use_proxy, stealth):
        stealth = stealth or self.stealth
        if self.session is None:
            session = stealth_requests.StealthSession(http_version=3) if stealth else requests.Session()
        else:
            session = self.session
        headers = (headers or {}) if stealth else self._format_headers(url, headers, user_agent) 
        for num_failures in range(max_retries + 1):
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
                self.proxy_errors[proxy] += 1
                response = Response('', 500, str(e))
            else:
                content = request_response.content if not request_response.encoding or not auto_encoding else request_response.text
                response = Response(content, request_response.status_code, request_response.reason)
                if request_response.url != url:
                    response.redirect_url = request_response.url
                summary = '{} | {} {}'.format(response.status_code, url, self._format_data(data))
                if self._is_success(response):
                    print(f'Success: {summary}')
                    del self.proxy_errors[proxy]
                    break
                else:
                    print(f'Error: {summary} ({num_failures + 1}/{max_retries})')
                    self.proxy_errors[proxy] += 1
                    if not self._should_retry(response=response, num_failures=num_failures, max_retries=max_retries, retry_callback=retry_callback):
                        break
        if self.session is None:
            session.close()
        return response


    def get_proxy(self):
        if self.proxies:
            proxy = self.proxies.pop()
            self.proxies = [proxy] + self.proxies
            return proxy


    def geocode(self, address, api_key):
        gm = services.GoogleMaps(self, api_key)
        return gm.geocode(address, delay=self._throttle.delay)


    def wayback(self, url):
        available_url = f'https://web.archive.org/cdx/search/cdx?url={url}&output=json&filter=statuscode:200&limit=-1'
        ajax_response = self.get(available_url)
        if ajax_response:
            ajax = ajax_response.json()
            if ajax:
                timestamp = ajax[1][1]
                wayback_url = f'https://web.archive.org/web/{timestamp}id_/{url}'
                return self.get(wayback_url)


    def threaded(self, requests_iterator, max_workers=4, filter_duplicates=True):
        request_stream = iter(requests_iterator)
        
        seen = adt.HashDict() 
        callback_queue = collections.deque()
        active_futures = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while True:
                # 1. Fill the executor slots
                while len(active_futures) < max_workers:
                    request = None
                    
                    # Priority 1: Get requests from callbacks (discovered links)
                    if callback_queue:
                        request = callback_queue.popleft()
                    # Priority 2: Get requests from the main stream
                    else:
                        try:
                            request = next(request_stream)
                        except StopIteration:
                            break # Main stream exhausted

                    if request:
                        # Duplicate filtering
                        if filter_duplicates:
                            key = request.get_key()
                            if key in seen:
                                continue
                            seen.add(key)

                        # Cache Check
                        try:
                            response = self.cache[request.get_key()]
                            if self._should_retry(response, max_retries=self.max_retries):
                                raise KeyError()
                            # Process cached items immediately (no thread needed)
                            yield from self._handle_threaded_callback(request, response, callback_queue)
                        except KeyError:
                            # Submit to thread pool
                            future = executor.submit(
                                self.get, url=request.url, headers=request.headers, 
                                data=request.data, read_cache=False, write_cache=False
                            )
                            active_futures[future] = request

                # 2. If nothing is running and nothing is left in streams, we are done
                if not active_futures:
                    break

                # 3. Wait for the NEXT single download to finish
                done, _ = wait(active_futures.keys(), return_when=FIRST_COMPLETED)

                for future in done:
                    request = active_futures.pop(future)
                    try:
                        response = future.result()
                        if response is not None:
                            self.cache[request.get_key()] = response
                        yield from self._handle_threaded_callback(request, response, callback_queue)
                    except Exception:
                        print(f'Error on {request.url}:\n{traceback.format_exc()}')

        
    def _handle_threaded_callback(self, request, response, callback_queue):
        """Internal helper to process callbacks and feed the callback_queue."""
        if request.callback:
            if not isinstance(response, Response):
                response = Response(response, 200, '')
            try:
                callback_requests = request.callback(request, response) or iter([])
            except Exception:
                print('Callback error: ' + request.url)
                print(traceback.format_exc())
            else:
                while True:
                    try:
                        next_request = next(callback_requests)
                    except StopIteration:
                        break
                    except Exception:
                        print('Callback error: ' + request.url)
                        print(traceback.format_exc())
                    else:
                        if isinstance(next_request, Request):
                            # We put discovered requests in the callback_queue to be processed next
                            callback_queue.append(next_request)
                        else:
                            yield next_request


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
