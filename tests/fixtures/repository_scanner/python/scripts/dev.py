"""Developer helper for the Python fixture repository."""

from sample.service import OrderService


def main() -> None:
    service = OrderService()
    service.create("order-1", 150)
    print(f"total cents: {service.total_cents()}")


if __name__ == "__main__":
    main()
