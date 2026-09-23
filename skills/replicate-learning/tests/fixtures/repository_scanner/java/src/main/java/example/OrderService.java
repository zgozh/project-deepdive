package example;

import java.util.LinkedHashMap;
import java.util.Map;

/** Small in-memory order service used by the Phase 2 scanner fixture. */
public class OrderService {

    private final Map<String, Integer> orders = new LinkedHashMap<>();

    /**
     * Record one order.
     *
     * @param orderId identifier of the order
     * @param amountCents amount in cents; must not be negative
     */
    public void create(String orderId, int amountCents) {
        if (amountCents < 0) {
            throw new IllegalArgumentException("amountCents must not be negative");
        }
        orders.put(orderId, amountCents);
    }

    /** Sum every recorded order amount. */
    public int totalCents() {
        int total = 0;
        for (int amount : orders.values()) {
            total += amount;
        }
        return total;
    }
}
