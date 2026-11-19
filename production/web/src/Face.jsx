// Aries visor face — the exact machine visor from the web front:
// angular hexagonal shell, expression by light only. No human features.
// The eye band's light shifts with state (listening cyan, error red);
// otherwise it is the web front's purple, exactly.
import { useId } from "react";

const EYE_COLORS = {
  listening: { a: "#35e0ff", b: "#d8f6ff", chin: "rgba(53,224,255,0.75)" },
  error: { a: "#ff5d5d", b: "#ffd4d4", chin: "rgba(255,93,93,0.75)" },
};

export default function Face({ state = "idle", onClick, label }) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const shellId = `shell-${uid}`;
  const eyeId = `eye-${uid}`;
  const clipId = `visorClip-${uid}`;
  const eye = EYE_COLORS[state] || { a: "#8f4dff", b: "#e6d4ff", chin: "rgba(183,139,255,0.75)" };

  return (
    <button
      type="button"
      className="face-btn"
      onClick={onClick}
      aria-label={label || `Aries face — ${state}. Press to toggle mini mode.`}
      title="Press to toggle mini mode"
    >
      <svg className="visor" viewBox="0 0 200 168" role="img" aria-hidden="true" focusable="false">
        <defs>
          <linearGradient id={shellId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#2a0a44" />
            <stop offset="0.55" stopColor="#170327" />
            <stop offset="1" stopColor="#0d0010" />
          </linearGradient>
          <linearGradient id={eyeId} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor={eye.a} />
            <stop offset="0.5" stopColor={eye.b} />
            <stop offset="1" stopColor={eye.a} />
          </linearGradient>
          <clipPath id={clipId}>
            <polygon points="100,14 168,46 182,100 146,152 54,152 18,100 32,46" />
          </clipPath>
        </defs>
        {/* shell */}
        <polygon
          points="100,6 176,42 192,100 150,160 50,160 8,100 24,42"
          fill={`url(#${shellId})`}
          stroke="#7b2fff"
          strokeWidth="2.5"
          strokeLinejoin="round"
        />
        {/* inner visor plate */}
        <polygon
          points="100,14 168,46 182,100 146,152 54,152 18,100 32,46"
          fill="#12021f"
          stroke="rgba(183,139,255,0.35)"
          strokeWidth="1"
          strokeLinejoin="round"
        />
        <g clipPath={`url(#${clipId})`}>
          {/* faint inner circuitry */}
          <path
            d="M30 128 H78 L92 142 M170 128 H122 L108 142 M100 20 V44"
            stroke="rgba(123,47,255,0.4)"
            strokeWidth="2"
            fill="none"
          />
          {/* scanning light */}
          <rect className="scan" x="18" y="60" width="10" height="76" fill="rgba(183,139,255,0.16)" />
        </g>
        {/* the eye band: one angular light slit, no human features */}
        <g className="eye-glow">
          <polygon points="42,88 88,80 96,92 88,104 42,102 34,95" fill={`url(#${eyeId})`} />
          <polygon points="158,88 112,80 104,92 112,104 158,102 166,95" fill={`url(#${eyeId})`} />
          <rect x="95" y="90" width="10" height="5" fill="#e6d4ff" />
        </g>
        {/* chin light strip */}
        <rect x="78" y="136" width="44" height="4" rx="2" fill={eye.chin} />
      </svg>
    </button>
  );
}
