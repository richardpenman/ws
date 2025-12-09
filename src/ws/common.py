# -*- coding: utf-8 -*-
__doc__ = 'Common web scraping related functions'

import os
import re
import sys
import csv
csv.field_size_limit(sys.maxsize)
import time
import glob
import json
import string
import urllib.parse
import itertools
import html.entities
import logging
import logging.handlers
import threading
import collections
from datetime import datetime, timedelta
import sqlite3
import json
from html import unescape
import requests
from . import adt, settings, xpath


class WebScrapingError(Exception):
    pass


# known media file extensions
MEDIA_EXTENSIONS = ['ai', 'aif', 'aifc', 'aiff', 'asc', 'avi', 'bcpio', 'bin', 'c', 'cc', 'ccad', 'cdf', 'class', 'cpio', 'cpt', 'csh', 'css', 'csv', 'dcr', 'dir', 'dms', 'doc', 'drw', 'dvi', 'dwg', 'dxf', 'dxr', 'eps', 'etx', 'exe', 'ez', 'f', 'f90', 'fli', 'flv', 'gif', 'gtar', 'gz', 'h', 'hdf', 'hh', 'hqx', 'ice', 'ico', 'ief', 'iges', 'igs', 'imq', 'ips', 'ipx', 'jpe', 'jpeg', 'jpg', 'js', 'kar', 'latex', 'lha', 'lsp', 'lzh', 'm', 'man', 'me', 'mesh', 'mid', 'midi', 'mif', 'mime', 'mov', 'movie', 'mp2', 'mp3', 'mpe', 'mpeg', 'mpg', 'mpga', 'ms', 'msh', 'nc', 'oda', 'pbm', 'pdb', 'pdf', 'pgm', 'pgn', 'png', 'pnm', 'pot', 'ppm', 'pps', 'ppt', 'ppz', 'pre', 'prt', 'ps', 'qt', 'ra', 'ram', 'ras', 'raw', 'rgb', 'rm', 'roff', 'rpm', 'rtf', 'rtx', 'scm', 'set', 'sgm', 'sgml', 'sh', 'shar', 'silo', 'sit', 'skd', 'skm', 'skp', 'skt', 'smi', 'smil', 'snd', 'sol', 'spl', 'src', 'step', 'stl', 'stp', 'sv4cpio', 'sv4crc', 'swf', 't', 'tar', 'tcl', 'tex', 'texi', 'tif', 'tiff', 'tr', 'tsi', 'tsp', 'tsv', 'unv', 'ustar', 'vcd', 'vda', 'viv', 'vivo', 'vrml', 'w2p', 'wav', 'wmv', 'wrl', 'xbm', 'xlc', 'xll', 'xlm', 'xls', 'xlw', 'xml', 'xpm', 'xsl', 'xwd', 'xyz', 'zip']

# tags that do not contain content
EMPTY_TAGS = 'br', 'hr', 'meta', 'link', 'base', 'img', 'embed', 'param', 'area', 'col', 'input'


def to_ascii(html):
    """Return ascii part of html
    """
    return ''.join(c for c in (html or '') if ord(c) < 128)

def to_int(s, default=0):
    """Return integer from this string

    >>> to_int('90')
    90
    >>> to_int('-90.2432')
    -90
    >>> to_int('a90a')
    90
    >>> to_int('a')
    0
    >>> to_int('a', 90)
    90
    """
    return int(to_float(s, default))

def to_float(s, default=0.0):
    """Return float from this string

    >>> to_float('90.45')
    90.45
    >>> to_float('')
    0.0
    >>> to_float('90')
    90.0
    >>> to_float('..9')
    0.0
    >>> to_float('.9')
    0.9
    >>> to_float(None)
    0.0
    >>> to_float(1)
    1.0
    """
    result = default
    if s:
        valid = string.digits + '.-'
        try:
            result = float(''.join(c for c in str(s) if c in valid))
        except ValueError:
            pass # input does not contain a number
    return result

    
def is_html(html):
    """Returns whether content is likely HTML based on search for common tags
    """
    try:
        result = re.search('html|head|body', html) is not None
    except TypeError:
        result = False
    return result


def is_url(text):
    """Returns whether passed text is a URL

    >>> is_url('abc')
    False
    >>> is_url('webscraping.com')
    False
    >>> is_url('http://webscraping.com/blog')
    True
    """
    return re.match('https?://', text) is not None


def unique(l):
    """Remove duplicates from list, while maintaining order

    >>> unique([3,6,4,4,6])
    [3, 6, 4]
    >>> unique([])
    []
    >>> unique([3,6,4])
    [3, 6, 4]
    """
    checked = []
    for e in l:
        if e not in checked:
            checked.append(e)
    return checked


def flatten(l):
    """Flatten a list of lists into a single list

    >>> flatten([[1,2,3], [4,5,6]])
    [1, 2, 3, 4, 5, 6]
    """
    return [item for sublist in l for item in sublist]


def nth(l, i, default=''):
    """Return nth item from list or default value if out of range
    """
    try:
        return l[i] 
    except IndexError:
        return default

def first(l, default=''):
    """Return first element from list or default value if out of range

    >>> first([1,2,3])
    1
    >>> first([], None)
    
    """
    return nth(l, i=0, default=default)

def last(l, default=''):
    """Return last element from list or default value if out of range
    """
    return nth(l, i=-1, default=default)


def pad(l, size, default=None, end=True):
    """Return list of given size
    Insert elements of default value if too small
    Remove elements if too large
    Manipulate end of list if end is True, else start

    >>> pad(range(5), 5)
    [0, 1, 2, 3, 4]
    >>> pad(range(5), 3)
    [0, 1, 2]
    >>> pad(range(5), 7, -1)
    [0, 1, 2, 3, 4, -1, -1]
    >>> pad(range(5), 7, end=False)
    [None, None, 0, 1, 2, 3, 4]
    """
    while len(l) < size:
        if end:
            l.append(default)
        else:
            l.insert(0, default)
    while len(l) > size:
        if end:
            l.pop()
        else:
            l.pop(0)
    return l


def remove_tags(html, keep_children=True):
    """Remove HTML tags leaving just text
    If keep children is True then keep text within child tags

    >>> remove_tags('hello <b>world</b>!')
    'hello world!'
    >>> remove_tags('hello <b>world</b>!', False)
    'hello !'
    >>> remove_tags('hello <br>world<br />!', False)
    'hello world!'
    >>> remove_tags('<span><b></b></span>test</span>', False)
    'test'
    """
    if isinstance(html, xpath.Tree):
        html = str(html)
    html = re.sub('<(%s)[^>]*>' % '|'.join(EMPTY_TAGS), '', html)
    if not keep_children:
        for tag in unique(re.findall('<(\w+?)\W', html)):
            if tag not in EMPTY_TAGS:
                html = re.compile('<\s*%s.*?>.*?</\s*%s\s*>' % (tag, tag), re.DOTALL).sub('', html)
    return re.compile('<[^<]*?>').sub('', html)
    

def normalize(s, encoding=settings.default_encoding, keep_newlines=False):
    """Normalize the string by removing tags, unescaping, and removing surrounding whitespace
    
    >>> normalize('<span>Tel.:   029&nbsp;-&nbsp;12345678   </span>')
    'Tel.: 029 - 12345678'
    """
    if isinstance(s, xpath.Tree):
        s = str(s)
    if isinstance(s, str):
        # remove tags and set encoding
        s = unescape(remove_tags(s))#, encoding=encoding, keep_unicode=isinstance(s, unicode))
        if keep_newlines:
            # keep multiple newlines
            s = re.sub('[\n\r]+', '\n', s)
            s = re.sub('[ \t\f\v]+', ' ', s)
        else:
            # replace all subsequent whitespace with single space
            s = re.sub('[\s]+', ' ', s) 
        s = re.compile('<!--.*?-->', re.DOTALL).sub('', s).strip()
    return s


def regex_get(html, pattern, index=None, normalized=True, flag=re.DOTALL|re.IGNORECASE, default='', one=False):
    """Helper method to extract content from regular expression
    
    >>> regex_get('<div><span>Phone: 029&nbsp;01054609</span><span></span></div>', r'<span>Phone:([^<>]+)')
    '029 01054609'
    >>> regex_get('<div><span>Phone: 029&nbsp;01054609</span><span></span></div>', r'<span>Phone:\s*(\d+)&nbsp;(\d+)')
    ['029', '01054609']
    """
    m = re.compile(pattern, flag).search(html)
    if m:
        if len(m.groups()) == 1:
            return normalize(m.groups()[0]) if normalized else m.groups()[0]
        elif index != None:
            return normalize(m.groups()[index]) if normalized else m.groups()[index]
        else:
            return [normalize(item) if normalized else item for item in m.groups()]
    return default


def parse_jsonp(s):
    try:
        rindex = s.index(b'(')
        lindex = s.rindex(b')')
    except IndexError:
        pass
    else:
        return json.loads(s[rindex+1 : lindex])


def get_extension(url):
    """Return extension from given URL

    >>> get_extension('hello_world.JPG')
    'jpg'
    >>> get_extension('http://www.google-analytics.com/__utm.gif?utmwv=1.3&utmn=420639071')
    'gif'
    """
    return os.path.splitext(urlparse.urlsplit(url).path)[-1].lower().replace('.', '')


def get_domain(url):
    """Extract the domain from the given URL

    >>> get_domain('http://www.google.com.au/tos.html')
    'google.com.au'
    >>> get_domain('www.google.com')
    'google.com'
    """
    m = re.compile(r"^.*://(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})").search(url)
    if m:
        # an IP address
        return m.groups()[0]
    
    suffixes = 'ac', 'ad', 'ae', 'aero', 'af', 'ag', 'ai', 'al', 'am', 'an', 'ao', 'aq', 'ar', 'arpa', 'as', 'asia', 'at', 'au', 'aw', 'ax', 'az', 'ba', 'bb', 'bd', 'be', 'bf', 'bg', 'bh', 'bi', 'biz', 'bj', 'bm', 'bn', 'bo', 'br', 'bs', 'bt', 'bv', 'bw', 'by', 'bz', 'ca', 'cat', 'cc', 'cd', 'cf', 'cg', 'ch', 'ci', 'ck', 'cl', 'cm', 'cn', 'co', 'com', 'coop', 'cr', 'cu', 'cv', 'cx', 'cy', 'cz', 'de', 'dj', 'dk', 'dm', 'do', 'dz', 'ec', 'edu', 'ee', 'eg', 'er', 'es', 'et', 'eu', 'fi', 'fj', 'fk', 'fm', 'fo', 'fr', 'ga', 'gb', 'gd', 'ge', 'gf', 'gg', 'gh', 'gi', 'gl', 'gm', 'gn', 'gov', 'gp', 'gq', 'gr', 'gs', 'gt', 'gu', 'gw', 'gy', 'hk', 'hm', 'hn', 'hr', 'ht', 'hu', 'id', 'ie', 'il', 'im', 'in', 'info', 'int', 'io', 'iq', 'ir', 'is', 'it', 'je', 'jm', 'jo', 'jobs', 'jp', 'ke', 'kg', 'kh', 'ki', 'km', 'kn', 'kp', 'kr', 'kw', 'ky', 'kz', 'la', 'lb', 'lc', 'li', 'lk', 'lr', 'ls', 'lt', 'lu', 'lv', 'ly', 'ma', 'mc', 'md', 'me', 'mg', 'mh', 'mil', 'mk', 'ml', 'mm', 'mn', 'mo', 'mobi', 'mp', 'mq', 'mr', 'ms', 'mt', 'mu', 'mv', 'mw', 'mx', 'my', 'mz', 'na', 'name', 'nc', 'ne', 'net', 'nf', 'ng', 'ni', 'nl', 'no', 'np', 'nr', 'nu', 'nz', 'om', 'org', 'pa', 'pe', 'pf', 'pg', 'ph', 'pk', 'pl', 'pm', 'pn', 'pr', 'pro', 'ps', 'pt', 'pw', 'py', 'qa', 're', 'ro', 'rs', 'ru', 'rw', 'sa', 'sb', 'sc', 'sd', 'se', 'sg', 'sh', 'si', 'sj', 'sk', 'sl', 'sm', 'sn', 'so', 'sr', 'st', 'su', 'sv', 'sy', 'sz', 'tc', 'td', 'tel', 'tf', 'tg', 'th', 'tj', 'tk', 'tl', 'tm', 'tn', 'to', 'tp', 'tr', 'tt', 'tv', 'tw', 'tz', 'ua', 'ug', 'uk', 'us', 'uy', 'uz', 'va', 'vc', 've', 'vg', 'vi', 'vn', 'vu', 'wf', 'ws', 'xn', 'ye', 'yt', 'za', 'zm', 'zw'
    url = re.sub('^.*://', '', url).partition('/')[0].lower()
    domain = []
    for section in url.split('.'):
        if section in suffixes:
            domain.append(section)
        else:
            domain = [section]
    return '.'.join(domain)


def same_domain(url1, url2):
    """Return whether URLs belong to same domain
    
    >>> same_domain('http://www.google.com.au', 'code.google.com')
    True
    >>> same_domain('http://www.facebook.com', 'http://www.myspace.com')
    False
    """
    server1 = get_domain(url1)
    server2 = get_domain(url2)
    return server1 and server2 and (server1 in server2 or server2 in server1)


def parse_proxy(proxy):
    """Parse a proxy into its fragments
    Returns a dict with username, password, host, and port

    >>> f = parse_proxy('login:pw@66.197.208.200:8080')
    >>> f.username
    'login'
    >>> f.password
    'pw'
    >>> f.host
    '66.197.208.200'
    >>> f.port
    '8080'
    >>> f = parse_proxy('66.197.208.200')
    >>> f.username == f.password == f.port == ''
    True
    >>> f.host
    '66.197.208.200'
    """
    fragments = adt.Bag()
    if isinstance(proxy, str):
        match = re.match('((?P<username>\w+):(?P<password>\w+)@)?(?P<host>\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3})(:(?P<port>\d+))?', proxy)
        if match:
            groups = match.groupdict()
            fragments.username = groups.get('username') or ''
            fragments.password = groups.get('password') or ''
            fragments.host = groups.get('host')
            fragments.port = groups.get('port') or ''
    return fragments


def read_list(file):
    """Return file as list if exists
    """
    l = []
    if os.path.exists(file):
        l.extend(open(file).read().splitlines())
    else:
        logger.debug('%s not found' % file)
    return l


class UnicodeWriter:
    """A CSV writer that produces Excel-compatible CSV files from unicode data.
    
    file: 
        can either be a filename or a file object
    encoding:
        the encoding to use for output
    mode:
        the mode for writing to file
    unique:
        if True then will only write unique rows to output
    unique_by:
        make the rows unique by these columns(the value is a list of indexs), default by all columns
    quoting:
        csv module quoting style to use
    utf8_bom:
        whether need to add the BOM
    auto_repair:
        whether need to remove the invalid rows automatically
    
    >>> from io import StringIO
    >>> fp = StringIO()
    >>> writer = UnicodeWriter(fp, quoting=csv.QUOTE_MINIMAL)
    >>> writer.writerow(['a', '1'])
    >>> writer.flush()
    >>> fp.seek(0)
    >>> fp.read().strip()
    'a,1'
    """
    def __init__(self, file, encoding=settings.default_encoding, mode='w', unique=False, quoting=csv.QUOTE_ALL, **argv):
        self.encoding = encoding
        self.unique = unique
        self.seen = set()
        if hasattr(file, 'write'):
            self.fp = file
        else:
            self.fp = open(file, mode)
        self.writer = csv.writer(self.fp, quoting=quoting, **argv)
        
    def _cell(self, s):
        """Normalize the content for this cell
        """
        if isinstance(s, xpath.Tree):
            s = str(s)
        if isinstance(s, str):
            s = s.encode(self.encoding, 'ignore')
        elif s is None:
            s = ''
        else:
            pass #s = bytes(s)
        return s

    def writerow(self, row):
        """Write row to output and returns whether was written.
        """
        #row = [self._cell(col) for col in row]
        row = tuple(row)
        if self.unique:
            key = hash(row)
            if key in self.seen:
                return False
            else:
                self.seen.add(key)
                self.writer.writerow(row)
        else:
            self.writer.writerow(row)
        return True
            
    def writerows(self, rows):
        """Write multiple rows to output
        """
        for row in rows:
            self.writerow(row)

    def flush(self):
        """Flush output to disk
        """
        self.fp.flush()
        if hasattr(self.fp, 'fileno'):
            # this is a real file
            os.fsync(self.fp.fileno())
        
    def close(self):
        """Close the output file pointer
        """
        self.fp.close()


def csv_to_xls(filename):
    from xlsxwriter.workbook import Workbook
    workbook = Workbook(filename[:-4] + '.xlsx')
    worksheet = workbook.add_worksheet()
    with open(filename, 'rt') as f:
        reader = csv.reader(f)
        for r, row in enumerate(reader):
            for c, col in enumerate(row):
                worksheet.write(r, c, col.decode('utf-8'))
    workbook.close()


class ConsoleHandler(logging.StreamHandler):
    """Log to stderr for errors else stdout
    """
    def __init__(self):
        logging.StreamHandler.__init__(self)
        self.stream = None

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.stream = sys.stderr
        else:
            self.stream = sys.stdout
        logging.StreamHandler.emit(self, record)


def get_logger(output_file, level=settings.log_level, maxbytes=0):
    """Create a logger instance

    output_file:
        file where to save the log
    level:
        the minimum logging level to save
    maxbytes:
        the maxbytes allowed for the log file size. 0 means no limit.
    """
    logger = logging.getLogger(output_file)
    # avoid duplicate handlers
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        try:
            if not maxbytes:
                file_handler = logging.FileHandler(output_file)
            else:
                file_handler = logging.handlers.RotatingFileHandler(output_file, maxBytes=maxbytes)
        except IOError:
            pass # can not write file
        else:
            file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            logger.addHandler(file_handler)

        console_handler = ConsoleHandler()
        console_handler.setLevel(level)
        logger.addHandler(console_handler)
    return logger
logger = get_logger(settings.log_file, maxbytes=2*1024*1024*1024)
