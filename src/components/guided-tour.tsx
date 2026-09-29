import { useEffect, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";

export type TourStep = { target: string; text: string };

type Props = {
  steps: TourStep[];
  offset: number; // global index of first step
  total: number;
  onFinish: () => void;
};

type Box = { top: number; left: number; width: number; height: number };

const CARD_W = 280;
const GAP = 16;

export function GuidedTour({ steps, offset, total, onFinish }: Props) {
  const [index, setIndex] = useState(0);
  const [box, setBox] = useState<Box | null>(null);
  const step = steps[index];

  useLayoutEffect(() => {
    if (!step) return;
    let raf = 0;
    const measure = () => {
      const el = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
      if (el) {
        const r = el.getBoundingClientRect();
        setBox((prev) =>
          prev && prev.top === r.top && prev.left === r.left && prev.width === r.width && prev.height === r.height
            ? prev
            : { top: r.top, left: r.left, width: r.width, height: r.height },
        );
      } else setBox(null);
      raf = requestAnimationFrame(measure);
    };
    measure();
    return () => cancelAnimationFrame(raf);
  }, [step]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onFinish();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onFinish]);

  if (!step || !box || typeof document === "undefined") return null;

  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const pad = 6;
  let side: "right" | "left" | "bottom" = "right";
  if (box.left + box.width + GAP + CARD_W > vw - 12) side = box.left - GAP - CARD_W > 12 ? "left" : "bottom";

  const cardStyle: React.CSSProperties = { width: CARD_W };
  if (side === "bottom") {
    cardStyle.top = box.top + box.height + GAP;
    cardStyle.left = Math.min(Math.max(12, box.left + box.width / 2 - CARD_W / 2), vw - CARD_W - 12);
  } else {
    cardStyle.top = Math.min(Math.max(12, box.top + box.height / 2 - 80), vh - 180);
    cardStyle.left = side === "right" ? box.left + box.width + GAP : box.left - GAP - CARD_W;
  }
  const arrowTop = side === "bottom" ? undefined : box.top + box.height / 2 - (cardStyle.top as number);
  const arrowLeft = side === "bottom" ? box.left + box.width / 2 - (cardStyle.left as number) : undefined;

  const isLast = index === steps.length - 1;

  return createPortal(
    <div className="tour-root" role="dialog" aria-modal="true" aria-label={`Tanıtım adım ${offset + index + 1}`}>
      <div
        className="tour-spotlight"
        style={{ top: box.top - pad, left: box.left - pad, width: box.width + pad * 2, height: box.height + pad * 2 }}
      />
      <div key={index} className={`tour-card tour-card-${side}`} style={cardStyle}>
        <span className="tour-arrow" style={{ top: arrowTop, left: arrowLeft }} aria-hidden="true" />
        <p className="tour-step">Adım {offset + index + 1}/{total}</p>
        <p className="tour-text">{step.text}</p>
        <div className="tour-actions">
          <button type="button" className="tour-btn tour-btn-prev" onClick={() => (index === 0 ? onFinish() : setIndex(index - 1))}>
            {index === 0 ? "Geç" : "Önceki"}
          </button>
          <button type="button" className="tour-btn tour-btn-next" onClick={() => (isLast ? onFinish() : setIndex(index + 1))}>
            {isLast ? "Anladım" : "Sonraki"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
