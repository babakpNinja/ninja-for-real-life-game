/*
 * art.js — every pixel in this game is drawn here with canvas paths.
 * Nothing is traced from the show: these are original cartoon heeler-ish dogs
 * built from circles and rounded rects, tinted per character palette.
 */

export function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function ellipse(ctx, x, y, rx, ry, fill) {
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  ctx.fillStyle = fill;
  ctx.fill();
}

/* ---------------------------------------------------------------- dogs -- */

/**
 * Draw a cartoon heeler standing at (x, y) where y is the ground line.
 * `t` drives the run cycle, `state` tweaks pose: run | jump | float | idle | cheer.
 */
export function drawDog(ctx, x, y, size, pal, t, state = "run", facing = 1) {
  const s = size / 100; // art is authored at 100px tall
  const legSwing = state === "run" ? Math.sin(t * 12) : 0;
  const legSwing2 = state === "run" ? Math.sin(t * 12 + Math.PI) : 0;
  const bob = state === "run" ? Math.abs(Math.sin(t * 12)) * 3 : Math.sin(t * 3) * 1.5;
  const airborne = state === "jump" || state === "float";

  ctx.save();
  ctx.translate(x, y);
  ctx.scale(facing * s, s);
  ctx.translate(0, -bob);

  // soft contact shadow
  ctx.globalAlpha = airborne ? 0.12 : 0.22;
  ellipse(ctx, 0, 2, 30, 6, "#000000");
  ctx.globalAlpha = 1;

  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(30,40,60,0.35)";
  ctx.lineWidth = 2;

  const legY = -18;
  const legLen = 20;
  const legAngle = airborne ? (state === "float" ? 0.5 : -0.7) : 0;

  // back legs
  ctx.strokeStyle = pal.patch;
  ctx.lineWidth = 9;
  drawLeg(ctx, -14, legY, legLen, legSwing * 0.5 + legAngle);
  drawLeg(ctx, 12, legY, legLen, legSwing2 * 0.5 + legAngle);

  // tail — wags faster when cheering
  const wag = Math.sin(t * (state === "cheer" ? 18 : 8)) * 0.4;
  ctx.strokeStyle = pal.coat;
  ctx.lineWidth = 10;
  ctx.beginPath();
  ctx.moveTo(-20, -42);
  ctx.quadraticCurveTo(-34, -46 + wag * 10, -36, -60 + wag * 12);
  ctx.stroke();

  // body
  ctx.fillStyle = pal.coat;
  roundRect(ctx, -24, -58, 48, 42, 18);
  ctx.fill();

  // belly
  ctx.fillStyle = pal.belly;
  roundRect(ctx, -14, -38, 30, 22, 11);
  ctx.fill();

  // front legs
  ctx.strokeStyle = pal.coat;
  ctx.lineWidth = 9;
  drawLeg(ctx, -6, legY, legLen, legSwing2 + legAngle);
  drawLeg(ctx, 18, legY, legLen, legSwing + legAngle);

  // head group
  const headTilt = state === "cheer" ? -0.15 : airborne ? 0.08 : Math.sin(t * 6) * 0.03;
  ctx.save();
  ctx.translate(16, -62);
  ctx.rotate(headTilt);

  // ears
  ctx.fillStyle = pal.patch;
  ellipse(ctx, -12, -18, 7, 12, pal.patch);
  ellipse(ctx, 12, -18, 7, 12, pal.patch);

  // skull
  ctx.fillStyle = pal.coat;
  roundRect(ctx, -20, -20, 40, 36, 16);
  ctx.fill();

  // muzzle
  ctx.fillStyle = pal.belly;
  roundRect(ctx, -4, -2, 26, 20, 10);
  ctx.fill();

  // cheek patch (the heeler marking)
  ctx.globalAlpha = 0.55;
  ellipse(ctx, -9, 2, 9, 10, pal.patch);
  ctx.globalAlpha = 1;

  // eyes
  const blink = Math.sin(t * 1.7) > 0.985;
  ctx.fillStyle = "#FFFFFF";
  if (!blink) {
    ellipse(ctx, 2, -4, 5.5, 6.5, "#FFFFFF");
    ellipse(ctx, 15, -4, 5.5, 6.5, "#FFFFFF");
    ctx.fillStyle = "#22303F";
    ellipse(ctx, 3.5, -3, 3, 3.6, "#22303F");
    ellipse(ctx, 16.5, -3, 3, 3.6, "#22303F");
    ctx.fillStyle = "#FFFFFF";
    ellipse(ctx, 4.6, -4.6, 1.1, 1.3, "#FFFFFF");
    ellipse(ctx, 17.6, -4.6, 1.1, 1.3, "#FFFFFF");
  } else {
    ctx.strokeStyle = "#22303F";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(-2, -4); ctx.lineTo(7, -4);
    ctx.moveTo(11, -4); ctx.lineTo(20, -4);
    ctx.stroke();
  }

  // nose + smile
  ctx.fillStyle = "#22303F";
  ellipse(ctx, 20, 4, 4.5, 3.6, "#22303F");
  ctx.strokeStyle = "#22303F";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(20, 7);
  ctx.quadraticCurveTo(14, 14, 8, 9);
  ctx.stroke();

  // tongue when cheering or floating
  if (state === "cheer" || state === "float") {
    ctx.fillStyle = "#F27C9A";
    roundRect(ctx, 10, 9, 8, 7, 3.5);
    ctx.fill();
  }
  ctx.restore();
  ctx.restore();
}

function drawLeg(ctx, x, y, len, angle) {
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x + Math.sin(angle) * len, y + Math.cos(angle) * len);
  ctx.stroke();
}

/* ------------------------------------------------------------ scenery -- */

export function drawTree(ctx, x, groundY, scale, leaf, trunk) {
  ctx.save();
  ctx.translate(x, groundY);
  ctx.scale(scale, scale);
  ctx.fillStyle = trunk;
  roundRect(ctx, -8, -70, 16, 72, 6);
  ctx.fill();
  ctx.fillStyle = leaf;
  ellipse(ctx, 0, -86, 44, 34, leaf);
  ellipse(ctx, -28, -70, 26, 20, leaf);
  ellipse(ctx, 28, -70, 26, 20, leaf);
  ctx.restore();
}

export function drawGumTree(ctx, x, groundY, scale) {
  ctx.save();
  ctx.translate(x, groundY);
  ctx.scale(scale, scale);
  ctx.strokeStyle = "#D9CFC0";
  ctx.lineWidth = 12;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(-4, -90);
  ctx.moveTo(-3, -60);
  ctx.lineTo(-30, -84);
  ctx.moveTo(-3, -70);
  ctx.lineTo(26, -96);
  ctx.stroke();
  ctx.fillStyle = "#6FA86B";
  ellipse(ctx, -34, -92, 26, 18, "#6FA86B");
  ellipse(ctx, 30, -104, 28, 20, "#6FA86B");
  ellipse(ctx, -6, -112, 34, 24, "#7FB876");
  ctx.restore();
}

export function drawHouse(ctx, x, groundY, scale) {
  ctx.save();
  ctx.translate(x, groundY);
  ctx.scale(scale, scale);
  ctx.fillStyle = "#F3E2C7";
  roundRect(ctx, -70, -90, 140, 90, 6);
  ctx.fill();
  ctx.fillStyle = "#C1543E";
  ctx.beginPath();
  ctx.moveTo(-84, -88);
  ctx.lineTo(0, -136);
  ctx.lineTo(84, -88);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#8FD0E8";
  roundRect(ctx, -48, -70, 34, 30, 4); ctx.fill();
  roundRect(ctx, 16, -70, 34, 30, 4); ctx.fill();
  ctx.fillStyle = "#7A5A43";
  roundRect(ctx, -16, -46, 32, 46, 4); ctx.fill();
  ctx.fillStyle = "#FFD166";
  ellipse(ctx, 8, -24, 3, 3, "#FFD166");
  ctx.restore();
}

export function drawCloud(ctx, x, y, scale, alpha = 0.9) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.fillStyle = "#FFFFFF";
  ellipse(ctx, 0, 0, 34, 22, "#FFFFFF");
  ellipse(ctx, -30, 6, 22, 15, "#FFFFFF");
  ellipse(ctx, 30, 6, 24, 16, "#FFFFFF");
  ctx.restore();
}

export function drawBalloon(ctx, x, y, r, color) {
  ctx.save();
  ctx.translate(x, y);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.ellipse(0, 0, r * 0.86, r, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.45)";
  ellipse(ctx, -r * 0.3, -r * 0.35, r * 0.22, r * 0.3, "rgba(255,255,255,0.45)");
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(-4, r * 0.95);
  ctx.lineTo(4, r * 0.95);
  ctx.lineTo(0, r * 1.2);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.6)";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(0, r * 1.2);
  ctx.quadraticCurveTo(6, r * 1.6, 0, r * 2);
  ctx.stroke();
  ctx.restore();
}

/** Collectible icon — one shape per chapter so each level feels distinct. */
export function drawToken(ctx, x, y, r, kind, t) {
  ctx.save();
  ctx.translate(x, y + Math.sin(t * 4 + x * 0.01) * 3);
  ctx.rotate(Math.sin(t * 2 + x * 0.02) * 0.15);
  const glow = ctx.createRadialGradient(0, 0, 1, 0, 0, r * 2.1);
  glow.addColorStop(0, "rgba(255,255,255,0.55)");
  glow.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = glow;
  ellipse(ctx, 0, 0, r * 2.1, r * 2.1, glow);

  if (kind === "balloon") {
    drawBalloon(ctx, 0, 0, r, "#F25F5C");
  } else if (kind === "sticker") {
    ctx.fillStyle = "#FFD166";
    star(ctx, 0, 0, r, r * 0.48, 5);
    ctx.fill();
    ctx.fillStyle = "#F49AC1";
    ellipse(ctx, 0, 0, r * 0.3, r * 0.3, "#F49AC1");
  } else if (kind === "light") {
    ctx.fillStyle = "#6EC5B8";
    ellipse(ctx, 0, 0, r * 0.8, r, "#6EC5B8");
    ctx.fillStyle = "#FFF7D6";
    ellipse(ctx, 0, -r * 0.2, r * 0.35, r * 0.4, "#FFF7D6");
  } else if (kind === "shell") {
    ctx.fillStyle = "#F7C8A0";
    ctx.beginPath();
    ctx.arc(0, r * 0.4, r, Math.PI, 0);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = "#D89B72";
    ctx.lineWidth = 1.5;
    for (let i = -2; i <= 2; i++) {
      ctx.beginPath();
      ctx.moveTo(0, r * 0.4);
      ctx.lineTo(i * r * 0.42, r * 0.4 - r);
      ctx.stroke();
    }
  } else {
    // dream star
    ctx.fillStyle = "#FFF3B0";
    star(ctx, 0, 0, r * 1.1, r * 0.45, 5);
    ctx.fill();
  }
  ctx.restore();
}

export function star(ctx, cx, cy, outer, inner, points) {
  ctx.beginPath();
  for (let i = 0; i < points * 2; i++) {
    const rad = i % 2 === 0 ? outer : inner;
    const a = (Math.PI / points) * i - Math.PI / 2;
    ctx[i === 0 ? "moveTo" : "lineTo"](cx + Math.cos(a) * rad, cy + Math.sin(a) * rad);
  }
  ctx.closePath();
}

/** Obstacles are soft and bouncy-looking — nothing scary for a 3-year-old. */
export function drawObstacle(ctx, o, t) {
  const { x, y, w, h, kind } = o;
  ctx.save();
  ctx.translate(x, y);
  if (kind === "bush") {
    ctx.fillStyle = "#5FA05A";
    ellipse(ctx, w / 2, h / 2, w * 0.55, h * 0.55, "#5FA05A");
    ctx.fillStyle = "#6FB868";
    ellipse(ctx, w * 0.3, h * 0.4, w * 0.3, h * 0.35, "#6FB868");
  } else if (kind === "rock") {
    ctx.fillStyle = "#A9A29A";
    roundRect(ctx, 0, 0, w, h, 10);
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.25)";
    ellipse(ctx, w * 0.35, h * 0.3, w * 0.2, h * 0.16, "rgba(255,255,255,0.25)");
  } else if (kind === "box") {
    ctx.fillStyle = "#D9A066";
    roundRect(ctx, 0, 0, w, h, 6);
    ctx.fill();
    ctx.strokeStyle = "#B37F4A";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(0, h * 0.4); ctx.lineTo(w, h * 0.4);
    ctx.stroke();
  } else if (kind === "plant") {
    ctx.fillStyle = "#C1543E";
    roundRect(ctx, w * 0.2, h * 0.55, w * 0.6, h * 0.45, 5);
    ctx.fill();
    ctx.fillStyle = "#5FA05A";
    ellipse(ctx, w * 0.5, h * 0.4, w * 0.5, h * 0.35, "#5FA05A");
  } else if (kind === "sandcastle") {
    ctx.fillStyle = "#E8C88F";
    roundRect(ctx, 0, h * 0.3, w, h * 0.7, 4); ctx.fill();
    roundRect(ctx, w * 0.15, 0, w * 0.25, h * 0.4, 3); ctx.fill();
    roundRect(ctx, w * 0.6, 0, w * 0.25, h * 0.4, 3); ctx.fill();
  } else {
    // dream cloud — you bounce off it gently
    ctx.globalAlpha = 0.85;
    drawCloud(ctx, w / 2, h / 2, Math.min(w, h) / 44, 0.85);
  }
  ctx.restore();
}
