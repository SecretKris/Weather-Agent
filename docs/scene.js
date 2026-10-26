/* The hill: a small canvas world that mirrors the forecast at the scrubbed hour.
   - snow thickness grows and melts as you scrub (natural = white, machine = blue-white)
   - flakes fall at the forecast snowfall rate; the lift only runs when the day is skiable
   - sky follows the time of day */
(function () {
  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const rgb = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const noise = (x) => 0.5 + 0.5 * Math.sin(x * 0.031) * Math.sin(x * 0.0173 + 1.3) + 0.0 * x;

  class HillScene {
    constructor(canvas) {
      this.c = canvas;
      this.g = canvas.getContext("2d");
      this.reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.p = { nat: 0, art: 0, rate: 0, temp: 5, gust: 10, hour: 12, running: false };
      this.show = { nat: 0, art: 0 };          // eased values actually drawn
      this.flakes = [];
      this.t0 = performance.now();
      this.last = this.t0;
      this.stars = Array.from({ length: 60 }, (_, i) => ({ x: (i * 97.3) % 1, y: ((i * 53.7) % 1) * 0.42, r: 0.5 + ((i * 31) % 10) / 12 }));
      this.trees = Array.from({ length: 34 }, (_, i) => ({ u: ((i * 0.6180339) % 1), s: 0.7 + ((i * 37) % 10) / 14, d: (i % 3) }));
      this.skiers = [0.1, 0.45, 0.78].map((u, i) => ({ u, v: 0.07 + i * 0.012, ph: i * 2 }));
      this.chairs = Array.from({ length: 12 }, (_, i) => i / 12);
      this.chairU = 0;
      this.resize();
      if ("ResizeObserver" in window) new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
      this.raf = null;
      this.loop = this.loop.bind(this);
      this.start();
      document.addEventListener("visibilitychange", () => (document.hidden ? this.stop() : this.start()));
    }

    set(params) {
      Object.assign(this.p, params);
      if (this.reduced) { this.show.nat = this.p.nat; this.show.art = this.p.art; this.draw(0); }
    }
    start() { if (!this.raf && !this.reduced) { this.last = performance.now(); this.raf = requestAnimationFrame(this.loop); } }
    stop() { if (this.raf) cancelAnimationFrame(this.raf); this.raf = null; }

    resize() {
      const r = this.c.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.W = Math.max(300, r.width);
      this.H = Math.max(200, r.height);
      this.c.width = Math.round(this.W * dpr);
      this.c.height = Math.round(this.H * dpr);
      this.g.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.draw(0);
    }

    // ground surface of the main slope (top-left high, bottom-right low)
    surf(x) {
      const { W, H } = this;
      const t = x / W;
      return H * (0.47 + 0.30 * t - 0.05 * Math.sin(t * 3.1) ) + 3 * Math.sin(x * 0.02) + 2 * Math.sin(x * 0.057);
    }

    loop(now) {
      const dt = Math.min(0.05, (now - this.last) / 1000);
      this.last = now;
      const k = 1 - Math.pow(0.0008, dt);        // ease so the snow visibly builds / melts
      this.show.nat += (this.p.nat - this.show.nat) * k;
      this.show.art += (this.p.art - this.show.art) * k;
      this.draw(dt);
      this.raf = requestAnimationFrame(this.loop);
    }

    draw(dt) {
      const g = this.g, { W, H } = this, p = this.p;
      const time = (performance.now() - this.t0) / 1000;
      const h = p.hour;
      const day = clamp(Math.sin(Math.PI * (h - 7.3) / 10.4) * 1.6);         // 0 night .. 1 day (short October days)
      const snowing = clamp(p.rate * 6);
      const cold = clamp((3 - p.temp) / 8);

      // sky
      const top = mix(mix(hex("#071520"), hex("#4f8fb3"), day), hex("#56707f"), snowing * 0.55);
      const bot = mix(mix(hex("#12303f"), hex("#cfe4ee"), day), hex("#aebfc8"), snowing * 0.6);
      const sky = g.createLinearGradient(0, 0, 0, H * 0.7);
      sky.addColorStop(0, rgb(top)); sky.addColorStop(1, rgb(bot));
      g.fillStyle = sky; g.fillRect(0, 0, W, H);

      // stars
      if (day < 0.6 && snowing < 0.4) {
        g.fillStyle = `rgba(255,255,255,${(0.6 - day) * (1 - snowing) * 0.9})`;
        for (const s of this.stars) { g.beginPath(); g.arc(s.x * W, s.y * H, s.r, 0, 7); g.fill(); }
      }
      // sun / moon
      const sunT = clamp((h - 7.3) / 10.4), moonT = ((h + 6.5) % 24) / 12.5;
      const orb = day > 0.15 ? { x: W * (0.12 + 0.76 * sunT), y: H * (0.36 - 0.26 * Math.sin(Math.PI * sunT)), c: "#fff4d6", r: 15 } : { x: W * (0.15 + 0.7 * clamp(moonT)), y: H * (0.26 - 0.14 * Math.sin(Math.PI * clamp(moonT))), c: "#dce9f2", r: 11 };
      g.globalAlpha = 1 - snowing * 0.8; g.fillStyle = orb.c; g.beginPath(); g.arc(orb.x, orb.y, orb.r, 0, 7); g.fill(); g.globalAlpha = 1;

      // ridges
      const far = mix(mix(hex("#12313f"), hex("#6f98af"), day), hex("#7d909b"), snowing * 0.4);
      const mid = mix(mix(hex("#0e2733"), hex("#4b7389"), day), hex("#5c6f7a"), snowing * 0.4);
      for (const [col, base, amp, f, ph] of [[far, 0.46, 0.05, 0.006, 0.4], [mid, 0.53, 0.06, 0.009, 2.2]]) {
        g.fillStyle = rgb(col); g.beginPath(); g.moveTo(0, H);
        for (let x = 0; x <= W; x += 6) g.lineTo(x, H * (base + amp * Math.sin(x * f + ph) + amp * 0.5 * Math.sin(x * f * 2.3 + ph * 1.7)));
        g.lineTo(W, H); g.closePath(); g.fill();
        // thin snow on the ridges once it is cold enough
        if (this.show.nat > 1) { g.strokeStyle = rgb(mix(col, [255, 255, 255], 0.5), 0.5); g.lineWidth = 2; g.stroke(); }
      }

      // front hill (bare ground)
      const grass = mix(mix(hex("#1a2d28"), hex("#4a6446"), day), hex("#5b6b66"), cold * 0.6);
      g.fillStyle = rgb(grass); g.beginPath(); g.moveTo(0, H);
      for (let x = 0; x <= W; x += 4) g.lineTo(x, this.surf(x));
      g.lineTo(W, H); g.closePath(); g.fill();

      // snow layers (machine snow underneath, natural on top)
      const pxPer = H * 0.0016;                                   // px per cm
      const thickArt = (x) => clamp(this.show.art, 0, 60) * pxPer * (0.8 + 0.4 * noise(x));
      const thickNat = (x) => clamp(this.show.nat, 0, 60) * pxPer * (0.8 + 0.4 * noise(x + 400));
      const patchy = (cm, x, off) => (cm >= 6 ? 1 : cm <= 0.2 ? 0 : (noise(x * 1.7 + off) < cm / 6 ? 1 : 0));
      const light = 0.55 + 0.45 * day;
      const artCol = mix(hex("#a9c6d6"), [255, 255, 255], 0), natCol = [246, 251, 253];
      const layer = (thick, cm, col, baseFn, off, sitsOnArt) => {
        if (cm < 0.2) return;
        g.fillStyle = rgb(mix([40, 60, 75], col, light));
        g.beginPath();
        let started = false;
        const full = cm >= 6;
        for (let x = 0; x <= W; x += 3) {
          const on = patchy(cm, x, off);
          const y1 = this.surf(x) - baseFn(x) - (on ? Math.max(thick(x), 1.5) : 0);
          if (!started) { g.moveTo(x, y1); started = true; } else g.lineTo(x, y1);
        }
        for (let x = W; x >= 0; x -= 3) {
          const top = this.surf(x) - baseFn(x);
          // natural snow resting on machine snow is a cap; otherwise snow fills the hillside (or lies in patches when thin)
          const bottom = sitsOnArt ? top + 3 : full ? H : patchy(cm, x, off) ? top + 24 : top;
          g.lineTo(x, bottom);
        }
        g.closePath(); g.fill();
      };
      const hasArt = this.show.art > 0.2;
      layer(thickArt, this.show.art, artCol, () => 0, 11, false);
      layer(thickNat, this.show.nat, natCol, thickArt, 23, hasArt);

      // trees
      for (const t of this.trees) {
        const x = t.u * W, y = this.surf(x) - (x > W * 0.3 && x < W * 0.78 ? 9999 : 0);
        if (y < 0) continue;                                       // keep the slope itself clear
        const s = (H / 330) * 26 * t.s, sway = Math.sin(time * 1.3 + t.u * 20) * clamp(p.gust / 60) * 2.2;
        const base = this.surf(x) - clamp(this.show.nat + this.show.art, 0, 40) * pxPer * 0.6 + 2;
        const col = mix(mix(hex("#0f2a24"), hex("#27493c"), day), hex("#30463f"), snowing * 0.3);
        g.fillStyle = rgb(col);
        for (let k = 0; k < 3; k++) {
          const w = s * (0.95 - k * 0.22), y1 = base - s * (0.45 + k * 0.52), y0 = base - s * (k * 0.52 - 0.1);
          g.beginPath(); g.moveTo(x - w * 0.5, y0); g.lineTo(x + sway * (k + 1) / 3, y1 - s * 0.5); g.lineTo(x + w * 0.5, y0); g.closePath(); g.fill();
        }
        if (this.show.nat > 2) {                                    // snow on branches
          g.fillStyle = rgb(mix(col, [250, 253, 255], clamp(this.show.nat / 12)));
          g.beginPath(); g.moveTo(x - s * 0.18, base - s * 1.2); g.lineTo(x + sway, base - s * 1.55); g.lineTo(x + s * 0.18, base - s * 1.2); g.closePath(); g.fill();
        }
      }

      // lift: towers, cable, chairs (runs only when the day is skiable)
      const x0 = W * 0.10, x1 = W * 0.90, lift = (u) => { const x = x1 + (x0 - x1) * u; return [x, this.surf(x) - H * 0.17 - 4]; };
      g.strokeStyle = rgb(mix(hex("#0c1a22"), hex("#38505e"), day)); g.lineWidth = 1.6;
      g.beginPath(); for (let i = 0; i <= 24; i++) { const [x, y] = lift(i / 24); i ? g.lineTo(x, y) : g.moveTo(x, y); } g.stroke();
      for (let i = 0; i <= 5; i++) { const [x, y] = lift(i / 5); g.lineWidth = 3; g.beginPath(); g.moveTo(x, y); g.lineTo(x, this.surf(x) + 2); g.stroke(); }
      if (p.running && !this.reduced) this.chairU = (this.chairU + dt * 0.035) % 1;
      for (const c of this.chairs) {
        const u = (c + this.chairU) % 1, [x, y] = lift(u);
        g.fillStyle = p.running ? "#ff9a4d" : "#4d6676";
        g.fillRect(x - 3, y + 4, 7, 5); g.fillRect(x - 0.5, y, 1.2, 4);
      }

      // skiers on the slope (only when skiable)
      if (p.running) {
        for (const s of this.skiers) {
          if (!this.reduced) s.u = (s.u + dt * s.v) % 1;
          const u = s.u, x = W * (0.16 + 0.68 * u) + Math.sin(u * 22 + s.ph) * 16;
          const y = this.surf(x) - (this.show.nat + this.show.art > 1 ? clamp(this.show.nat + this.show.art, 0, 60) * pxPer * 0.8 : 0);
          g.strokeStyle = "#fff"; g.lineWidth = 2; g.beginPath(); g.moveTo(x - 6, y); g.lineTo(x + 6, y - 2); g.stroke();
          g.fillStyle = "#ff9a4d"; g.fillRect(x - 2, y - 11, 4, 9);
          g.fillStyle = "#e8f1f6"; g.beginPath(); g.arc(x, y - 14, 2.4, 0, 7); g.fill();
        }
      }

      // snowfall
      const want = this.reduced ? 0 : Math.round(clamp(p.rate / 1.2) * 520 + (p.rate > 0 ? 25 : 0));
      while (this.flakes.length < want) this.flakes.push({ x: Math.random() * W, y: Math.random() * H, z: 0.4 + Math.random() * 0.9, ph: Math.random() * 6 });
      if (this.flakes.length > want) this.flakes.length = want;
      g.fillStyle = "rgba(255,255,255,.9)";
      const drift = (p.gust / 25) * 14;
      for (const f of this.flakes) {
        f.y += (28 + 38 * f.z) * dt; f.x += (Math.sin(time * 0.9 + f.ph) * 8 + drift) * dt * f.z;
        if (f.y > H) { f.y = -4; f.x = Math.random() * W; }
        if (f.x > W) f.x = 0;
        g.globalAlpha = 0.45 + 0.5 * f.z; g.beginPath(); g.arc(f.x, f.y, 0.9 + f.z * 1.3, 0, 7); g.fill();
      }
      g.globalAlpha = 1;
    }
  }
  window.HillScene = HillScene;
})();
