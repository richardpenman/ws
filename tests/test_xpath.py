import pytest
from ws.xpath import Tree, Form
from ws.download import Response


class TestTree:
    def test_bool_valid_doc(self):
        assert bool(Tree('<p>hello</p>'))

    def test_bool_none_doc(self):
        assert not bool(Tree(None))

    def test_get_returns_first_match(self):
        t = Tree('<div><p>first</p><p>second</p></div>')
        assert str(t.get('//p')) == 'first'

    def test_get_missing_returns_falsy_tree(self):
        result = Tree('<div></div>').get('//p')
        assert not result
        assert str(result) == ''

    def test_search_returns_all_matches(self):
        t = Tree('<ul><li>a</li><li>b</li><li>c</li></ul>')
        assert [str(e) for e in t.search('//li')] == ['a', 'b', 'c']

    def test_search_no_match_returns_empty_list(self):
        assert Tree('<div></div>').search('//p') == []

    def test_str_text_content(self):
        assert str(Tree('<p>hello world</p>')) == 'hello world'

    def test_str_none_returns_empty_string(self):
        assert str(Tree(None)) == ''

    def test_remove_element(self):
        t = Tree('<div><p>keep</p><span>gone</span></div>')
        span = t.get('//span')
        assert t.remove(span) is True
        assert t.search('//span') == []
        assert str(t.get('//p')) == 'keep'

    def test_remove_already_removed_raises(self):
        t = Tree('<div><span>a</span></div>')
        span = t.get('//span')
        assert t.remove(span) is True
        # removing again raises because parent is now None
        with pytest.raises(AttributeError):
            t.remove(span)

    def test_json_parses_text_content(self):
        assert Tree('<pre>{"key": 1}</pre>').get('//pre').json() == {'key': 1}

    def test_regex_on_text_content(self):
        assert Tree('<p>price $42.50</p>').get('//p').regex(r'\$[\d.]+').group() == '$42.50'

    def test_none_input_search_returns_empty(self):
        assert Tree(None).search('//p') == []

    def test_none_input_get_returns_falsy(self):
        assert not Tree(None).get('//p')

    def test_attribute_access(self):
        t = Tree('<a href="http://example.com">link</a>')
        assert str(t.get('//a/@href')) == 'http://example.com'


class TestForm:
    def test_input_fields_extracted(self):
        form = Form(Tree('<form><input name="user" value="alice"><input name="pass" value="secret"></form>'))
        assert form['user'] == 'alice'
        assert form['pass'] == 'secret'

    def test_textarea_extracted(self):
        form = Form(Tree('<form><textarea name="msg">hello</textarea></form>'))
        assert form['msg'] == 'hello'

    def test_selected_option_extracted(self):
        html = '<form><select name="color"><option value="red">Red</option><option value="blue" selected>Blue</option></select></form>'
        form = Form(Tree(html))
        assert form['color'] == 'blue'

    def test_nameless_inputs_ignored(self):
        form = Form(Tree('<form><input value="orphan"><input name="named" value="kept"></form>'))
        assert '' not in form.data
        assert form['named'] == 'kept'

    def test_setitem(self):
        form = Form(Tree('<form><input name="q" value="original"></form>'))
        form['q'] = 'updated'
        assert form['q'] == 'updated'

    def test_str_is_urlencoded(self):
        form = Form(Tree('<form><input name="q" value="hello"></form>'))
        assert 'q=hello' in str(form)

    def test_form_accepts_response_tree(self):
        resp = Response('<form><input name="token" value="abc123"></form>', 200, '')
        form = Form(resp.get_tree())
        assert form['token'] == 'abc123'
