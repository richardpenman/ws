
import collections, json, re, urllib, urllib.parse

from . import common


class GoogleMaps:
    def __init__(self, D, api_key):
        self.D = D
        self.api_key = api_key

    def geocode(self, address, delay=5, max_retries=1, language=None):
        """Geocode address using Google's API and return dictionary of useful fields

        address:
            what to pass to geocode API
        delay:
            how long to delay between API requests
        max_retries:
            the number of times to try downloading
        language:
            the language to set
        """
        address = re.sub(r'%C2%9\d*', '', urllib.parse.quote_plus(address))
        geocode_url = 'https://maps.google.com/maps/api/geocode/json?address=%s&key=%s&sensor=false%s' % (address, self.api_key, '&language=' + language if language else '')
        geocode_response = self.D.get(geocode_url, delay=delay, max_retries=max_retries, use_proxy=False, auto_encoding=False)
        geocode_data = self.load_result(geocode_url, geocode_response.text)
        for result in geocode_data.get('results', []):
            return self.parse_location(result)
        return collections.defaultdict(str)


    def load_result(self, url, html):
        """Parse the result from API

        If JSON is well formed and status is OK then will return result
        Else will return an empty dict
        """
        if html:
            try:
                search_data = json.loads(html)
            except ValueError as e:
                common.logger.debug(str(e))
            else:
                status = search_data['status']
                if status == 'OK':
                    return search_data
                elif status == 'ZERO_RESULTS':
                    pass
                elif status == 'OVER_QUERY_LIMIT':
                    # error geocoding - try again later
                    common.logger.info('Over query limit')
                    del self.D.cache[url]
                elif status in ('REQUEST_DENIED', 'INVALID_REQUEST'):
                    common.logger.info('{0}: {1}'.format(status, url))
        return {}


    def parse_location(self, result):
        """Parse address data from Google's geocoding response into a more usable flat structure

        Example: https://developers.google.com/maps/documentation/geocoding/#JSON
        """
        results = collections.defaultdict(str)
        for e in result['address_components']:
            # parse address compenents into flat layer
            types, value, abbrev = e['types'], e['long_name'], e['short_name']
            if 'street_number' in types:
                results['number'] = value
            elif 'route' in types:
                results['street'] = value
            elif 'postal_code' in types:
                results['postcode'] = value
            elif 'locality' in types:
                results['suburb'] = value
            elif 'administrative_area_level_1' in types:
                results['state'] = value
                results['state_code'] = abbrev
            elif 'administrative_area_level_2' in types:
                results['county'] = value
            elif 'administrative_area_level_3' in types:
                results['district'] = value
            elif 'country' in types:
                results['country'] = value
                results['country_code'] = abbrev

        # extract addresses
        results['full_address'] = result['formatted_address']
        if 'street' in results:
            results['address'] = (results['number'] + ' ' + results['street']).strip()

        results['lat'] = result['geometry']['location']['lat']
        results['lng'] = result['geometry']['location']['lng']
        results['types'] = result['types']
        results['place_id'] = result['place_id']
        return results
