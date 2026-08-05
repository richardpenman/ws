import json, re
import xml.etree.ElementTree as ET

from . import common, settings, xpath


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
        if isinstance(self.text, bytes):
            flag += 'b'
        open(filename, flag).write(self.text)

    def __str__(self):
        return '{}: {}'.format(self.status_code, self.text[:100] if self.text else '')

    def __bool__(self):
        return self.status_code in settings.SUCCESS_STATUS


class Client:
    pass


class Requests(Client):
    def __init__(self, use_session=False):
        self.session = self.get_session() if use_session else None

    def get_session(self):
        import requests
        return requests.Session()

    def fetch(self, url, headers, data, ssl_verify, proxies, timeout, auto_encoding):
        session = self.get_session() if self.session is None else self.session
        if data is None:
            session_response = session.get(url, headers=headers, verify=ssl_verify, proxies=proxies, timeout=timeout)
        else:
            session_response = session.post(url, headers=headers, data=data, verify=ssl_verify, proxies=proxies, timeout=timeout)

        content = session_response.content if not session_response.encoding or not auto_encoding else session_response.text
        response = Response(content, session_response.status_code, session_response.reason)
        if session_response.url != url:
            response.redirect_url = session_response.url

        if self.session is None:
            session.close()
        return response


class CurlCffi(Requests):
    def __init__(self, use_session=False, impersonate='firefox'):
        self.impersonate = impersonate
        self.session = self.get_session() if use_session else None

    def get_session(self):
        import curl_cffi
        return curl_cffi.Session(impersonate=self.impersonate)


class Primp(Client):
    def __init__(self, use_session=False, impersonate='chrome'):
        self.impersonate = impersonate
        self.use_session = use_session
        self.session = None

    def get_session(self, proxy):
        import primp
        # Note primp has a bug where can't change the session proxy: https://github.com/deedy5/primp/issues/154
        return primp.Client(impersonate=self.impersonate, proxy=proxy)
    
    def fetch(self, url, headers, data, ssl_verify, proxies, timeout, auto_encoding):
        if self.session is None:
            proxy = proxies['http'] if proxies else None
            session = self.get_session(proxy)
            if self.use_session:
                self.session = session
        else:
            session = self.session

        if data is None:
            session_response = session.get(url, headers=headers, timeout=timeout)
        else:
            session_response = session.post(url, headers=headers, data=data, timeout=timeout)
       
        content = session_response.content if not session_response.encoding or not auto_encoding else session_response.text
        reason = '' # how to get reason?
        response = Response(content, session_response.status_code, reason)
        if session_response.url != url:
            response.redirect_url = session_response.url

        if self.session is None:
            session.close()
        return response 


class Playwright(Client):
    def __init__(self, headless=True, wait_until='load'):
        """
        wait_until: commit -> domcontentloaded -> load
        """
        self.headless = headless
        self.wait_until = wait_until
        self.initialized = False

    def __del__(self):
        if self.initialized:
            self.browser.close()
            self.playwright.stop()

    def fetch(self, url, headers, data, ssl_verify, proxies, timeout, auto_encoding):
        if data:
            raise Exception('data not supported')
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
        print('Rendering: {}'.format(url))
        if not self.initialized:
            self.initialized = True
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.firefox.launch(headless=self.headless)

        context = self.browser.new_context(proxy=self.parse_proxy(proxies))
        page = context.new_page()
        try:
            response = page.goto(url, wait_until=self.wait_until, timeout=timeout * 1000)
        except PlaywrightError as e:
            print('Render error:', e)
            content = ''
            status = 500
            error = str(e)
        else:
            if self.wait_until == 'commit':
                content = response.text()
            else:
                content = page.content()
            status = response.status
            error = ''
        page.close()
        context.close()
        return Response(content, status, error)

    def parse_proxy(self, proxies):
        if proxies:
            server = proxies['http']
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
