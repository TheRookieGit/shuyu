import unittest

from tests.test_library import LibraryFixture


class CatalogTreeTests(LibraryFixture,unittest.TestCase):
    def test_counts_follow_folders_and_nested_archives(self):
        tree=self.lib.tree
        root=tree.node([])
        self.assertEqual(root['counts']['total'],8)
        children={c['name']:c for c in root['children']}
        self.assertEqual((children['合集.zip']['kind'],children['合集.zip']['total']),('archive',3))
        self.assertEqual((children['未整理']['kind'],children['未整理']['total']),('folder',1))
        self.assertNotIn('漫画',children,'an image folder is one comic, not a folder to open')
        self.assertEqual(sorted(b['name'] for b in root['books']),['sample.pdf','春日.epub','漫画','漫画.zip'])
        self.assertEqual(root['counts']['comics'],2)
        bundle=tree.node(['合集.zip'])
        self.assertEqual([b['title'] for b in bundle['books']],['故事'])
        deepest=tree.node(['合集.zip','第一层','第二层.zip','深一层.zip','书'])
        self.assertEqual([b['title'] for b in deepest['books']],['夜航'])
        with self.assertRaises(ValueError):tree.node(['不存在'])
        with self.assertRaises(ValueError):tree.node('合集.zip')

    def test_rescans_and_duplicates_refresh_the_counts(self):
        self.assertEqual(self.lib.tree.node([])['counts']['unique'],8)
        (self.root/'重复').mkdir()
        (self.root/'重复'/'《夜航》作者：小舟.txt').write_text('另一份副本',encoding='utf-8')
        self.scan()
        counts=self.lib.tree.node([])['counts']
        self.assertEqual((counts['total'],counts['unique']),(9,8),'same title and author is merged')
        self.assertEqual(self.lib.tree.node(['重复'])['books'][0]['canonical'],self.book('夜航')['id'])


if __name__=='__main__':unittest.main()
