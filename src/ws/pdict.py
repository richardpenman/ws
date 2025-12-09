__doc__ = """
pdict has a dictionary like interface and a sqlite backend
It uses pickle to store Python objects and strings, which are then compressed
Multithreading is supported
"""

import os
import sys
import datetime
import time
import sqlite3
import zlib
import itertools
import threading
import hashlib
import shutil
import glob
import pickle

DEFAULT_LIMIT = 1000
DEFAULT_TIMEOUT = 10000



class PersistentDict:
    """Stores and retrieves persistent data through a dict-like interface
    Data is stored compressed on disk using sqlite3 

    filename: 
        where to store sqlite database. Uses in memory by default.
    compress_level: 
        between 1-9 (in my test levels 1-3 produced a 1300kb file in ~7 seconds while 4-9 a 288kb file in ~9 seconds)
    expires: 
        a timedelta object of how old data can be before expires. By default is set to None to disable.
    timeout: 
        how long should a thread wait for sqlite to be ready (in ms)
    isolation_level: 
        None for autocommit or else 'DEFERRED' / 'IMMEDIATE' / 'EXCLUSIVE'

    >>> cache = PersistentDict()
    >>> url = 'http://google.com/abc'
    >>> html = '<html>abc</html>'
    >>>
    >>> url in cache
    False
    >>> len(cache)
    0
    >>> cache[url] = html
    >>> url in cache
    True
    >>> len(cache)
    1
    >>> cache[url] == html
    True
    >>> cache.get(url)['value'] == html
    True
    >>> cache.meta(url)
    {}
    >>> cache.meta(url, 'meta')
    >>> cache.meta(url)
    'meta'
    >>> del cache[url]
    >>> url in cache
    False
    >>> os.remove(cache.filename)
    """
    def __init__(self, filename='cache.db', compress_level=6, expires=None, timeout=DEFAULT_TIMEOUT, isolation_level=None):
        """initialize a new PersistentDict with the specified database file.
        """
        self.filename = filename
        self.compress_level, self.expires, self.timeout, self.isolation_level = \
            compress_level, expires, timeout, isolation_level
        self.conn = sqlite3.connect(filename, timeout=timeout, isolation_level=isolation_level, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
        #self.conn.text_factory = lambda x: str(x)
        sql = """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT NOT NULL PRIMARY KEY UNIQUE,
            value BLOB,
            meta BLOB,
            status INTEGER,
            updated timestamp DEFAULT (datetime('now', 'localtime'))
        );
        """
        self.conn.execute(sql)
        self.conn.execute("CREATE INDEX IF NOT EXISTS keys ON config (key);")


    def __copy__(self):
        """make a copy of current cache settings
        """
        return PersistentDict(filename=self.filename, compress_level=self.compress_level, expires=self.expires, 
                              timeout=self.timeout, isolation_level=self.isolation_level)


    def __contains__(self, key):
        """check the database to see if a key exists
        """
        row = self.conn.execute("SELECT updated FROM config WHERE key=?;", (key,)).fetchone()
        return row and self.is_fresh(row[0])


    def contains(self, keys, ignore_expires=False):
        """check if a list of keys exist
    
        >>> # try 0 second expiration so expires immediately
        >>> cache = PersistentDict(expires=datetime.timedelta(seconds=0))
        >>> cache['a'] = 1; 
        >>> cache.contains(['a', 'b'])
        []
        >>> cache.contains(['a', 'b'], ignore_expires=True)
        [u'a']
        >>> os.remove(cache.filename)
        """
        results = []
        c = self.conn.cursor()
        c.execute("SELECT key, updated FROM config WHERE key IN (%s);" % ','.join(len(keys)*'?'), keys)
        for row in c:
            if ignore_expires or self.is_fresh(row[1]):
                results.append(row[0])
        return results
        

    def __iter__(self):
        """iterate each key in the database
        """
        c = self.conn.cursor()
        c.execute("SELECT key FROM config;")
        for row in c:
            yield row[0]

    
    def __bool__(self):
        return True

    def __nonzero__(self):
        return True


    def __len__(self):
        """Return the number of entries in the cache
        """
        c = self.conn.cursor()
        c.execute("SELECT count(*) FROM config;")
        return c.fetchone()[0]


    def __getitem__(self, key):
        """return the value of the specified key or raise KeyError if not found
        """
        row = self.conn.execute("SELECT value, updated FROM config WHERE key=?;", (key,)).fetchone()
        if row:
            if self.is_fresh(row[1]):
                value = row[0]
                return self.deserialize(value)
            else:
                raise KeyError("Key `%s' is stale" % key)
        else:
            raise KeyError("Key `%s' does not exist" % key)


    def __delitem__(self, key):
        """remove the specifed value from the database
        """
        self.conn.execute("DELETE FROM config WHERE key=?;", (key,))


    def __setitem__(self, key, value):
        """set the value of the specified key
        """
        updated = datetime.datetime.now()
        self.conn.execute("INSERT OR REPLACE INTO config (key, value, meta, updated) VALUES(?, ?, ?, ?);", (
            key, self.serialize(value), self.serialize({}), updated)
        )


    def serialize(self, value):
        """convert object to a compressed pickled string to save in the db
        """
        return sqlite3.Binary(zlib.compress(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), self.compress_level))
    
    def deserialize(self, value):
        """convert compressed pickled string from database back into an object
        """
        if value:
            return pickle.loads(zlib.decompress(value), encoding='latin1')


    def is_fresh(self, t):
        """returns whether this datetime has expired
        """
        return self.expires is None or datetime.datetime.now() - t < self.expires


    def get(self, key, default=None, ignore_expires=False):
        """Get data at key and return default if not defined
        """
        data = default
        if key:
            row = self.conn.execute("SELECT value, meta, updated FROM config WHERE key=?;", (key,)).fetchone()
            if row:
                if ignore_expires or self.is_fresh(row[2]):
                    value = row[0] 
                    data = dict(
                        value=self.deserialize(value),
                        meta=self.deserialize(row[1]),
                        updated=row[2]
                    )
        return data


    def rename(self, cur_key, to_key):
        """Rename the data at `cur_key` to `to_key`
        """
        c = self.conn.cursor()
        num_rows = 0
        c.execute("BEGIN")
        c.execute("DELETE FROM config WHERE key=?;", (to_key,))
        num_rows += c.rowcount
        c.execute("UPDATE config SET key=? WHERE key=?;", (to_key, cur_key))
        num_rows += c.rowcount
        c.execute("COMMIT")
        return num_rows


    def touch(self, key, t=None):
        updated = t or datetime.datetime.now()
        self.conn.execute("UPDATE config SET updated=? WHERE key=?;", (updated, key))
        

    def meta(self, key, value=None):
        """Get / set meta for this value

        if value is passed then set the meta attribute for this key
        if not then get the existing meta data for this key
        """
        if value is None:
            # want to get meta
            row = self.conn.execute("SELECT meta FROM config WHERE key=?;", (key,)).fetchone()
            if row:
                return self.deserialize(row[0])
            else:
                raise KeyError("Key `%s' does not exist" % key)
        else:
            # want to set meta
            self.conn.execute("UPDATE config SET meta=?, updated=? WHERE key=?;", (self.serialize(value), datetime.datetime.now(), key))


    def clear(self):
        """Clear all cached data
        """
        self.conn.execute("DELETE FROM config;")


    def merge(self, db, override=False):
        """Merge this databases content
        override determines whether to override existing keys
        """
        for key in db.keys():
            if override or key not in self:
                self[key] = db[key]


    def vacuum(self):
        self.conn.execute('VACUUM')


if __name__ == '__main__':
    import tempfile
    import webbrowser
    from optparse import OptionParser
    parser = OptionParser(usage='usage: %prog [options] <cache file>')
    parser.add_option('-k', '--key', dest='key', help='The key to use')
    parser.add_option('-v', '--value', dest='value', help='The value to store')
    parser.add_option('-b', '--browser', action='store_true', dest='browser', default=False, help='View content of this key in a web browser')
    parser.add_option('-c', '--clear', action='store_true', dest='clear', default=False, help='Clear all data for this cache')
    parser.add_option('-s', '--size', action='store_true', dest='size', default=False, help='Display size of database')
    options, args = parser.parse_args()
    if not args:
        parser.error('Must specify the cache file')
    cache = PersistentDict(args[0])

    if options.value:
        # store thie value 
        if options.key:
            cache[options.key] = options.value
        else:
            parser.error('Must specify the key')
    elif options.browser:
        if options.key:
            value = cache[options.key]
            filename = tempfile.NamedTemporaryFile().name
            fp = open(filename, 'w')
            fp.write(str(value))
            fp.flush()
            webbrowser.open(filename)
        else:
            parser.error('Must specify the key')
    elif options.key:
        print(cache[options.key])
    elif options.clear:
        if raw_input('Really? Clear the cache? (y/n) ') == 'y':
            cache.clear()
            print('cleared')
    elif options.size:
        print(len(cache))
    else:
        parser.error('No options selected')
