/* ============================================================
   背景流动线条效果 — 鼠标吸附版
   ------------------------------------------------------------
   - 全屏 Canvas，z-index:-1，pointer-events:none
   - 粒子缓慢流动，相邻粒子连线（很浅）
   - 鼠标慢速/静止：附近粒子被吸附，形成凝聚团
   - 鼠标快速移动：粒子释放，恢复自由流动
   - 适配夜间模式
   ============================================================ */
(function () {
  if (location.pathname.includes('/admin/')) return;
  if (document.getElementById('bg-flow')) return;

  const canvas = document.createElement('canvas');
  canvas.id = 'bg-flow';
  canvas.setAttribute('aria-hidden', 'true');
  canvas.style.cssText =
    'position:fixed;top:0;left:0;width:100%;height:100%;z-index:-1;pointer-events:none;';
  document.body.appendChild(canvas);

  const ctx = canvas.getContext('2d');
  let W = 0, H = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);
  let particles = [];

  // 鼠标状态
  const mouse = { x: -9999, y: -9999, prevX: -9999, prevY: -9999, speed: 0, active: false };

  const CFG = {
    count: 0,
    linkDist: 130,       // 粒子间连线最大距离
    baseAlpha: 0.07,     // 基础线条透明度
    dotAlpha: 0.15,      // 粒子点透明度
    attractDist: 140,    // 吸附半径
    attractForce: 0.015, // 吸附力系数
    speed: 0.3,          // 自由流动基础速度
    maxSpeed: 0.9,       // 吸附后最大速度（需能跟上锚点绕动）
  };

  function lineColor() {
    return document.body.classList.contains('dark')
      ? '239, 235, 227'
      : '26, 25, 21';
  }
  let color = lineColor();

  function resize() {
    W = window.innerWidth;
    H = window.innerHeight;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    CFG.count = Math.max(50, Math.min(110, Math.round(W * H / 16000)));
    initParticles();
  }

  function initParticles() {
    particles = [];
    for (let i = 0; i < CFG.count; i++) {
      particles.push({
        x: Math.random() * W,
        y: Math.random() * H,
        vx: (Math.random() - 0.5) * CFG.speed,
        vy: (Math.random() - 0.5) * CFG.speed,
        r: Math.random() * 1.2 + 0.6,
        // 吸附状态
        attached: false,
      });
    }
  }

  function step() {
    ctx.clearRect(0, 0, W, H);

    // 鼠标速度阈值：慢于 3px/frame 触发吸附，快于 8px/frame 完全释放
    const slow = mouse.speed < 3 && mouse.active;
    const fast = mouse.speed > 8;

    // 更新粒子
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      // 粒子到鼠标的距离：只用于判断是否进入吸附范围
      const mdx = p.x - mouse.x;
      const mdy = p.y - mouse.y;
      const distToMouse = Math.sqrt(mdx * mdx + mdy * mdy);

      if (slow && distToMouse < CFG.attractDist) {
        // 进入吸附时，给粒子分配一个鼠标周围的轨道锚点（半径 14~62px、任意角度）
        // 锚点会绕鼠标持续转动 → 粒子被拽着沿轨道游走，保持多边形团状且一直运动
        if (!p.attached) {
          p.anchorR = 14 + Math.random() * 48;
          p.anchorA = Math.random() * Math.PI * 2;
          p.orbitDir = Math.random() < 0.5 ? 1 : -1;          // 旋转方向
          p.orbitSpeed = 0.004 + Math.random() * 0.006;        // 角速度(rad/frame)，慢速游走
        }
        // 锚点角度随时间推进（绕鼠标公转）
        p.anchorA += p.orbitDir * p.orbitSpeed;
        // 目标点 = 鼠标位置 + 轨道锚点偏移（团状跟随鼠标并持续运动）
        const tx = mouse.x + Math.cos(p.anchorA) * p.anchorR;
        const ty = mouse.y + Math.sin(p.anchorA) * p.anchorR;
        const adx = p.x - tx;
        const ady = p.y - ty;
        const adist = Math.sqrt(adx * adx + ady * ady);
        if (adist > 3) {
          // 吸附模式：粒子被拽向自己的锚点（而非鼠标点）
          const force = (1 - adist / CFG.attractDist) * CFG.attractForce * (1 - mouse.speed / 8);
          p.vx -= (adx / adist) * force * 2 + (Math.random() - 0.5) * 0.02;
          p.vy -= (ady / adist) * force * 2 + (Math.random() - 0.5) * 0.02;
        }
        // 阻尼
        p.vx *= 0.98;
        p.vy *= 0.98;
        // 限速
        const spd = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
        if (spd > CFG.maxSpeed) {
          p.vx = (p.vx / spd) * CFG.maxSpeed;
          p.vy = (p.vy / spd) * CFG.maxSpeed;
        }
        p.attached = true;
      } else if (fast && p.attached) {
        // 快速移动：释放，给随机初速度
        p.vx += (Math.random() - 0.5) * 0.15;
        p.vy += (Math.random() - 0.5) * 0.15;
        p.attached = false;
      } else if (!slow && !p.attached) {
        // 自由模式：恢复自然漂移
        p.vx += (Math.random() - 0.5) * 0.004;
        p.vy += (Math.random() - 0.5) * 0.004;
        const spd = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
        const target = CFG.speed;
        if (spd > target + 0.15) {
          p.vx *= 0.995;
          p.vy *= 0.995;
        } else if (spd < target - 0.1) {
          p.vx *= 1.002;
          p.vy *= 1.002;
        }
      }

      p.x += p.vx;
      p.y += p.vy;

      if (p.x < -10) p.x = W + 10;
      else if (p.x > W + 10) p.x = -10;
      if (p.y < -10) p.y = H + 10;
      else if (p.y > H + 10) p.y = -10;
    }

    // 画粒子间连线（始终很浅，不因鼠标变化）
    for (let i = 0; i < particles.length; i++) {
      const a = particles[i];
      for (let j = i + 1; j < particles.length; j++) {
        const b = particles[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist >= CFG.linkDist) continue;
        const t = 1 - dist / CFG.linkDist;
        const alpha = CFG.baseAlpha * t;
        ctx.strokeStyle = `rgba(${color}, ${alpha.toFixed(3)})`;
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    // 画粒子点
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      ctx.fillStyle = `rgba(${color}, ${CFG.dotAlpha.toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }

    requestAnimationFrame(step);
  }

  // 鼠标事件 — 计算速度
  window.addEventListener('mousemove', (e) => {
    mouse.prevX = mouse.x;
    mouse.prevY = mouse.y;
    mouse.x = e.clientX;
    mouse.y = e.clientY;
    mouse.speed = Math.hypot(mouse.x - mouse.prevX, mouse.y - mouse.prevY);
    mouse.active = true;
  });
  window.addEventListener('mouseout', () => { mouse.active = false; mouse.speed = 99; });
  window.addEventListener('blur', () => { mouse.active = false; mouse.speed = 99; });

  window.addEventListener('touchmove', (e) => {
    if (e.touches.length) {
      mouse.prevX = mouse.x;
      mouse.prevY = mouse.y;
      mouse.x = e.touches[0].clientX;
      mouse.y = e.touches[0].clientY;
      mouse.speed = Math.hypot(mouse.x - mouse.prevX, mouse.y - mouse.prevY);
      mouse.active = true;
    }
  }, { passive: true });
  window.addEventListener('touchend', () => { mouse.active = false; mouse.speed = 99; });

  const themeObs = new MutationObserver(() => { color = lineColor(); });
  themeObs.observe(document.body, { attributes: true, attributeFilter: ['class'] });

  let rt;
  window.addEventListener('resize', () => {
    clearTimeout(rt);
    rt = setTimeout(resize, 200);
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { mouse.active = false; mouse.speed = 99; }
  });

  resize();
  step();
})();
