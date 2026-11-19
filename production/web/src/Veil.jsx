// Aries veil — the web front's 2D approach, ported faithfully to React.
// A baked grain <img> is the background layer (plain HTML, on screen
// from the first paint). A transparent 2D <canvas> overlay ONLY paints
// the full-colour reveal (a radial-masked copy of the artwork following
// the pointer, with a soft purple rim) and click bursts. The canvas is
// cleared every frame and never fills the screen with an opaque
// background, so it can never paint a solid block.
import { useEffect, useRef } from "react";
import { ARTWORK, GRAIN_COARSE, GRAIN_FINE } from "./veilImages.js";

export default function Veil({ grain, burstSignal }) {
  const canvasRef = useRef(null);
  const apiRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext("2d");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // No canvas, no veil: the grain is a plain image layer and stays
    // up regardless. The canvas below only adds the colour reveal.
    if (!ctx) {
      canvas.style.display = "none";
      return undefined;
    }

    let W = 0;
    let H = 0;
    let DPR = 1;
    let revealRadius = 190; // CSS px, scaled on small screens
    const img = new Image();
    let imgReady = false;

    const full = document.createElement("canvas"); // original artwork, cover-fit
    const revealBuf = document.createElement("canvas"); // masked original for the reveal

    // Pointer state. Until the visitor moves, the reveal drifts on its
    // own so the effect is visible at a glance.
    const pointer = { x: 0, y: 0, tx: 0, ty: 0, seen: false, inside: false };
    let bursts = []; // {x, y, start}
    const startTime = performance.now();
    let raf = 0;

    function coverDraw(target, tw, th) {
      const tctx = target.getContext("2d");
      const iw = img.naturalWidth;
      const ih = img.naturalHeight;
      const scale = Math.max(tw / iw, th / ih);
      const dw = iw * scale;
      const dh = ih * scale;
      tctx.clearRect(0, 0, tw, th);
      tctx.drawImage(img, (tw - dw) / 2, (th - dh) / 2, dw, dh);
    }

    function buildLayers() {
      if (!imgReady) return;
      // Only the full-colour layer is built here, purely for the
      // reveal. The grain the visitor actually sees never passes
      // through a canvas at all — it is the pre-rendered image layer.
      full.width = W;
      full.height = H;
      coverDraw(full, W, H);
      revealBuf.width = W;
      revealBuf.height = H;
    }

    function resize() {
      DPR = Math.min(window.devicePixelRatio || 1, 1.5);
      W = Math.round(window.innerWidth * DPR);
      H = Math.round(window.innerHeight * DPR);
      canvas.width = W;
      canvas.height = H;
      revealRadius = Math.max(120, Math.min(210, window.innerWidth * 0.32)) * DPR;
      if (!pointer.seen) {
        pointer.x = pointer.tx = W * 0.5;
        pointer.y = pointer.ty = H * 0.44;
      }
      buildLayers();
    }

    function drawMaskedReveal(cx, cy, radius, alpha) {
      if (radius <= 0 || alpha <= 0) return;
      const rctx = revealBuf.getContext("2d");
      rctx.save();
      rctx.clearRect(0, 0, W, H);
      rctx.globalCompositeOperation = "source-over";
      rctx.drawImage(full, 0, 0);
      rctx.globalCompositeOperation = "destination-in";
      const grad = rctx.createRadialGradient(cx, cy, radius * 0.55, cx, cy, radius);
      grad.addColorStop(0, "rgba(0,0,0,1)");
      grad.addColorStop(1, "rgba(0,0,0,0)");
      rctx.fillStyle = grad;
      rctx.beginPath();
      rctx.arc(cx, cy, radius, 0, Math.PI * 2);
      rctx.fill();
      rctx.restore();

      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.drawImage(revealBuf, 0, 0);
      ctx.restore();

      // Soft lit rim where the veil lifts.
      ctx.save();
      ctx.globalAlpha = 0.35 * alpha;
      ctx.strokeStyle = "#b78bff";
      ctx.lineWidth = Math.max(1, DPR);
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 0.97, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    function frame(now) {
      // The canvas is a transparent overlay: clear it and paint only
      // the reveal. It never lays down an opaque background, so the
      // grain image layer underneath always shows through.
      ctx.clearRect(0, 0, W, H);
      if (!imgReady) {
        raf = requestAnimationFrame(frame);
        return;
      }
      const t = (now - startTime) / 1000;

      if (!pointer.seen && !reduced) {
        // Idle drift: a slow lissajous so the reveal breathes on its own.
        pointer.tx = W * (0.5 + 0.26 * Math.sin(t * 0.32));
        pointer.ty = H * (0.46 + 0.2 * Math.sin(t * 0.47 + 1.3));
      }
      pointer.x += (pointer.tx - pointer.x) * 0.12;
      pointer.y += (pointer.ty - pointer.y) * 0.12;

      if (pointer.inside || !pointer.seen) {
        drawMaskedReveal(pointer.x, pointer.y, revealRadius, 1);
      }

      // Click bursts: an expanding ring of full colour that fades out.
      const alive = [];
      for (let i = 0; i < bursts.length; i++) {
        const b = bursts[i];
        const p = (now - b.start) / 750;
        if (p < 1) {
          alive.push(b);
          const ease = 1 - Math.pow(1 - p, 3);
          const br = 60 + ease * Math.max(W, H) * 0.55;
          drawMaskedReveal(b.x, b.y, br, reduced ? 0 : (1 - p) * 0.95);
        }
      }
      bursts = alive;

      raf = requestAnimationFrame(frame);
    }

    function toCanvas(e) {
      const rect = canvas.getBoundingClientRect();
      return {
        x: (e.clientX - rect.left) * DPR,
        y: (e.clientY - rect.top) * DPR,
      };
    }

    function onMove(e) {
      const p = toCanvas(e);
      pointer.tx = p.x;
      pointer.ty = p.y;
      pointer.seen = true;
      pointer.inside = true;
    }

    // Bursts only fire on the veil itself. The dock, the face button and
    // the composer sit above the canvas, so tapping Send or typing never
    // sets one off — the web front listened on the whole window, but a
    // working chat app can't burst on every tap.
    function onDown(e) {
      const p = toCanvas(e);
      pointer.tx = p.x;
      pointer.ty = p.y;
      pointer.seen = true;
      pointer.inside = true;
      bursts.push({ x: p.x, y: p.y, start: performance.now() });
    }

    function onLeave() {
      pointer.inside = false;
    }

    function burstFromCentre() {
      bursts.push({ x: W * 0.5, y: H * 0.44, start: performance.now() });
    }
    apiRef.current = { burstFromCentre };

    window.addEventListener("pointermove", onMove, { passive: true });
    canvas.addEventListener("pointerdown", onDown, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    window.addEventListener("blur", onLeave);

    // ----- Boot -----
    // This load only feeds the colour reveal. If it fails, the canvas
    // hides itself and the pre-rendered grain layer carries the page.
    img.onload = () => {
      imgReady = true;
      buildLayers();
    };
    img.onerror = () => {
      canvas.style.display = "none";
    };
    img.src = ARTWORK;

    let resizeTimer = null;
    function onResize() {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(resize, 120);
    }
    window.addEventListener("resize", onResize);

    resize();
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(resizeTimer);
      window.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerdown", onDown);
      document.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("blur", onLeave);
      window.removeEventListener("resize", onResize);
      img.onload = null;
      img.onerror = null;
      apiRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (burstSignal > 0) apiRef.current?.burstFromCentre();
  }, [burstSignal]);

  return (
    <>
      <img
        id="veilBase"
        src={grain === "fine" ? GRAIN_FINE : GRAIN_COARSE}
        alt=""
      />
      <canvas
        ref={canvasRef}
        id="veil"
        aria-label="A purple nebula landscape hidden under a dithered veil. Move your pointer or finger across it to reveal the full colour underneath."
      />
      <div className="vignette" aria-hidden="true" />
    </>
  );
}
