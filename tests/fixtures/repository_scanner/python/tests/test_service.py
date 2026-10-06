"""Behaviour tests for the fixture order service."""

import unittest

from sample.service import OrderService


class OrderServiceTests(unittest.TestCase):
    def test_create_rejects_a_negative_amount(self):
        with self.assertRaises(ValueError):
            OrderService().create("order-1", -1)

    def test_total_cents_sums_every_created_order(self):
        service = OrderService()
        service.create("order-1", 150)
        service.create("order-2", 250)

        self.assertEqual(400, service.total_cents())


if __name__ == "__main__":
    unittest.main()
