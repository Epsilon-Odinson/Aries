import DitherVeil from './DitherVeil.jsx';

export default function App() {
  return (
    <div className="aries-hero">
      <div className="aries-veil">
        <DitherVeil
          src="https://images.unsplash.com/photo-1737071371043-761e02b1ef95?q=80&w=1400&auto=format&fit=crop"
          pattern="floyd"
          pixelSize={2}
          inkColor="#120f17"
          paperColor="#f4f1ea"
          revealRadius={200}
          softness={0.6}
          linger={1}
        />
      </div>
      <div className="aries-overlay">
        <h1>Aries active.</h1>
        <p className="serif">Ready to cook.</p>
        <p className="sub">Move your cursor to reveal the picture</p>
      </div>
    </div>
  );
}
