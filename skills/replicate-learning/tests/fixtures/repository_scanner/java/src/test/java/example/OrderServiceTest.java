package example;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class OrderServiceTest {

    @Test
    void rejectsNegativeAmounts() {
        OrderService service = new OrderService();
        assertThrows(IllegalArgumentException.class, () -> service.create("order-1", -1));
    }

    @Test
    void sumsEveryRecordedOrder() {
        OrderService service = new OrderService();
        service.create("order-1", 150);
        service.create("order-2", 250);
        assertEquals(400, service.totalCents());
    }
}
