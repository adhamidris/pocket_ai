/**
 * DottedGlowBackground (Vanilla JS port)
 * Based on Aceternity UI Dotted Glow Background
 */
class DottedGlowBackground {
  constructor(container, options = {}) {
    this.container = container;
    this.canvas = document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d');
    
    // Default options
    this.options = {
      gap: 12,
      radius: 2,
      color: "rgba(0,0,0,0.7)",
      darkColor: null, // will fall back to color or CSS var
      glowColor: "rgba(0, 170, 255, 0.85)",
      darkGlowColor: null,
      colorLightVar: "--color-neutral-500", // Fallback if not using Tailwind v4 vars
      colorDarkVar: "--color-neutral-500",
      glowColorLightVar: "--color-neutral-600", 
      glowColorDarkVar: "--color-sky-800",
      opacity: 0.6,
      backgroundOpacity: 0,
      speedMin: 0.4,
      speedMax: 1.3,
      speedScale: 1,
      ...options
    };

    this.dots = [];
    this.raf = null;
    this.stopped = false;
    
    // Style the canvas
    this.canvas.style.display = 'block';
    this.canvas.style.position = 'absolute';
    this.canvas.style.inset = '0';
    this.canvas.style.width = '100%';
    this.canvas.style.height = '100%';
    this.canvas.style.pointerEvents = 'none'; // Ensure clicks pass through
    this.canvas.style.zIndex = '0'; // Behind content but check parent context
    
    // Check if container is positioned
    const compStyle = window.getComputedStyle(this.container);
    if (compStyle.position === 'static') {
      this.container.style.position = 'relative';
    }
    
    // Ensure content is above canvas
    // We assume the container has children that should be on top.
    // If not, the caller should handle z-indices.
    
    this.container.appendChild(this.canvas);
    
    this.init();
  }

  resolveCssVariable(variableName) {
    if (!variableName) return null;
    const normalized = variableName.startsWith("--") ? variableName : `--${variableName}`;
    const value = getComputedStyle(document.documentElement).getPropertyValue(normalized).trim();
    return value || null;
  }

  detectDarkMode() {
    return document.documentElement.classList.contains('dark');
  }

  computeColors() {
    const isDark = this.detectDarkMode();
    let nextColor = this.options.color;
    let nextGlow = this.options.glowColor;

    if (isDark) {
      const varDot = this.resolveCssVariable(this.options.colorDarkVar);
      const varGlow = this.resolveCssVariable(this.options.glowColorDarkVar);
      nextColor = varDot || this.options.darkColor || nextColor;
      nextGlow = varGlow || this.options.darkGlowColor || nextGlow;
    } else {
      const varDot = this.resolveCssVariable(this.options.colorLightVar);
      const varGlow = this.resolveCssVariable(this.options.glowColorLightVar);
      nextColor = varDot || nextColor;
      nextGlow = varGlow || nextGlow;
    }

    this.resolvedColor = nextColor;
    this.resolvedGlowColor = nextGlow;
  }

  init() {
    this.computeColors();
    
    // Observe theme changes
    this.observer = new MutationObserver(() => this.computeColors());
    this.observer.observe(document.documentElement, {
      attributes: true, 
      attributeFilter: ['class']
    });

    // Resize observer
    this.resizeObserver = new ResizeObserver(() => {
      this.resize();
      this.regenDots();
    });
    this.resizeObserver.observe(this.container);
    
    this.resize();
    this.regenDots();
    this.lastTime = performance.now();
    this.loop();
  }

  resize() {
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const rect = this.container.getBoundingClientRect();
    this.canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    this.canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  regenDots() {
    this.dots = [];
    const rect = this.container.getBoundingClientRect();
    const width = rect.width;
    const height = rect.height;
    const { gap, speedMin, speedMax } = this.options;
    
    const cols = Math.ceil(width / gap) + 2;
    const rows = Math.ceil(height / gap) + 2;
    const min = Math.min(speedMin, speedMax);
    const max = Math.max(speedMin, speedMax);

    for (let i = -1; i < cols; i++) {
      for (let j = -1; j < rows; j++) {
        const x = i * gap + (j % 2 === 0 ? 0 : gap * 0.5);
        const y = j * gap;
        const phase = Math.random() * Math.PI * 2;
        const span = Math.max(max - min, 0);
        const speed = min + Math.random() * span;
        this.dots.push({ x, y, phase, speed });
      }
    }
  }

  draw(now) {
    if (this.stopped) return;
    
    const dt = (now - this.lastTime) / 1000;
    this.lastTime = now;
    
    const rect = this.container.getBoundingClientRect();
    const width = rect.width;
    const height = rect.height;

    this.ctx.clearRect(0, 0, width, height);
    this.ctx.globalAlpha = this.options.opacity;

    // Background fade (optional)
    if (this.options.backgroundOpacity > 0) {
      const grad = this.ctx.createRadialGradient(
        width * 0.5, height * 0.4, Math.min(width, height) * 0.1,
        width * 0.5, height * 0.5, Math.max(width, height) * 0.7
      );
      grad.addColorStop(0, "rgba(0,0,0,0)");
      grad.addColorStop(1, `rgba(0,0,0,${Math.min(Math.max(this.options.backgroundOpacity, 0), 1)})`);
      this.ctx.fillStyle = grad;
      this.ctx.fillRect(0, 0, width, height);
    }

    this.ctx.save();
    this.ctx.fillStyle = this.resolvedColor;

    const time = (now / 1000) * Math.max(this.options.speedScale, 0);
    
    for (const d of this.dots) {
      const mod = (time * d.speed + d.phase) % 2;
      const lin = mod < 1 ? mod : 2 - mod; 
      const a = 0.25 + 0.55 * lin;

      if (a > 0.6) {
        const glow = (a - 0.6) / 0.4;
        this.ctx.shadowColor = this.resolvedGlowColor;
        this.ctx.shadowBlur = 6 * glow;
      } else {
        this.ctx.shadowColor = "transparent";
        this.ctx.shadowBlur = 0;
      }

      this.ctx.globalAlpha = a * this.options.opacity;
      this.ctx.beginPath();
      this.ctx.arc(d.x, d.y, this.options.radius, 0, Math.PI * 2);
      this.ctx.fill();
    }
    
    this.ctx.restore();
  }

  loop() {
    const render = (now) => {
      this.draw(now);
      if (!this.stopped) {
        this.raf = requestAnimationFrame(render);
      }
    };
    this.raf = requestAnimationFrame(render);
  }

  destroy() {
    this.stopped = true;
    if (this.raf) cancelAnimationFrame(this.raf);
    if (this.observer) this.observer.disconnect();
    if (this.resizeObserver) this.resizeObserver.disconnect();
    if (this.canvas && this.canvas.parentNode) {
      this.canvas.parentNode.removeChild(this.canvas);
    }
  }
}

// Export for usage in other scripts if using modules, or just global
window.DottedGlowBackground = DottedGlowBackground;
