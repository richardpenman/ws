__doc__ = 'default application wide settings'

import sys
import os
import logging


# default location to store output state files
dirname, filename = os.path.split(sys.argv[0])
state_dir = os.path.join(dirname, '.' + filename.replace('.py', '')) 
if not os.path.exists(state_dir):
    try:
        os.mkdir(state_dir)
    except OSError as e:
        state_dir = ''
        #print 'Unable to create state directory:', e
cache_file  = os.path.relpath(os.path.join(state_dir, 'cache.db')) # file to use for pdict cache
log_file    = os.path.join(state_dir, 'webscraping.log') # default logging file
log_level = logging.INFO

default_encoding = 'utf-8'
default_headers =  {
    'Accept-Language': 'en-us,en;q=0.5',
    'Accept': '*/*',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36',
}
