import itertools, json, re, sys, urllib, urllib.parse
from optparse import OptionParser
import lxml.html
import lxml.etree


class Tree:
    def __init__(self, doc, **kwargs):
        if doc is None:
            self.doc = None
        elif isinstance(doc, lxml.html.HtmlElement):
            # input is already a passed lxml tree
            self.doc = doc
        else:
            try:
                try:
                    self.doc = lxml.html.fromstring(doc)
                except ValueError:
                    # For error: Unicode strings with encoding declaration are not supported. Please use bytes input or XML fragments without declaration
                    self.doc = lxml.html.fromstring(doc.encode('utf-8'))
            except lxml.etree.LxmlError as e:
                if doc.strip():
                    print('Error parsing doc:', e)
                self.doc = None

    def search(self, path):
        if self.doc is None:
            return []
        else:
            return [Tree(e) for e in self.doc.xpath(path)]

    def get(self, path):
        es = self.search(path)
        if es:
            return es[0]
        else:
            return Tree(None)

    def remove(self, e):
        try:
            e.doc.getparent().remove(e.doc)
        except (ValueError, AttributeError):
            return False
        else:
            return True

    def regex(self, r, flags=0):
        return re.compile(r, flags=flags).search(str(self))

    def json(self):
        return json.loads(str(self))

    def __str__(self):
        if self.doc is None:
            return ''
        else:
            try:
                parts = [self.doc.text] + [c if isinstance(c, str) else lxml.etree.tostring(c).decode() for c in self.doc] + [self.doc.tail]
                return ''.join(filter(None, parts)) #or str(self.doc)
            except AttributeError as e:
                print('Error parsing node:', e)
                return ''

    def __bool__(self):
        return self.doc is not None

    def serialize(self, encoding='unicode'):
        return lxml.etree.tostring(self.doc, encoding=encoding)


def get(html, xpath, remove=None):
    """Return first element from XPath search of HTML
    """
    return str(Tree(html, remove=remove).get(xpath))

def search(html, xpath, remove=None):
    """Return all elements from XPath search of HTML
    """
    return [str(e) for e in Tree(html, remove=remove).search(xpath)]


class Form:
    """Helper class for filling and submitting forms
    """
    def __init__(self, form_response):
        self.data = {}
        for form_input in form_response.search('//input'):
            self.data[str(form_input.get('@name'))] = str(form_input.get('@value'))
        for textarea in form_response.search('//textarea'):
            self.data[str(textarea.get('@name'))] = str(textarea)
        for option in form_response.search('//select/option[@selected]'):
            select = str(option.get('../@name'))
            self.data[select] = str(option.get('@value'))
        if '' in self.data:
            del self.data['']

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __str__(self):
        return urllib.parse.urlencode(self.data)

    def submit(self, D, action, **argv):
        return D.get(url=action, data=self.data, **argv)
