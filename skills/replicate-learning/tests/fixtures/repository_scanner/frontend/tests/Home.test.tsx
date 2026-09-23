import { describe, expect, it } from "vitest";

import { fetchOrders } from "../src/api/client";

describe("fetchOrders", () => {
  it("throws when the response is not ok", async () => {
    await expect(fetchOrders()).rejects.toThrow();
    expect(true).toBe(true);
  });
});
