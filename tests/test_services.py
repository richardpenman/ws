import pytest
from ws.services import GoogleMaps
from ws.download import Download, Response


SAMPLE_RESULT = {
    'formatted_address': '10 Main St, Springfield, IL 62701, United States',
    'geometry': {'location': {'lat': 39.7, 'lng': -89.6}},
    'types': ['street_address'],
    'place_id': 'ChIJtest',
    'address_components': [
        {'types': ['street_number'], 'long_name': '10', 'short_name': '10'},
        {'types': ['route'], 'long_name': 'Main St', 'short_name': 'Main St'},
        {'types': ['locality'], 'long_name': 'Springfield', 'short_name': 'Springfield'},
        {'types': ['administrative_area_level_1'], 'long_name': 'Illinois', 'short_name': 'IL'},
        {'types': ['administrative_area_level_2'], 'long_name': 'Sangamon County', 'short_name': 'Sangamon County'},
        {'types': ['postal_code'], 'long_name': '62701', 'short_name': '62701'},
        {'types': ['country'], 'long_name': 'United States', 'short_name': 'US'},
    ]
}


class TestParseLocation:
    def setup_method(self):
        self.gm = GoogleMaps(Download(cache_file=':memory:'), api_key='test')

    def test_street_number_and_route(self):
        result = self.gm.parse_location(SAMPLE_RESULT)
        assert result['number'] == '10'
        assert result['street'] == 'Main St'
        assert result['address'] == '10 Main St'

    def test_locality_and_state(self):
        result = self.gm.parse_location(SAMPLE_RESULT)
        assert result['suburb'] == 'Springfield'
        assert result['state'] == 'Illinois'
        assert result['state_code'] == 'IL'

    def test_postcode_and_country(self):
        result = self.gm.parse_location(SAMPLE_RESULT)
        assert result['postcode'] == '62701'
        assert result['country'] == 'United States'
        assert result['country_code'] == 'US'

    def test_county(self):
        result = self.gm.parse_location(SAMPLE_RESULT)
        assert result['county'] == 'Sangamon County'

    def test_lat_lng(self):
        result = self.gm.parse_location(SAMPLE_RESULT)
        assert result['lat'] == 39.7
        assert result['lng'] == -89.6

    def test_full_address(self):
        result = self.gm.parse_location(SAMPLE_RESULT)
        assert result['full_address'] == '10 Main St, Springfield, IL 62701, United States'

    def test_place_id_and_types(self):
        result = self.gm.parse_location(SAMPLE_RESULT)
        assert result['place_id'] == 'ChIJtest'
        assert result['types'] == ['street_address']


class TestLoadResult:
    def setup_method(self):
        self.d = Download(cache_file=':memory:')
        self.gm = GoogleMaps(self.d, api_key='test')

    def test_ok_status_returns_data(self):
        html = '{"status": "OK", "results": [{"place_id": "abc"}]}'
        result = self.gm.load_result('http://maps.example.com', html)
        assert result['status'] == 'OK'

    def test_zero_results_returns_empty(self):
        assert self.gm.load_result('url', '{"status": "ZERO_RESULTS"}') == {}

    def test_over_query_limit_deletes_cache_key(self):
        url = 'http://maps.example.com/cached'
        self.d.cache[url] = 'cached response'
        self.gm.load_result(url, '{"status": "OVER_QUERY_LIMIT"}')
        assert url not in self.d.cache

    def test_request_denied_returns_empty(self):
        assert self.gm.load_result('url', '{"status": "REQUEST_DENIED"}') == {}

    def test_invalid_request_returns_empty(self):
        assert self.gm.load_result('url', '{"status": "INVALID_REQUEST"}') == {}

    def test_malformed_json_returns_empty(self):
        assert self.gm.load_result('url', 'not json') == {}

    def test_empty_html_returns_empty(self):
        assert self.gm.load_result('url', '') == {}
