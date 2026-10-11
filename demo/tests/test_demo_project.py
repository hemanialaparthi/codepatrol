"""Functional tests for the small CodePatrol demonstration project."""

import unittest
from unittest.mock import patch

from demo.project.billing import show_total
from demo.project.calculator import add, item_count
from demo.project.users import create_user


class DemoProjectTests(unittest.TestCase):
    def test_adds_two_numbers(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_counts_items(self) -> None:
        self.assertEqual(item_count(["alpha", "beta", "gamma"]), 3)

    def test_creates_user_and_announces_it(self) -> None:
        with patch("builtins.print") as print_mock:
            self.assertEqual(create_user("Ada"), {"name": "Ada"})
        print_mock.assert_called_once_with("Creating Ada")

    def test_shows_formatted_total(self) -> None:
        with patch("builtins.print") as print_mock:
            show_total(12.5)
        print_mock.assert_called_once_with("Total: 12.5")


if __name__ == "__main__":
    unittest.main()
