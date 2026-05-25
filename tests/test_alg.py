import pytest
from ws.alg import get_links, extract_emails, extract_phones, decode_cf_email


class TestGetLinks:
    def test_absolute_link(self):
        assert get_links('<a href="http://example.com/page">link</a>') == ['http://example.com/page']

    def test_relative_link_resolved(self):
        assert get_links('<a href="/page">link</a>', url='http://example.com') == ['http://example.com/page']

    def test_anchor_stripped(self):
        assert get_links('<a href="http://example.com/page#section">x</a>') == ['http://example.com/page']

    def test_pure_anchor_dropped(self):
        assert get_links('<a href="#top">top</a>') == []

    def test_mailto_ignored(self):
        assert get_links('<a href="mailto:a@b.com">email</a>') == []

    def test_iframe_src_included(self):
        assert get_links('<iframe src="http://example.com/frame"></iframe>') == ['http://example.com/frame']

    def test_local_links_excluded(self):
        html = '<a href="/local">local</a><a href="http://other.com">ext</a>'
        links = get_links(html, url='http://example.com', local=False)
        assert 'http://other.com' in links
        assert not any('example.com' in l for l in links)

    def test_external_links_excluded(self):
        html = '<a href="/local">local</a><a href="http://other.com">ext</a>'
        links = get_links(html, url='http://example.com', external=False)
        assert 'http://example.com/local' in links
        assert 'http://other.com' not in links

    def test_deduplication(self):
        html = '<a href="/page">1</a><a href="/page">2</a>'
        links = get_links(html, url='http://example.com')
        assert len(links) == 1

    def test_empty_html(self):
        assert get_links('') == []

    def test_js_location_href(self):
        html = "location.href = 'http://example.com/redirect'"
        assert 'http://example.com/redirect' in get_links(html)


class TestExtractEmails:
    def test_plain_email(self):
        assert extract_emails('contact@example.com') == ['contact@example.com']

    def test_multiple_emails(self):
        emails = extract_emails('alice@example.com and bob@other.org')
        assert 'alice@example.com' in emails
        assert 'bob@other.org' in emails

    def test_at_dot_obfuscation(self):
        assert extract_emails('user AT example DOT com') == ['user@example.com']

    def test_html_comment_obfuscation(self):
        emails = extract_emails('user@<!-- trick -->example.com')
        assert 'user@example.com' in emails

    def test_mailto_href(self):
        assert 'info@example.com' in extract_emails('<a href="mailto:info@example.com">contact</a>')

    def test_plus_address(self):
        assert 'info+hn@gmail.com' in extract_emails('info+hn@gmail.com')

    def test_ignored_list(self):
        emails = extract_emails('alice@example.com bob@other.com', ignored=['alice@example.com'])
        assert 'alice@example.com' not in emails
        assert 'bob@other.com' in emails

    def test_empty_input(self):
        assert extract_emails('') == []

    def test_no_duplicates(self):
        assert extract_emails('alice@example.com alice@example.com').count('alice@example.com') == 1


class TestExtractPhones:
    def test_parentheses_format(self):
        assert '(123) 456-7890' in extract_phones('Call (123) 456-7890 now')

    def test_dot_format(self):
        assert '123.456.7890' in extract_phones('Phone 123.456.7890 ')

    def test_dash_format(self):
        assert any('123-456-7890' in r for r in extract_phones('+1-123-456-7890'))

    def test_tel_href(self):
        assert '0234673460' in extract_phones('<a href="tel:0234673460">call</a>')

    def test_short_number_ignored(self):
        assert extract_phones('456-7890') == []

    def test_empty_input(self):
        assert extract_phones('') == []

    def test_multiple_phones(self):
        assert len(extract_phones('(123) 456-7890 and 123.456.7890')) == 2


class TestDecodeCfEmail:
    def test_known_encoded_value(self):
        assert decode_cf_email('107150723e73') == 'a@b.c'

    def test_empty_string(self):
        assert decode_cf_email('') == ''

    def test_invalid_hex_returns_empty(self):
        assert decode_cf_email('zz1234') == ''
