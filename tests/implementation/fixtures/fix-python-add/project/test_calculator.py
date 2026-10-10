import unittest
from calculator import add


class AdditionTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)

    def test_mixed(self):
        self.assertEqual(add(-4, 7), 3)


if __name__ == '__main__':
    unittest.main()
