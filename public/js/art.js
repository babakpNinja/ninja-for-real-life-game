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

/**
 * A palm on the beach's shoreline (#229): a leaning trunk and drooping fronds.
 *
 * Chapter 4 spent its whole life dressed in `drawTree` with a green tint — a
 * round oak canopy on a straight trunk, standing on a tropical shoreline, which
 * is the creek's prop with the colour changed. Eight comments in two files had
 * already called these palms; this is the drawing catching up with them.
 *
 * No canopy blob on purpose. A palm reads as a palm because of the *gaps*
 * between its fronds — fill the middle in and it is an oak again at any size
 * this is drawn at.
 *
 * The trunk leans, but its foot stays at local x=0: the placement test measures
 * a prop down its own centre column, so a base that wandered left with the lean
 * would be measured on the sky beside it (#223). The foot is also 2px below the
 * ground line, as the other props are — a shape that stops exactly on its own
 * baseline paints its last row in antialiasing too faint to count as paint.
 */
export function drawPalm(ctx, x, groundY, scale) {
  ctx.save();
  ctx.translate(x, groundY);
  ctx.scale(scale, scale);

  const crownX = -15, crownY = -98;
  ctx.fillStyle = "#A5764F";               // the trunk: wide at the foot, bent
  ctx.beginPath();                         // toward the crown, narrow at the top
  ctx.moveTo(-7, 2);
  ctx.quadraticCurveTo(-6, -52, crownX - 5, crownY + 4);
  ctx.lineTo(crownX + 6, crownY + 4);
  ctx.quadraticCurveTo(5, -52, 7, 2);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = "#8E6242";             // the ring scars, following the bend
  ctx.lineWidth = 1.6;
  for (let i = 0; i < 5; i++) {
    const f = 0.18 + i * 0.17, y = 2 + (crownY - 2) * f, w = 6.6 - f * 2.2;
    ctx.beginPath();
    ctx.moveTo(-w + crownX * f * f, y);
    ctx.lineTo(w + crownX * f * f, y);
    ctx.stroke();
  }

  ctx.translate(crownX, crownY);
  ellipse(ctx, -4, 4, 5, 5, "#7A5B3F");    // two coconuts, tucked under the crown
  ellipse(ctx, 6, 6, 4.5, 4.5, "#8B6A4F");

  /* Five fronds, each an arched blade: the width comes off the *perpendicular*
   * to the blade's own axis, so a frond pointing straight up is as thick as one
   * pointing sideways. Bowing the control points vertically instead — the
   * obvious way — collapses the upright ones to a hairline, which is what the
   * first draft of this drew.
   *
   * Outer two darker, inner three lighter, so the crown has a far side and a
   * near side rather than reading as one flat star. */
  const frond = (tipX, tipY, w, arch, fill) => {
    const len = Math.hypot(tipX, tipY);
    const nx = -tipY / len, ny = tipX / len;          // unit perpendicular
    const mx = tipX * 0.5, my = tipY * 0.5 - arch;    // arched, so the tip droops
    ctx.fillStyle = fill;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.quadraticCurveTo(mx + nx * w, my + ny * w, tipX, tipY);
    ctx.quadraticCurveTo(mx - nx * w, my - ny * w, 0, 4);
    ctx.closePath();
    ctx.fill();
  };
  frond(-58, 8, 10, 22, "#4E9E6C");
  frond(60, 12, 10, 22, "#4E9E6C");
  frond(-42, -28, 9, 15, "#67B47F");
  frond(46, -24, 9, 15, "#67B47F");
  frond(2, -44, 8, 6, "#7CC492");
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

/*
 * The middle distance of chapter 3 (#213). The far layer is a row of warehouse
 * shelving; between it and the player there was nothing at all, so the aisle
 * had no depth. These three stand on the shop floor at the chapter's horizon.
 *
 * All three are drawn upward from (x, groundY) and paint nothing below it —
 * the foot *is* the ground line, which is what lets the placement test measure
 * them against the surface the background paints.
 */

export function drawPallets(ctx, x, groundY, scale) {
  ctx.save();
  ctx.translate(x, groundY);
  ctx.scale(scale, scale);
  // three timber pallets, the top one carrying bags of potting mix
  for (let i = 0; i < 3; i++) {
    const y = -13 - i * 15;   // the bottom one sits *on* the floor, not 3px over it
    ctx.fillStyle = i % 2 ? "#B0854F" : "#C89A63";
    roundRect(ctx, -46, y, 92, 13, 2);
    ctx.fill();
    ctx.fillStyle = "rgba(0,0,0,0.14)";
    for (let s = 0; s < 4; s++) ctx.fillRect(-40 + s * 24, y + 3, 5, 8);
  }
  ctx.fillStyle = "#7E9160";
  roundRect(ctx, -40, -78, 44, 20, 6); ctx.fill();
  roundRect(ctx, 2, -74, 40, 16, 6); ctx.fill();
  ctx.fillStyle = "#6C7F51";
  roundRect(ctx, -26, -94, 44, 20, 6); ctx.fill();
  ctx.restore();
}

export function drawTrolleys(ctx, x, groundY, scale) {
  ctx.save();
  ctx.translate(x, groundY);
  ctx.scale(scale, scale);
  // a bay of nested trolleys, back to front so the near one sits on top
  for (let i = 4; i >= 0; i--) {
    const dx = -34 + i * 15;
    ctx.strokeStyle = i ? "#B6BEC6" : "#9AA6B0";
    ctx.lineWidth = 3;
    ctx.beginPath();                       // the basket, tipped back
    ctx.moveTo(dx - 20, -74);
    ctx.lineTo(dx + 22, -74);
    ctx.lineTo(dx + 16, -34);
    ctx.lineTo(dx - 12, -34);
    ctx.closePath();
    ctx.stroke();
    ctx.beginPath();                       // the mesh, two bars of it
    ctx.moveTo(dx - 17, -61); ctx.lineTo(dx + 20, -61);
    ctx.moveTo(dx - 14, -48); ctx.lineTo(dx + 18, -48);
    ctx.stroke();
    ctx.beginPath();                       // the frame down to the wheels
    ctx.moveTo(dx - 11, -34); ctx.lineTo(dx - 8, -8);
    ctx.moveTo(dx + 15, -34); ctx.lineTo(dx + 12, -8);
    ctx.stroke();
    ctx.fillStyle = "#5C6670";
    ellipse(ctx, dx - 8, -5, 5, 5, "#5C6670");
    ellipse(ctx, dx + 12, -5, 5, 5, "#5C6670");
    ctx.strokeStyle = "#C1543E";           // the handle
    ctx.beginPath();
    ctx.moveTo(dx - 22, -78); ctx.lineTo(dx - 6, -78);
    ctx.stroke();
  }
  ctx.restore();
}

export function drawStepLadder(ctx, x, groundY, scale) {
  ctx.save();
  ctx.translate(x, groundY);
  ctx.scale(scale, scale);
  ctx.strokeStyle = "#E08A3C";
  ctx.lineWidth = 6;
  ctx.lineCap = "round";
  ctx.beginPath();                         // the A of it
  ctx.moveTo(-26, -2); ctx.lineTo(-7, -108);
  ctx.moveTo(26, -2); ctx.lineTo(9, -108);
  ctx.stroke();
  ctx.strokeStyle = "#C9762F";
  ctx.lineWidth = 5;
  ctx.beginPath();                         // the spreader bar, down on the floor
  ctx.moveTo(-24, -2); ctx.lineTo(24, -2);
  ctx.stroke();
  ctx.lineWidth = 4;
  for (let i = 0; i < 4; i++) {            // the steps, narrowing upward
    const y = -26 - i * 22, w = 21 - i * 3.4;
    ctx.beginPath();
    ctx.moveTo(-w, y); ctx.lineTo(w, y);
    ctx.stroke();
  }
  ctx.fillStyle = "#D9D2C8";               // the paint shelf on top
  roundRect(ctx, -14, -118, 28, 9, 3);
  ctx.fill();
  ctx.restore();
}

/**
 * A ringed planet resting in the cloud sea (#228), where `baseY` is the surface.
 *
 * The body sits 4px into the cloud rather than tangent to it: a sphere that only
 * touches its own foot paints its last row in antialiasing too faint to be a
 * pixel, and "is this prop standing on the surface" is measured off the lowest
 * row it really changes. Four px of it under the cloud is also what a thing
 * settled into cloud looks like.
 *
 * The ring is kept above that line for the same reason in reverse — a ring
 * hanging below the body would put the planet's lowest paint somewhere the
 * planet is not standing.
 */
export function drawDreamPlanet(ctx, x, baseY, scale) {
  const r = 40 * scale;
  ctx.save();
  ctx.translate(x, baseY - r + 4);
  ctx.globalAlpha = 0.85;
  ellipse(ctx, 0, 0, r, r, "#C79AD8");
  ellipse(ctx, -r * 0.28, -r * 0.3, r * 0.52, r * 0.44, "#DDB6E9");  // the lit side
  ctx.save();                              // two soft bands, clipped to the body
  ctx.beginPath();
  ctx.ellipse(0, 0, r, r, 0, 0, Math.PI * 2);
  ctx.clip();
  ellipse(ctx, 0, r * 0.34, r * 1.2, r * 0.13, "#A87CBE");
  ellipse(ctx, 0, -r * 0.62, r * 1.2, r * 0.1, "#A87CBE");
  ctx.restore();
  ctx.strokeStyle = "#EBD3F2";             // the ring, tilted and behind at the far edge
  ctx.lineWidth = 5 * scale;
  ctx.globalAlpha = 0.6;
  ctx.beginPath();
  ctx.ellipse(0, -r * 0.18, r * 1.6, r * 0.42, -0.22, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

/**
 * A tower of cloud standing in the cloud sea (#228): three puffs, widest down.
 *
 * Lilac where the sky's clouds are white, so the layer reads as a distance
 * rather than as more sky — the same job the hills of chapters 1-2 do, done by
 * the only kind of thing a dream has lying about.
 */
export function drawCloudTower(ctx, x, baseY, scale) {
  ctx.save();
  ctx.translate(x, baseY + 2);             // sat into the cloud, as the planets are
  ctx.scale(scale, scale);
  // Dim and lilac at the foot, moonlit at the top. The sky's own clouds are
  // white at 0.5: a tower painted that colour reads as one of them that happens
  // to be low down, and the middle distance stops being a distance.
  ctx.globalAlpha = 0.8;
  ellipse(ctx, 0, -28, 52, 30, "#9F97D2");   // bottoms just under the surface at
  ellipse(ctx, -34, -20, 30, 20, "#9F97D2"); // every scale this is drawn at, so
  ellipse(ctx, 36, -22, 32, 21, "#9F97D2");  // the lowest paint is its own foot
  ctx.globalAlpha = 0.75;
  ellipse(ctx, 6, -74, 38, 26, "#B4ACE2");
  ellipse(ctx, -20, -66, 24, 17, "#B4ACE2");
  ctx.globalAlpha = 0.7;
  ellipse(ctx, -2, -112, 26, 19, "#CFC8F2");
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

/** A bird, at the distance where a bird is two strokes (#326). */
export function drawBird(ctx, x, y, scale, alpha = 0.5) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.strokeStyle = "#5B6B7A";
  ctx.lineWidth = 2.2;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(-14, 4);
  ctx.quadraticCurveTo(-7, -5, 0, 1);
  ctx.quadraticCurveTo(7, -5, 14, 4);
  ctx.stroke();
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
