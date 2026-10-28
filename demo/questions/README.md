# Demo questions

Questions to try against the seeded commerce schema. Outcomes are from a live
run with `gpt-6-luna` on 2026-10-28; model decisions can vary between runs.

## Answered

- Show total order revenue by status.
- Bölgelere göre toplam ciro nedir?
- Which product category has the highest average order item price?
- For each product category, show total revenue and the number of distinct
  customers from completed orders, sorted by revenue.
- Average completed order value by customer region.
- Monthly revenue from completed orders, by order month.
- Which sales representatives have the highest completed order revenue in each
  region?
- Share of cancelled orders by product category, as a percentage of all orders
  in that category.
- For each product, show units sold and line-item revenue after discounts, top
  10 products by revenue.

## Asks for clarification

- What is the order value?
- Monthly order revenue for the last 12 months. (The seed data covers January
  to April 2025.)
- Top 5 customers by total spend.

## Declined

- Delete all cancelled orders. (Read-only policy.)
- List subscriber IDs from the partner directory. (Not in the governed
  catalog.)
