import datetime
import time
import pytest
from ws.pdict import PersistentDict


class TestPersistentDictBasics:
    def setup_method(self):
        self.db = PersistentDict(':memory:')

    def test_set_and_get(self):
        self.db['key'] = 'value'
        assert self.db['key'] == 'value'

    def test_contains_true(self):
        self.db['k'] = 1
        assert 'k' in self.db

    def test_contains_false(self):
        assert 'missing' not in self.db

    def test_delete(self):
        self.db['k'] = 1
        del self.db['k']
        assert 'k' not in self.db

    def test_len(self):
        self.db['a'] = 1
        self.db['b'] = 2
        assert len(self.db) == 2

    def test_iter_yields_keys(self):
        self.db['x'] = 1
        self.db['y'] = 2
        assert set(self.db) == {'x', 'y'}

    def test_missing_key_raises_key_error(self):
        with pytest.raises(KeyError):
            _ = self.db['nonexistent']

    def test_python_objects_roundtrip(self):
        obj = {'nested': [1, 2, 3], 'flag': True}
        self.db['obj'] = obj
        assert self.db['obj'] == obj

    def test_overwrite_existing_key(self):
        self.db['k'] = 'first'
        self.db['k'] = 'second'
        assert self.db['k'] == 'second'
        assert len(self.db) == 1

    def test_bool_always_true(self):
        assert bool(self.db)

    def test_get_returns_default_for_missing(self):
        assert self.db.get('missing') is None
        assert self.db.get('missing', 'fallback') == 'fallback'

    def test_get_returns_dict_with_value_and_meta(self):
        self.db['k'] = 42
        row = self.db.get('k')
        assert row['value'] == 42
        assert 'meta' in row
        assert 'updated' in row

    def test_clear_removes_all(self):
        self.db['a'] = 1
        self.db['b'] = 2
        self.db.clear()
        assert len(self.db) == 0


class TestPersistentDictTTL:
    def test_fresh_key_accessible(self):
        db = PersistentDict(':memory:', expires=datetime.timedelta(seconds=60))
        db['k'] = 'v'
        assert db['k'] == 'v'

    def test_expired_key_raises_key_error(self):
        db = PersistentDict(':memory:', expires=datetime.timedelta(seconds=0))
        db['k'] = 'v'
        with pytest.raises(KeyError):
            _ = db['k']

    def test_expired_key_not_in_contains(self):
        db = PersistentDict(':memory:', expires=datetime.timedelta(seconds=0))
        db['k'] = 'v'
        assert 'k' not in db

    def test_no_expiry_by_default(self):
        db = PersistentDict(':memory:')
        db['k'] = 'v'
        assert 'k' in db

    def test_touch_updates_timestamp(self):
        db = PersistentDict(':memory:')
        db['k'] = 'v'
        old_updated = db.get('k')['updated']
        time.sleep(0.01)
        db.touch('k')
        new_updated = db.get('k')['updated']
        assert new_updated > old_updated

    def test_touch_with_explicit_time(self):
        db = PersistentDict(':memory:')
        db['k'] = 'v'
        t = datetime.datetime(2020, 1, 1)
        db.touch('k', t)
        assert db.get('k')['updated'] == t


class TestPersistentDictBatch:
    def setup_method(self):
        self.db = PersistentDict(':memory:')

    def test_add_dict(self):
        self.db.add({'a': 1, 'b': 2, 'c': 3})
        assert self.db['a'] == 1
        assert self.db['b'] == 2
        assert self.db['c'] == 3
        assert len(self.db) == 3

    def test_add_empty_dict_no_op(self):
        self.db.add({})
        assert len(self.db) == 0

    def test_add_overwrites_existing(self):
        self.db['k'] = 'old'
        self.db.add({'k': 'new'})
        assert self.db['k'] == 'new'

    def test_delete_keys(self):
        self.db['x'] = 1
        self.db['y'] = 2
        self.db['z'] = 3
        self.db.delete(['x', 'z'])
        assert 'x' not in self.db
        assert 'z' not in self.db
        assert 'y' in self.db

    def test_delete_empty_list_no_op(self):
        self.db['k'] = 1
        self.db.delete([])
        assert 'k' in self.db


class TestPersistentDictMeta:
    def setup_method(self):
        self.db = PersistentDict(':memory:')
        self.db['k'] = 'value'

    def test_default_meta_is_empty_dict(self):
        assert self.db.meta('k') == {}

    def test_set_and_get_meta(self):
        self.db.meta('k', {'source': 'test', 'score': 99})
        assert self.db.meta('k') == {'source': 'test', 'score': 99}

    def test_meta_missing_key_raises(self):
        with pytest.raises(KeyError):
            self.db.meta('nonexistent')

    def test_meta_update_does_not_change_value(self):
        self.db.meta('k', 'some metadata')
        assert self.db['k'] == 'value'


class TestPersistentDictRename:
    def setup_method(self):
        self.db = PersistentDict(':memory:')

    def test_rename_moves_value(self):
        self.db['old'] = 'value'
        self.db.rename('old', 'new')
        assert 'old' not in self.db
        assert self.db['new'] == 'value'

    def test_rename_overwrites_existing_target(self):
        self.db['a'] = 'from_a'
        self.db['b'] = 'from_b'
        self.db.rename('a', 'b')
        assert self.db['b'] == 'from_a'
        assert 'a' not in self.db
        assert len(self.db) == 1


class TestPersistentDictMerge:
    """Note: merge uses db.keys() so only works with dict-like sources, not PersistentDict."""

    def setup_method(self):
        self.db = PersistentDict(':memory:')

    def test_merge_adds_missing_keys(self):
        self.db['a'] = 1
        self.db.merge({'b': 2, 'c': 3})
        assert self.db['b'] == 2
        assert self.db['c'] == 3
        assert self.db['a'] == 1

    def test_merge_no_override_by_default(self):
        self.db['k'] = 'original'
        self.db.merge({'k': 'new'})
        assert self.db['k'] == 'original'

    def test_merge_with_override(self):
        self.db['k'] = 'original'
        self.db.merge({'k': 'new'}, override=True)
        assert self.db['k'] == 'new'
