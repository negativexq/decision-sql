import type { CardLayout, ReportCard } from "../../types";

export const COLUMNS = 12;

function overlaps(a: CardLayout, b: CardLayout): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

export function clamp(layout: CardLayout): CardLayout {
  const w = Math.min(COLUMNS, Math.max(1, Math.round(layout.w)));
  const h = Math.min(40, Math.max(2, Math.round(layout.h)));
  return {
    w,
    h,
    x: Math.min(COLUMNS - w, Math.max(0, Math.round(layout.x))),
    y: Math.max(0, Math.round(layout.y)),
  };
}

/**
 * Put one card at a new place and settle the rest around it: cards it covers move
 * down, then every card floats up as far as it can, so the page has no holes.
 */
export function place(cards: ReportCard[], id: string, target: CardLayout): ReportCard[] {
  const moved = clamp(target);
  const order = [...cards].sort((a, b) => {
    // The moved card keeps the place it was dropped on; others settle around it.
    if (a.id === id) return -1;
    if (b.id === id) return 1;
    return a.layout.y - b.layout.y || a.layout.x - b.layout.x;
  });
  const placed: ReportCard[] = [];
  for (const card of order) {
    let layout = card.id === id ? moved : { ...card.layout };
    if (card.id !== id) {
      let collision = placed.find((other) => overlaps(other.layout, layout));
      while (collision) {
        layout = { ...layout, y: collision.layout.y + collision.layout.h };
        collision = placed.find((other) => overlaps(other.layout, layout));
      }
    }
    placed.push({ ...card, layout });
  }
  const settled = compact(placed, id);
  return cards.map((card) => settled.find((item) => item.id === card.id) ?? card);
}

export function compact(cards: ReportCard[], pinned?: string): ReportCard[] {
  const order = [...cards].sort(
    (a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x || (a.id === pinned ? -1 : b.id === pinned ? 1 : 0),
  );
  const settled: ReportCard[] = [];
  for (const card of order) {
    let layout = { ...card.layout };
    while (layout.y > 0 && !settled.some((other) => overlaps(other.layout, { ...layout, y: layout.y - 1 }))) {
      layout = { ...layout, y: layout.y - 1 };
    }
    settled.push({ ...card, layout });
  }
  // Keep the caller's order so React keys and saved order stay stable.
  return cards.map((card) => settled.find((item) => item.id === card.id) ?? card);
}

/** The first row below every card, where a new card goes. */
export function bottom(cards: ReportCard[]): number {
  return cards.reduce((max, card) => Math.max(max, card.layout.y + card.layout.h), 0);
}
