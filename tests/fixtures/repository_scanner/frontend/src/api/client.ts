const BASE_URL = "/api";

export async function fetchOrders(): Promise<string[]> {
  const response = await fetch(`${BASE_URL}/orders`);
  if (!response.ok) {
    throw new Error(`orders request failed: ${response.status}`);
  }
  return (await response.json()) as string[];
}
