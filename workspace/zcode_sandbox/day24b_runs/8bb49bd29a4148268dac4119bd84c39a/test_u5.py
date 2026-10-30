"""Unit tests (u5)."""

import unittest


def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b


class TestAdd(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)

    def test_zero(self):
        self.assertEqual(add(0, 0), 0)


class TestDivide(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(divide(10, 2), 5)

    def test_float(self):
        self.assertAlmostEqual(divide(1, 3), 1 / 3)

    def test_divide_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            divide(1, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
