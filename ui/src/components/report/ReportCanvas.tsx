import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { flushSync } from "react-dom";
import type { CardLayout, ReportCard } from "../../types";
import { COLUMNS, clamp, place } from "./layout";

const ROW = 36;
const GAP = 12;
// About the printable width of a landscape page, in CSS pixels; wider layouts are scaled down.
const PRINT_WIDTH = 960;

/** Cards grouped into horizontal bands no card crosses, so a page can break between bands. */
function bands(cards: ReportCard[]): { top: number; bottom: number; cards: ReportCard[] }[] {
  const result: { top: number; bottom: number; cards: ReportCard[] }[] = [];
  for (const card of [...cards].sort((a, b) => a.layout.y - b.layout.y)) {
    const last = result[result.length - 1];
    if (last && card.layout.y < last.bottom) {
      last.cards.push(card);
      last.bottom = Math.max(last.bottom, card.layout.y + card.layout.h);
    } else {
      result.push({ top: card.layout.y, bottom: card.layout.y + card.layout.h, cards: [card] });
    }
  }
  return result;
}

interface Drag {
  id: string;
  mode: "move" | "resize";
  startX: number;
  startY: number;
  origin: CardLayout;
  dx: number;
  dy: number;
}

/**
 * A twelve-column grid. In edit mode a card moves by its handle and resizes by its
 * corner; arrow keys on the handle do the same (Shift resizes).
 */
export function ReportCanvas({
  cards,
  editing,
  onChange,
  renderCard,
}: {
  cards: ReportCard[];
  editing: boolean;
  onChange: (cards: ReportCard[]) => void;
  renderCard: (card: ReportCard, handle: ReactNode) => ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  // The grid renders one of three roots (canvas, stack, print); observe whichever is mounted.
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const attach = useCallback((element: HTMLDivElement | null) => {
    ref.current = element;
    setNode(element);
  }, []);
  const [width, setWidth] = useState(0);
  const [drag, setDrag] = useState<Drag | null>(null);
  // While printing, the screen width is kept, so charts keep the size they were drawn at.
  const [printWidth, setPrintWidth] = useState<number | null>(null);

  useEffect(() => {
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);

  useEffect(() => {
    // Synchronous, so the print layout is in place before the browser lays out the page.
    const before = () => flushSync(() => setPrintWidth(ref.current?.clientWidth || null));
    const after = () => setPrintWidth(null);
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);

  const used = printWidth ?? width;
  // Narrow screens stack cards in one column; their layout is kept for wider ones.
  const stacked = used > 0 && used < 640;
  const column = (used - GAP * (COLUMNS - 1)) / COLUMNS;
  const toPx = (layout: CardLayout) => ({
    left: layout.x * (column + GAP),
    top: layout.y * (ROW + GAP),
    width: layout.w * column + (layout.w - 1) * GAP,
    height: layout.h * ROW + (layout.h - 1) * GAP,
  });

  const target = (state: Drag): CardLayout => {
    const cols = state.dx / (column + GAP);
    const rows = state.dy / (ROW + GAP);
    return clamp(
      state.mode === "move"
        ? { ...state.origin, x: state.origin.x + cols, y: state.origin.y + rows }
        : { ...state.origin, w: state.origin.w + cols, h: state.origin.h + rows },
    );
  };

  const preview = drag ? place(cards, drag.id, target(drag)) : cards;
  // Position by transform so settling cards animate without layout work.
  const style = (box: ReturnType<typeof toPx>) => ({
    transform: `translate(${box.left}px, ${box.top}px)`,
    width: box.width,
    height: box.height,
  });

  const start = (event: PointerEvent, card: ReportCard, mode: Drag["mode"]) => {
    if (!editing || event.button !== 0) return;
    event.preventDefault();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    setDrag({ id: card.id, mode, startX: event.clientX, startY: event.clientY, origin: card.layout, dx: 0, dy: 0 });
  };
  const move = (event: PointerEvent) => {
    if (!drag) return;
    setDrag({ ...drag, dx: event.clientX - drag.startX, dy: event.clientY - drag.startY });
  };
  const end = () => {
    if (!drag) return;
    const changed = preview.some((card, index) => JSON.stringify(card.layout) !== JSON.stringify(cards[index].layout));
    setDrag(null);
    if (changed) onChange(preview);
  };

  const nudge = (event: KeyboardEvent, card: ReportCard) => {
    const step: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    const delta = step[event.key];
    if (!delta) return;
    event.preventDefault();
    const [dx, dy] = delta;
    const layout = event.shiftKey
      ? { ...card.layout, w: card.layout.w + dx, h: card.layout.h + dy }
      : { ...card.layout, x: card.layout.x + dx, y: card.layout.y + dy };
    onChange(place(cards, card.id, layout));
  };

  if (printWidth && !stacked) {
    return (
      <div ref={attach} className="rc-root">
        <div className="rc-print" style={{ width: printWidth, zoom: Math.min(1, PRINT_WIDTH / printWidth) }}>
          {bands(cards).map((band) => (
            <div key={band.top} className="rc-band" style={{ height: (band.bottom - band.top) * (ROW + GAP) - GAP }}>
              {band.cards.map((card) => (
                <div key={card.id} className="rc-cell" style={style(toPx({ ...card.layout, y: card.layout.y - band.top }))}>
                  {renderCard(card, null)}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (stacked) {
    return (
      <div ref={attach} className="rc-root rc-stack">
        {[...cards]
          .sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x)
          .map((card) => (
            <div key={card.id} className="rc-cell" style={{ height: (card.kind === "heading" ? card.layout.h : Math.max(4, card.layout.h)) * (ROW + GAP) - GAP }}>
              {renderCard(card, null)}
            </div>
          ))}
      </div>
    );
  }

  const height = preview.reduce((max, card) => Math.max(max, card.layout.y + card.layout.h), 0);
  const dragged = drag ? cards.find((card) => card.id === drag.id) : undefined;

  return (
    <div
      ref={attach}
      className={editing ? "rc-root rc-canvas editing" : "rc-root rc-canvas"}
      style={{ height: Math.max(height + (editing ? 4 : 0), 6) * (ROW + GAP) }}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={end}
    >
      {editing && width > 0 && (
        <div className="rc-columns" aria-hidden="true">
          {Array.from({ length: COLUMNS }, (_, index) => <span key={index} style={{ width: column }} />)}
        </div>
      )}
      {width > 0 &&
        preview.map((card) => {
          const isDragged = drag?.id === card.id && dragged;
          const settled = toPx(card.layout);
          let box = settled;
          if (isDragged) {
            const origin = toPx(dragged.layout);
            box =
              drag.mode === "move"
                ? { ...origin, left: origin.left + drag.dx, top: origin.top + drag.dy }
                : { ...origin, width: Math.max(column, origin.width + drag.dx), height: Math.max(ROW, origin.height + drag.dy) };
          }
          const handle = editing ? (
            <button
              type="button"
              className="rc-handle"
              aria-label={`Move ${card.title || "card"}. Arrow keys move it, Shift with arrows resizes it.`}
              onPointerDown={(event) => start(event, card, "move")}
              onKeyDown={(event) => nudge(event, card)}
            >
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                {[2, 6, 10].flatMap((cy) => [4, 8].map((cx) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.1" fill="currentColor" />))}
              </svg>
            </button>
          ) : null;
          return (
            <div key={card.id}>
              {isDragged && <div className="rc-placeholder" style={style(settled)} />}
              <div className={isDragged ? "rc-cell dragging" : "rc-cell"} style={style(box)}>
                {renderCard(card, handle)}
                {editing && (
                  <span
                    className="rc-resize"
                    role="presentation"
                    onPointerDown={(event) => start(event, card, "resize")}
                  />
                )}
              </div>
            </div>
          );
        })}
    </div>
  );
}
