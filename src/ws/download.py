
import collections, json, random, re, time, os, logging, traceback, urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Callable

import ua_generator

from . import adt, clients, common, pdict, services, settings, xpath
Response = clients.Response


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
    def __init__(self, cache_file='', cache=None, delay=1, max_retries=1, proxy_file=None, proxies=None, cache_expires=None, timeout=30, client=None):
        self.cache = cache or pdict.PersistentDict(cache_file or settings.cache_file, expires=cache_expires)
        self.client = clients.Primp() if client is None else client
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
        self._throttle = Throttle(delay)

    def _format_data(self, data, max_length=100):
        if data:
            data_str = str(data)
            if len(data_str) > max_length:
                data_str = data_str[:max_length] + '...'
        else:
            data_str = ''
        return data_str

    def _is_success(self, response):
        if not isinstance(response, clients.Response):
            return True
        elif response.status_code in settings.SUCCESS_STATUS:
            return True
        else:
            return False

    def _should_retry(self, response, num_failures=0, max_retries=1, retry_callback=None):
        if self._is_success(response):
            return False
        elif response.status_code in settings.NON_RETRIABLE_STATUS:
            return False
        elif retry_callback is not None and retry_callback(response):
            return True
        else:
            return num_failures < max_retries

    def get(self, url, delay=None, max_retries=None, retry_callback=None, user_agent='', read_cache=True, write_cache=True, headers=None, data=None, ssl_verify=True, auto_encoding=True, use_proxy=True, key=None):
        if isinstance(data, dict):
            data = urllib.parse.urlencode(sorted(data.items()))
        key = key or Request(url, data=data).get_key()
        max_retries = self.max_retries if max_retries is None else max_retries
        try:
            if not read_cache:
                raise KeyError()
            response = self.cache[key]
            if isinstance(response, (str, dict)):
                response = clients.Response(response, 200, '')
            if self._should_retry(response, max_retries=max_retries):
                raise KeyError()
        except KeyError:
            for num_failures in range(max_retries + 1):
                proxy = self.get_proxy() if use_proxy else None
                self._throttle(delay, proxy)
                try:
                    request_proxies = {'http': proxy, 'https': proxy} if proxy else None
                    response = self.client.fetch(url, headers=headers, data=data, ssl_verify=ssl_verify, proxies=request_proxies, timeout=self.timeout, auto_encoding=auto_encoding)
                except Exception as e:
                    print('ws.download error:', url, type(e), e)
                    self.proxy_errors[proxy] += 1
                    response = clients.Response('', 500, str(e))
                else:
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
            if write_cache:
                self.cache[key] = response
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
            if not isinstance(response, clients.Response):
                response = clients.Response(response, 200, '')
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
