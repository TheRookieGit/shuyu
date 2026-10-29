import tempfile
import unittest
from unittest.mock import patch

from collectionkit.hub import Hub
from collectionkit.testing import FakeHub
from tests.test_library import LibraryFixture
from wishlist import WEB


def wish(uid,title,author='',**extra):
    return {'id':uid,'category':'novel','title':title,'extra':{'author':author} if author else {},'links':[],'kind':'novel',
            'createdAt':'2026-01-01T00:00:00Z','updatedAt':'2026-01-01T00:00:00Z','done':False,**extra}


class HubWishlistTests(LibraryFixture,unittest.TestCase):
    """The wishlist connected to the wish hub, with the hub in memory."""
    def setUp(self):
        super().setUp()
        self.shared = tempfile.TemporaryDirectory()
        self.fake = FakeHub()
        self.patch = patch('collectionkit.hub.fetch',side_effect=self.fake)
        self.patch.start()
        self.wishes = self.lib.wishlist
        self.wishes.hub = Hub('shuyu',self.shared.name)

    def tearDown(self):
        self.patch.stop()
        self.shared.cleanup()
        super().tearDown()

    def owned(self,uid):
        return self.fake.fulfilled.get((uid,'shuyu'))

    def test_connecting_uploads_the_local_wishes_once_and_reports_what_is_on_the_shelf(self):
        self.wishes.save({'title':'春日','author':'小林','kind':'novel','url':'https://example.com/spring'})
        self.wishes.save({'title':'还没有的书','kind':'novel'})
        self.fake.add(wish('h1','春日','小林'))
        self.fake.add(wish('h2','夜航'))
        self.fake.add({'id':'m1','category':'movie','title':'别的分类'})
        status = self.wishes.pair(self.fake.token('shuyu'))
        self.assertTrue(status['paired'])
        titles = sorted(i['title'] for i,_ in self.fake.items.values() if i['category']=='novel')
        self.assertEqual(titles,['夜航','春日','还没有的书'],'春日 was in the hub already, so it is not sent twice')
        listed = {i['title']:i for i in self.wishes.items()['items']}
        self.assertEqual(set(listed),{'春日','夜航','还没有的书'});self.assertTrue(self.wishes.items()['hub'])
        self.assertTrue(listed['春日']['downloaded'] and listed['夜航']['downloaded'] and not listed['还没有的书']['downloaded'])
        spring = self.owned('h1')
        self.assertEqual(spring['ref'],listed['春日']['book_id'])
        self.assertEqual(spring['url'],WEB+'#book='+spring['ref']);self.assertTrue(spring['ownedAt'])
        self.assertTrue(self.owned('h2'))
        with self.lib.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM wishlist').fetchone()[0],2,'the local list stays as it was')
        # Connecting again later does not upload again.
        self.wishes.unpair()
        self.assertEqual([i['title'] for i in self.wishes.items()['items']].count('春日'),1,'back to the local list')
        self.wishes.pair(self.fake.token('shuyu'))
        self.assertEqual(len([i for i,_ in self.fake.items.values() if i['category']=='novel']),3)

    def test_a_match_the_hub_rejects_is_undone_and_not_made_again(self):
        self.fake.add(wish('h1','夜航'))
        self.wishes.pair(self.fake.token('shuyu'))
        self.assertTrue(self.wishes.item('h1')['downloaded'])
        self.fake.reject('h1','shuyu')
        calls = len(self.fake.calls)
        self.wishes.pull()
        self.assertFalse(self.wishes.item('h1')['downloaded'],'不是这个')
        self.assertEqual([c for c,_ in self.fake.calls[calls:]],['mc_wishes'],'the rejected book is not reported again')
        self.scan()
        self.assertFalse(self.wishes.item('h1')['downloaded'])

    def test_wishes_added_here_go_to_the_hub_and_wait_while_it_is_offline(self):
        self.wishes.pair(self.fake.token('shuyu'))
        added = self.wishes.save({'title':'在书屿里加的','author':'作者','kind':'zip','url':'https://example.com/a','note':'备注'})
        sent = self.fake.items[added['id']][0]
        self.assertEqual((sent['category'],sent['kind'],sent['extra'],sent['links'],sent['source']),('novel','zip',{'author':'作者'},['https://example.com/a'],'shuyu'))
        self.fake.offline = True
        offline = self.wishes.save({'title':'离线时加的','kind':'novel'})
        self.assertEqual(self.wishes.item(offline['id'])['title'],'离线时加的','the app goes on without the hub')
        self.assertNotIn(offline['id'],self.fake.items)
        self.assertFalse(self.wishes.pull())
        self.wishes.delete(added['id'])
        self.fake.offline = False
        self.assertTrue(self.wishes.pull())
        self.assertIn(offline['id'],self.fake.items);self.assertTrue(self.fake.items[added['id']][0]['deleted'])
        self.assertNotIn(added['id'],[i['id'] for i in self.wishes.items()['items']])

    def test_changes_in_the_hub_arrive_with_the_next_pull(self):
        self.fake.add(wish('h1','还没有的书',links=['https://pan.quark.cn/s/abc 提取码 1234','https://pan.baidu.com/s/1x']))
        self.wishes.pair(self.fake.token('shuyu'))
        self.assertEqual(self.wishes.item('h1')['url'],'https://pan.quark.cn/s/abc')
        # Editing here keeps the hub's other links and the code after an unchanged address.
        self.wishes.save({'id':'h1','title':'还没有的书','kind':'novel','url':'https://pan.quark.cn/s/abc','note':'改了备注'})
        self.assertEqual(self.fake.items['h1'][0]['links'],['https://pan.quark.cn/s/abc 提取码 1234','https://pan.baidu.com/s/1x'])
        self.fake.add(wish('h1','夜航',updatedAt='2026-05-01T00:00:00Z'))
        self.fake.add(wish('h2','春日',deleted=True,updatedAt='2026-05-01T00:00:00Z'))
        self.wishes.pull()
        self.assertTrue(self.wishes.item('h1')['downloaded'],'renamed in the hub, then matched')
        self.assertEqual([i['id'] for i in self.wishes.items()['items']],['h1'])


    def test_screenshots_come_from_the_hub_and_go_back_with_the_wish(self):
        self.fake.images['shot-hub-1'] = 'data:image/png;base64,iVBO'
        self.fake.add(wish('h1','截图里的书',shots=['shot-hub-1'],rating=5))
        self.wishes.pair(self.fake.token('shuyu'))
        listed = self.wishes.items()['items'][0]
        self.assertEqual(listed['shots'],['shot-hub-1'])
        self.assertEqual(self.wishes.shot('shot-hub-1')['data'],'data:image/png;base64,iVBO')
        added = self.wishes.add_shot('data:image/jpeg;base64,/9j/')['id']
        self.wishes.save({'id':'h1','title':'截图里的书','kind':'novel','shots':[added]})
        sent = self.fake.items['h1'][0]
        self.assertEqual(sent['shots'],[added],"the hub's screenshot removed, the new one added")
        self.assertEqual(sent['rating'],5,'the rest of the hub item stays')
        self.assertEqual(self.fake.images[added],'data:image/jpeg;base64,/9j/')
        self.wishes.save({'id':'h1','title':'改个名','kind':'novel'})
        self.assertEqual(self.fake.items['h1'][0]['shots'],[added],'saving without shots leaves them alone')
        with self.assertRaises(ValueError):self.wishes.shot('shot-none')
        self.wishes.unpair()
        with self.assertRaises(ValueError):self.wishes.add_shot('data:image/jpeg;base64,/9j/')

if __name__=='__main__':
    unittest.main()
