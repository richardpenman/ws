import time
import pytest
from unittest.mock import MagicMock, patch
from ws.download import Download, Request, Response, Throttle, SUCCESS_STATUS, NON_RETRIABLE_STATUS


class TestRequest:
    def test_get_key_url_only(self):
        assert Request(url='http://example.com').get_key() == 'http://example.com'

    def test_get_key_with_data(self):
        assert Request(url='http://example.com', data='q=1').get_key() == 'http://example.com q=1'

    def test_get_key_empty_data(self):
        assert Request(url='http://example.com', data='').get_key() == 'http://example.com'


class TestResponse:
    def test_bool_success_statuses(self):
        for status in SUCCESS_STATUS:
            assert bool(Response('ok', status, ''))

    def test_bool_failure_statuses(self):
        for status in [400, 404, 500, 503]:
            assert not bool(Response('error', status, ''))

    def test_str_includes_status(self):
        assert '200' in str(Response('hello', 200, ''))

    def test_json(self):
        assert Response('{"k": 1}', 200, '').json() == {'k': 1}

    def test_findall(self):
        assert Response('a1 b2 c3', 200, '').findall(r'\w\d') == ['a1', 'b2', 'c3']

    def test_regex_match(self):
        assert Response('price $42', 200, '').regex(r'\$\d+').group() == '$42'

    def test_regex_no_match(self):
        assert Response('no price', 200, '').regex(r'\$\d+') is None

    def test_bool_false_for_empty_response(self):
        assert not bool(Response('', 500, 'Internal Server Error'))


class TestDownloadShouldRetry:
    def setup_method(self):
        self.d = Download(cache_file=':memory:')

    def test_success_never_retries(self):
        for status in SUCCESS_STATUS:
            assert not self.d._should_retry(Response('', status, ''))

    def test_non_retriable_statuses_never_retry(self):
        for status in NON_RETRIABLE_STATUS:
            assert not self.d._should_retry(Response('', status, ''), num_failures=0, max_retries=5)

    def test_retries_within_limit(self):
        r = Response('', 500, '')
        assert self.d._should_retry(r, num_failures=0, max_retries=3)
        assert self.d._should_retry(r, num_failures=2, max_retries=3)

    def test_no_retry_at_limit(self):
        assert not self.d._should_retry(Response('', 500, ''), num_failures=3, max_retries=3)

    def test_retry_callback_forces_retry_beyond_limit(self):
        r = Response('', 500, '')
        assert self.d._should_retry(r, num_failures=99, max_retries=1, retry_callback=lambda _: True)

    def test_non_response_is_treated_as_success(self):
        assert not self.d._should_retry('a string')
        assert not self.d._should_retry({'cached': True})


class TestThrottle:
    def test_zero_delay_does_not_block(self):
        t = Throttle(delay=0)
        start = time.time()
        t(0, None)
        assert time.time() - start < 0.2

    def test_instance_delay_used_when_call_delay_is_none(self):
        t = Throttle(delay=0)
        start = time.time()
        t(None, 'proxy1')
        assert time.time() - start < 0.2

    def test_separate_ips_throttled_independently(self):
        t = Throttle(delay=0)
        t(0, 'ip1')
        t(0, 'ip2')
        assert 'ip1' in t.last_time
        assert 'ip2' in t.last_time


class TestDownloadThreaded:
    def setup_method(self):
        self.d = Download(cache_file=':memory:')

    def test_empty_input_returns_nothing(self):
        assert list(self.d.threaded([])) == []

    def test_cached_response_triggers_callback(self):
        url = 'http://example.com/page'
        self.d.cache[url] = Response('cached', 200, '')
        visited = []
        def cb(req, resp):
            visited.append(resp.text)
        list(self.d.threaded([Request(url=url, callback=cb)]))
        assert visited == ['cached']

    def test_duplicate_urls_only_fetched_once(self):
        url = 'http://example.com/dup'
        self.d.cache[url] = Response('content', 200, '')
        seen = []
        def cb(req, resp):
            seen.append(req.url)
        list(self.d.threaded([Request(url=url, callback=cb)] * 5))
        assert len(seen) == 1

    def test_duplicate_filtering_disabled(self):
        url = 'http://example.com/nofilter'
        self.d.cache[url] = Response('content', 200, '')
        seen = []
        def cb(req, resp):
            seen.append(req.url)
        list(self.d.threaded([Request(url=url, callback=cb)] * 3, filter_duplicates=False))
        assert len(seen) == 3

    def test_callback_can_yield_new_requests(self):
        url1 = 'http://example.com/p1'
        url2 = 'http://example.com/p2'
        self.d.cache[url1] = Response('page1', 200, '')
        self.d.cache[url2] = Response('page2', 200, '')
        visited = []
        def cb(req, resp):
            visited.append(req.url)
            if req.url == url1:
                yield Request(url=url2, callback=cb)
        list(self.d.threaded([Request(url=url1, callback=cb)]))
        assert url1 in visited
        assert url2 in visited

    def test_callback_without_request_yields_values(self):
        url = 'http://example.com/data'
        self.d.cache[url] = Response('hello', 200, '')
        def cb(req, resp):
            yield resp.text
        results = list(self.d.threaded([Request(url=url, callback=cb)]))
        assert results == ['hello']

    def test_no_callback_request_is_processed_silently(self):
        url = 'http://example.com/nocb'
        self.d.cache[url] = Response('content', 200, '')
        results = list(self.d.threaded([Request(url=url)]))
        assert results == []

    def test_response_is_written_to_cache_after_fetch(self):
        url = 'http://example.com/fetch'
        fake_response = Response('fetched', 200, '')
        with patch.object(self.d, 'fetch', return_value=fake_response):
            list(self.d.threaded([Request(url=url)]))
        assert self.d.cache[url].text == 'fetched'


class TestDownloadGet:
    def setup_method(self):
        self.d = Download(cache_file=':memory:')

    def test_cache_hit_skips_fetch(self):
        url = 'http://example.com/cached'
        self.d.cache[url] = Response('cached', 200, '')
        with patch.object(self.d, 'fetch') as mock_fetch:
            result = self.d.get(url)
            mock_fetch.assert_not_called()
        assert result.text == 'cached'

    def test_cache_miss_calls_fetch(self):
        url = 'http://example.com/new'
        fake = Response('fetched', 200, '')
        with patch.object(self.d, 'fetch', return_value=fake):
            result = self.d.get(url)
        assert result.text == 'fetched'

    def test_read_cache_false_bypasses_cache(self):
        url = 'http://example.com/bypass'
        self.d.cache[url] = Response('stale', 200, '')
        with patch.object(self.d, 'fetch', return_value=Response('fresh', 200, '')):
            result = self.d.get(url, read_cache=False)
        assert result.text == 'fresh'

    def test_write_cache_false_does_not_store(self):
        url = 'http://example.com/nostore'
        with patch.object(self.d, 'fetch', return_value=Response('ok', 200, '')):
            self.d.get(url, write_cache=False)
        assert url not in self.d.cache

    def test_failed_cached_response_is_retried(self):
        url = 'http://example.com/fail'
        self.d.cache[url] = Response('', 500, 'error')
        with patch.object(self.d, 'fetch', return_value=Response('recovered', 200, '')) as mock_fetch:
            result = self.d.get(url)
            mock_fetch.assert_called_once()
        assert result.text == 'recovered'

    def test_string_in_cache_wrapped_as_response(self):
        url = 'http://example.com/legacy'
        self.d.cache[url] = '<html>legacy</html>'
        result = self.d.get(url)
        assert isinstance(result, Response)
        assert result.status_code == 200
        assert result.text == '<html>legacy</html>'

    def test_dict_data_sorted_and_urlencoded(self):
        url = 'http://example.com/form'
        with patch.object(self.d, 'fetch', return_value=Response('ok', 200, '')):
            self.d.get(url, data={'b': '2', 'a': '1'})
        assert 'http://example.com/form a=1&b=2' in self.d.cache


class TestDownloadGetProxy:
    def test_no_proxies_returns_none(self):
        d = Download(cache_file=':memory:')
        assert d.get_proxy() is None

    def test_returns_proxy_from_list(self):
        d = Download(cache_file=':memory:', proxies=['p1', 'p2', 'p3'])
        assert d.get_proxy() in ['p1', 'p2', 'p3']

    def test_rotates_round_robin(self):
        d = Download(cache_file=':memory:', proxies=['p1', 'p2', 'p3'])
        results = [d.get_proxy() for _ in range(6)]
        assert results.count('p1') == 2
        assert results.count('p2') == 2
        assert results.count('p3') == 2

    def test_single_proxy_always_returned(self):
        d = Download(cache_file=':memory:', proxies=['only'])
        assert all(d.get_proxy() == 'only' for _ in range(5))
