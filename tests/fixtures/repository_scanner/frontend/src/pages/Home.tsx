import { useEffect, useState } from "react";

import { fetchOrders } from "../api/client";
import { Button } from "../components/Button";

export function Home() {
  const [orders, setOrders] = useState<string[]>([]);

  useEffect(() => {
    fetchOrders().then(setOrders);
  }, []);

  return (
    <main>
      <h1>Orders</h1>
      <Button label="Refresh" onClick={() => fetchOrders().then(setOrders)} />
      <ul>
        {orders.map((order) => (
          <li key={order}>{order}</li>
        ))}
      </ul>
    </main>
  );
}
