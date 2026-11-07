import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class FillerTests(unittest.TestCase):
    def test_filler_text(self):
        from norway_company_agent.footprint import filler_text, is_demo_item
        self.assertTrue(filler_text("Sed pretium, ligula sollicitudin laoreet viverra, tortor libero sodales leo, eget blandit nunc."))
        self.assertTrue(filler_text("The house opposite commanded a plain view into the room, the indecorous figure that Queequeg made."))
        self.assertTrue(filler_text("Lorem ipsum dolor sit amet"))
        self.assertFalse(filler_text("Vi har ansatt en ny prosjektleder med lang erfaring fra bygg og anlegg i Vestfold."))
        self.assertFalse(filler_text("Vestibulum er et anatomisk begrep; vi behandler svimmelhet."))
        self.assertTrue(is_demo_item({"title": "Vivamus vestibulum ntulla necante", "url": "https://hijabhus.no/2020/09/06/vivamus-vestibulum-ntulla-necante/"}))
        self.assertFalse(is_demo_item({"title": "Åpen dag ved Atico", "url": "https://www.atico.no/nyheter/apen-dag-ved-atico/"}))
        self.assertTrue(is_demo_item({"title": "Test 2", "url": "https://kdtrading.no/test-2/"}))
        self.assertTrue(is_demo_item({"title": "Now You Can Blog from Everywhere!", "url": "https://www.campsoon.no/post/now-you-can-blog"}))
        self.assertTrue(filler_text("Treating Injuries with Tape. This is a subtitle for your new post. Learn more"))
        from norway_company_agent.footprint import NOT_POST_TYPES
        self.assertTrue(NOT_POST_TYPES.search("/ledig-stilling/project-manager/"))
        self.assertFalse(NOT_POST_TYPES.search("/nyheter/ny-stilling-besatt/"))
        self.assertFalse(is_demo_item({"title": "Test av ny maskin", "url": "https://firma.no/test-av-ny-maskin/"}))
        self.assertTrue(filler_text("in tristique ipsum. Donec mauris ex, dignissim a enim id, interdum scelerisque dolor. Cras pretium"))


class ForeignOwnerTests(unittest.TestCase):
    def test_foreign_copyright_holder(self):
        from norway_company_agent.footprint import foreign_owner

        class P:
            footer_text = ""

        p = P()
        for text, bad in (("Blog Produkter © 2026 IsiCom AB, Goteborg", True), ("© 2026 IsiCom AS", False),
                          ("© 2019-2026 Isicom Ltd.", False), ("IsiCom AB er var leverandor", False)):
            p.text = text
            self.assertEqual(foreign_owner([p], ["isicom"]) is not None, bad, text)


if __name__ == "__main__":
    unittest.main()
