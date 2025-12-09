__doc__ = 'High level functions for interpreting useful data from input'

import csv, io, logging, math, os, random, re, sys, urllib, zipfile
import requests
from . import common, xpath


def get_links(html, url=None, local=True, external=True):
    """Return all links from html and convert relative to absolute if source url is provided

    html:
        HTML to parse
    url:
        optional URL for determining path of relative links
    local:
        whether to include links from same domain
    external:
        whether to include linkes from other domains
    """
    def normalize_link(link):
        if urllib.parse.urlsplit(link).scheme in ('http', 'https', ''):
            if '#' in link:
                link = link[:link.index('#')]
            if url:
                link = urllib.parse.urljoin(url, link)
                if not local and common.same_domain(url, link):
                    # local links not included
                    link = None
                if not external and not common.same_domain(url, link):
                    # external links not included
                    link = None
        else:
            link = None # ignore mailto, etc
        return link
    tree = xpath.Tree(html)
    a_links = tree.search('//a/@href')
    i_links = tree.search('//iframe/@src')
    js_links = re.findall('location.href ?= ?[\'"](.*?)[\'"]', html)
    links = []
    for link in a_links + i_links + js_links:
        try:
            link = normalize_link(str(link))
        except UnicodeError:
            pass
        else:
            if link and link not in links:
                links.append(link)
    return links


def extract_emails(html, ignored=None):
    """Remove common obfuscations from HTML and then extract all emails

    ignored: 
        list of dummy emails to ignore

    >>> extract_emails('')
    []
    >>> extract_emails('hello contact@webscraping.com world')
    ['contact@webscraping.com']
    >>> extract_emails('hello contact@<!-- trick comment -->webscraping.com world')
    ['contact@webscraping.com']
    >>> extract_emails('hello contact AT webscraping DOT com world')
    ['contact@webscraping.com']
    >>> extract_emails(' info+hn@gmail.com ')
    ['info+hn@gmail.com']
    >>> extract_emails('<a href="mailto:first.last@mail.co.uk">Contact</a>')
    ['first.last@mail.co.uk']
    """
    emails = []
    if html:
        email_re = re.compile('([\w\.\-\+]{1,64})@(\w[\w\.-]{1,255})\.(\w+)')
        # remove comments, which can obfuscate emails
        html = re.compile('<!--.*?-->', re.DOTALL).sub('', html).replace('mailto:', '')
        for user, domain, ext in email_re.findall(html):
            if ext.lower() not in common.MEDIA_EXTENSIONS and len(ext)>=2 and not re.compile('\d').search(ext) and domain.count('.')<=3:
                email = '%s@%s.%s' % (user, domain, ext)
                if email not in emails:
                    emails.append(email)

        # look for obfuscated email
        for user, domain, ext in re.compile('([\w\.\-\+]{1,64})\s?.?AT.?\s?([\w\.-]{1,255})\s?.?DOT.?\s?(\w+)', re.IGNORECASE).findall(html):
            if ext.lower() not in common.MEDIA_EXTENSIONS and len(ext)>=2 and not re.compile('\d').search(ext) and domain.count('.')<=3:
                email = '%s@%s.%s' % (user, domain, ext)
                if email not in emails:
                    emails.append(email)
    if ignored:
        emails = [email for email in emails if email not in ignored]
    return emails


def extract_phones(html):
    """Extract phone numbers from this HTML

    >>> extract_phones('Phone: (123) 456-7890 <br>')
    ['(123) 456-7890']
    >>> extract_phones('Phone 123.456.7890 ')
    ['123.456.7890']
    >>> extract_phones('+1-123-456-7890<br />123 456 7890n')
    ['123-456-7890', '123 456 7890']
    >>> extract_phones('456-7890')
    []
    >>> extract_phones('<a href="tel:0234673460">Contact</a>')
    ['0234673460']
    """
    return [match.group() for match in re.finditer('(\+\d{1,2}\s)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}', html)] + re.findall('tel:(\d+)', html)


def parse_us_address(address):
    """Parse USA address into address, city, state, and zip code

    >>> parse_us_address('6200 20th Street, Vero Beach, FL 32966')
    ('6200 20th Street', 'Vero Beach', 'FL', '32966')
    """
    city = state = zipcode = ''
    addrs = map(lambda x:x.strip(), address.split(','))
    if addrs:
        m = re.compile('([A-Z]{2,})\s*(\d[\d\-\s]+\d)').search(addrs[-1])
        if m:
            state = m.groups()[0].strip()
            zipcode = m.groups()[1].strip()

            if len(addrs)>=3:
                city = addrs[-2].strip()
                address = ','.join(addrs[:-2])
            else:
                address = ','.join(addrs[:-1])
            
    return address, city, state, zipcode


def get_earth_radius(scale):
    if scale is None:
        return 1.0
    elif scale == 'km':
        return 6373.0
    elif scale == 'miles':
        return 3960.0
    else:
        raise common.WebScrapingError('Invalid scale: %s' % str(scale))


def distance(p1, p2, scale=None):
    """Calculate distance between 2 (latitude, longitude) points.

    scale:
        By default the distance will be returned as a ratio of the earth's radius
        Use 'km' to return distance in kilometres, 'miles' to return distance in miles

    >>> melbourne = -37.7833, 144.9667
    >>> san_francisco = 37.7750, -122.4183
    >>> int(distance(melbourne, san_francisco, 'km'))
    12659
    """
    if p1 == p2:
        return 0
    lat1, long1 = p1
    lat2, long2 = p2
    # Convert latitude and longitude to 
    # spherical coordinates in radians.
    degrees_to_radians = math.pi / 180.0
        
    # phi = 90 - latitude
    phi1 = (90.0 - lat1)*degrees_to_radians
    phi2 = (90.0 - lat2)*degrees_to_radians
        
    # theta = longitude
    theta1 = long1*degrees_to_radians
    theta2 = long2*degrees_to_radians
        
    cos = (math.sin(phi1) * math.sin(phi2) * math.cos(theta1 - theta2) + math.cos(phi1) * math.cos(phi2))
    arc = math.acos(min(1, cos))
    return arc * get_earth_radius(scale)


def find_coordinates(ch_lat=100, ch_lng=100, ch_scale='miles', min_lat=-90, max_lat=90, min_lng=-180, max_lng=180):
    """Find all latitude/longitude coordinates within bounding box, with given increments
    """
    cur_lat = min_lat
    while cur_lat < max_lat:
        cur_lng = min_lng
        while cur_lng < max_lng:
            yield cur_lat, cur_lng
            _, cur_lng = move_coordinate(cur_lat, cur_lng, 0, ch_lng, ch_scale)
        cur_lat, _ = move_coordinate(cur_lat, cur_lng, ch_lat, 0, ch_scale)


def move_coordinate(lat, lng, ch_lat, ch_lng, ch_scale=None):
    """Move latitude/longitude coordinate a given increment
    """
    r_earth = get_earth_radius(ch_scale)
    new_lat = lat + (ch_lat / r_earth) * (180 / math.pi);
    new_lng = lng + (ch_lng / r_earth) * (180 / math.pi) / math.cos(lat * math.pi/180.0)
    return new_lat, new_lng


def find_json_path(e, value, path=''):
    """Find the JSON path that points to this value
    """
    results = []
    if e == value:
        results.append(path)
    if isinstance(e, dict):
        for k, v in e.items():
            key_path = '{}["{}"]'.format(path, k)
            results.extend(find_json_path(v, value, key_path))
    elif isinstance(e, list):
        for i, v in enumerate(e):
            index_path = '{}[{}]'.format(path, i)
            results.extend(find_json_path(v, value, index_path))
    return results


def download_zipcodes(country_code):
    """Download zipcodes for this country code
    """
    if country_code in ('ca', 'gb', 'nl'):
        filename = country_code.upper() + '_full.csv.zip'
    else:
        filename = country_code.upper() + '.zip'
    if os.path.exists(filename):
        zip_data = open(filename, 'rb').read()
    else:
        zip_data = requests.get('http://download.geonames.org/export/zip/%s' % filename).content
    found = False
    if zip_data:
        input_zip = io.BytesIO()
        input_zip.write(zip_data)
        zf = zipfile.ZipFile(input_zip)
        for filename in zf.namelist():
            if country_code.upper() in filename:
                tsv_data = zf.read(filename).decode('utf-8')
                for row in csv.reader(tsv_data.splitlines(), delimiter='\t'):
                    zip_code, city, state = row[1:4]
                    lat, lng = row[9:11]
                    found = True
                    yield zip_code, lng, lat, city, state
                break

    if not found:
        search_html = requests.get('http://www.geonames.org/postalcode-search.html?q=&country=' + country_code.upper()).text
        trs = xpath.search(search_html, '//table[@class="restable"]/tr')
        while trs:
            tds = trs.pop(0).search('./td')
            if any(tds):
                city = str(tds[1])
                zip_code = str(tds[2])
                tds = trs.pop(0).search('./td')
                lat, lng = str(tds[1].get('./a/small')).split('/')
                yield zip_code, lng, lat, city, ''


def generate_zipcode_file(country_code, should_split=False):
    """Generate zip code file for the given country code ordered by the minimum distance apart with proceeding zip codes
    """
    outstanding_zips = {}
    for zip_code, lng, lat, city, state in download_zipcodes(country_code):
        if should_split:
            zip_code = zip_code.split('-')[0]
        if country_code == 'us':
            import zipcodes
            result_list = zipcodes.matching(zip_code)
            if result_list:
                result = result_list[0]
                if result['zip_code_type'] != 'STANDARD':
                    continue
            else:
                continue
        if lat and lng and 'CEDEX' not in zip_code:
            outstanding_zips[zip_code] = float(lng), float(lat), city, state
    # keep track of the current max distance from a zip code
    # any further added points can only be closer
    max_known_distances = {}
    max_distance = 10000

    output_rows = []
    for min_distance in reversed(range(1, max_distance + 1)):
        print(min_distance)
        zip_codes = list(outstanding_zips.keys())
        random.shuffle(zip_codes)
        for zip_code in zip_codes:
            max_known_distance = max_known_distances.get(zip_code, max_distance)
            if max_known_distance < min_distance:
                # can skip processing this coordinate until target distance reaches the known maximum distance
                continue
            lng, lat, city, state = outstanding_zips[zip_code]
            for _, other_lng, other_lat, *_ in output_rows:
                this_distance = distance((lat, lng), (other_lat, other_lng), scale='km')
                if this_distance < min_distance:
                    # this distance is less than the current target
                    max_known_distances[zip_code] = this_distance
                    break
            else:
                # found a new coordinates that is atleast the target distance away from every other coordinate
                output_rows.append((zip_code, lng, lat, city, state, min_distance))
                del outstanding_zips[zip_code]
    # add any leftover points with 0 distance
    for zip_code, (lng, lat, city, state) in outstanding_zips.items():
        output_rows.append((zip_code, lng, lat, city, state, 0))

    output_filename = '%s_locations.csv' % country_code
    writer = csv.writer(open(output_filename, 'w'))
    writer.writerow(['Zip code', 'Longitude', 'Latitude', 'City', 'State', 'Distance'])
    writer.writerows(output_rows)
    print('Output to', output_filename)


def get_zip_codes(filename, distance=None):
    """Get unique zip codes this minimum distance apart, including when at same lat/lng
    """
    for row in get_zip_lng_lats(filename, distance):
        yield row[0]


def get_zip_lng_lats(filename, distance=None):
    """Get zip, lng, lat that are a minimum distance apart
    """
    reader = csv.reader(open(filename))
    header = next(reader)
    for zip_code, lng, lat, *_, zip_distance in reader:
        lng, lat, zip_distance = float(lng), float(lat), int(zip_distance)
        if distance is None or distance <= zip_distance:
            yield zip_code, lng, lat
        else:
            break
