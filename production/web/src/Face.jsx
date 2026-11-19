// Aries visor face — a machine, not a human.
// Angular hexagonal visor, expression by light and motion only:
// no smile, no eyebrows, no human mouth. States: idle, listening,
// thinking, speaking, searching, error.
export default function Face({ state = "idle", size = 320, onClick, label }) {
  const glow =
    state === "listening"
      ? "#35e0ff"
      : state === "error"
        ? "#ff5d5d"
        : "#F2B544";
  const cls = `aries-face state-${state}`;
  return (
    <button
      type="button"
      className={cls}
      style={{ width: size, height: size }}
      onClick={onClick}
      aria-label={label || `Aries face — ${state}. Tap for mini mode.`}
      title="Tap for mini mode"
    >
      <svg viewBox="0 0 200 200" width={size} height={size} aria-hidden="true">
        {/* antenna */}
        <line x1="100" y1="18" x2="100" y2="38" stroke="#F2B544" strokeWidth="4" strokeLinecap="round" />
        <circle cx="100" cy="14" r="6" fill={glow} className="face-antenna" />
        {/* head: angular machine shell */}
        <path
          d="M40 55 L160 55 L178 80 L178 140 L160 168 L40 168 L22 140 L22 80 Z"
          fill="#111114"
          stroke="#605B56"
          strokeWidth="2.5"
        />
        {/* vent slots */}
        <line x1="70" y1="48" x2="90" y2="48" stroke="#3C3B39" strokeWidth="4" strokeLinecap="round" />
        <line x1="110" y1="48" x2="130" y2="48" stroke="#3C3B39" strokeWidth="4" strokeLinecap="round" />
        {/* side plates */}
        <rect x="14" y="96" width="10" height="34" rx="3" fill="#3C3B39" />
        <rect x="176" y="96" width="10" height="34" rx="3" fill="#3C3B39" />
        {/* hexagonal visor */}
        <path
          d="M52 92 L148 92 L162 112 L148 132 L52 132 L38 112 Z"
          fill="#060607"
          stroke={glow}
          strokeWidth="2"
          className="face-visor"
        />
        {/* two slanted eye-slits whose cores shift with the state */}
        <path d="M62 112 L88 104 L88 120 L62 120 Z" fill={glow} className="face-eye" />
        <path d="M112 104 L138 112 L138 120 L112 120 Z" fill={glow} className="face-eye" />
        {/* waveform bars while speaking */}
        {state === "speaking" && (
          <g fill={glow} className="face-wave">
            <rect x="82" y="146" width="5" height="12" rx="2" />
            <rect x="92" y="142" width="5" height="18" rx="2" />
            <rect x="102" y="146" width="5" height="12" rx="2" />
            <rect x="112" y="144" width="5" height="15" rx="2" />
          </g>
        )}
        {/* thinking ring */}
        {state === "thinking" && (
          <circle cx="100" cy="112" r="52" fill="none" stroke={glow} strokeWidth="1.5"
            strokeDasharray="10 14" className="face-ring" />
        )}
        {/* status LED */}
        <circle cx="100" cy="182" r="4" fill={glow} className="face-led" />
      </svg>
    </button>
  );
}
